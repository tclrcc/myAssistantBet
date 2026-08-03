"""Assemblage du prompt final a partir des blocs de rendu et d'un template Jinja.

Le livrable de l'application est ce bloc de texte. L'app ne l'envoie nulle part :
elle le rend, l'humain le copie.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from jinja2 import (
    Environment,
    FileSystemLoader,
    TemplateNotFound,
    TemplateSyntaxError,
    select_autoescape,
)

from ..config import PACKAGE_DIR, Settings, get_settings
from ..db import connect, utcnow
from .render import estimate_tokens
from .session import render_blocks, session_label

logger = logging.getLogger(__name__)

TEMPLATES_DIR = PACKAGE_DIR / "templates" / "prompts"
DEFAULT_TEMPLATE = "session_default.md.j2"
TEMPLATE_SUFFIX = ".md.j2"

MOIS_FR = (
    "janvier",
    "fevrier",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "aout",
    "septembre",
    "octobre",
    "novembre",
    "decembre",
)


@dataclass
class Tier:
    """Une bande de cote, exposee au template."""

    key: str
    label: str
    emoji: str
    min_price: float
    max_price: float | None
    quota_min: int
    quota_max: int

    @property
    def range_label(self) -> str:
        if self.max_price is None:
            return f"> {self.min_price:.2f}   (scores exacts multichoix, marches exotiques)"
        return f"{self.min_price:.2f} – {self.max_price:.2f}"

    @property
    def quota_label(self) -> str:
        return f"{self.quota_min}-{self.quota_max} {self.emoji}"


@dataclass
class RenderedPrompt:
    """Le prompt genere, avant sauvegarde."""

    template_name: str
    body: str
    blocks: int

    @property
    def token_estimate(self) -> int:
        return estimate_tokens(self.body)


def _environment() -> Environment:
    return Environment(
        loader=FileSystemLoader(TEMPLATES_DIR),
        autoescape=select_autoescape(default=False, default_for_string=False),
        keep_trailing_newline=True,
        trim_blocks=False,
    )


def list_templates() -> list[str]:
    """Templates disponibles, lus sur le disque a chaque appel.

    Editer un fichier suffit donc a changer le prompt, sans redeploiement.
    """
    if not TEMPLATES_DIR.is_dir():
        return []
    names = sorted(path.name for path in TEMPLATES_DIR.glob(f"*{TEMPLATE_SUFFIX}"))
    if DEFAULT_TEMPLATE in names:
        names.remove(DEFAULT_TEMPLATE)
        names.insert(0, DEFAULT_TEMPLATE)
    return names


def load_tiers(settings: Settings | None = None) -> list[Tier]:
    """Bandes de cotes, lues en base pour rester modifiables sans redeploiement."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT key, label, emoji, min_price, max_price, quota_min, quota_max "
            "FROM tiers ORDER BY position"
        ).fetchall()
    return [
        Tier(
            key=row["key"],
            label=row["label"],
            emoji=row["emoji"],
            min_price=float(row["min_price"]),
            max_price=None if row["max_price"] is None else float(row["max_price"]),
            quota_min=int(row["quota_min"]),
            quota_max=int(row["quota_max"]),
        )
        for row in rows
    ]


def date_fr(moment: datetime) -> str:
    return f"{moment.day} {MOIS_FR[moment.month - 1]} {moment.year}"


def build_prompt(
    session_id: int,
    template_name: str = DEFAULT_TEMPLATE,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> RenderedPrompt:
    """Rend le prompt d'une session. Ne touche ni au reseau ni a la base d'ecriture."""
    settings = settings or get_settings()
    if template_name not in list_templates():
        raise TemplateNotFound(template_name)

    blocks = render_blocks(session_id, settings)
    moment = (now or datetime.now(ZoneInfo(settings.tz))).astimezone(ZoneInfo(settings.tz))

    body = (
        _environment()
        .get_template(template_name)
        .render(
            date_fr=date_fr(moment),
            event_blocks=blocks,
            session_label=session_label(session_id, settings),
            tiers=load_tiers(settings),
        )
    )
    return RenderedPrompt(template_name=template_name, body=body, blocks=len(blocks))


def save_prompt(session_id: int, prompt: RenderedPrompt, settings: Settings | None = None) -> int:
    """Archive le prompt genere. Renvoie son id."""
    with connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                session_id,
                prompt.template_name,
                prompt.body,
                prompt.token_estimate,
                utcnow(),
            ),
        )
        prompt_id = int(cursor.lastrowid)
    logger.info(
        "Prompt genere pour la session %d : %d blocs, ~%d tokens",
        session_id,
        prompt.blocks,
        prompt.token_estimate,
    )
    return prompt_id


def template_path(name: str) -> Path:
    return TEMPLATES_DIR / name


# -- Personnalisation (phase 5) ---------------------------------------------

#: Un nom de template est un simple slug : aucune traversee de repertoire possible.
TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md\.j2$")


class CustomizationError(ValueError):
    """Saisie invalide. Le message est affiche tel quel a l'utilisateur."""


def read_template(name: str) -> str:
    """Contenu d'un template, pour l'editeur."""
    if name not in list_templates():
        raise CustomizationError(f"Template inconnu : {name}")
    return template_path(name).read_text(encoding="utf-8")


def save_template(name: str, body: str) -> str:
    """Ecrit un template apres l'avoir compile.

    Un template qui ne compile pas casserait toute generation de prompt : on
    refuse d'ecrire plutot que de laisser l'application dans cet etat.
    """
    name = (name or "").strip()
    if not TEMPLATE_NAME.match(name):
        raise CustomizationError(
            "Nom invalide : minuscules, chiffres, tirets et underscores, "
            "et l'extension .md.j2 (exemple : session_court.md.j2)."
        )
    if not body.strip():
        raise CustomizationError("Le template est vide.")

    try:
        _environment().from_string(body)
    except TemplateSyntaxError as exc:
        raise CustomizationError(
            f"Erreur de syntaxe Jinja ligne {exc.lineno} : {exc.message}"
        ) from exc

    path = template_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")
    logger.info("Template enregistre : %s (%d caracteres)", name, len(body))
    return name


def delete_template(name: str) -> None:
    """Supprime un template. Le template par defaut n'est jamais supprimable."""
    if name == DEFAULT_TEMPLATE:
        raise CustomizationError("Le template par défaut ne peut pas être supprimé.")
    if name not in list_templates():
        raise CustomizationError(f"Template inconnu : {name}")
    template_path(name).unlink()
    logger.info("Template supprime : %s", name)


def save_tiers(rows: list[dict[str, Any]], settings: Settings | None = None) -> None:
    """Met a jour les bandes de cotes. Les bornes doivent rester coherentes."""
    for row in rows:
        minimum = row.get("min_price")
        maximum = row.get("max_price")
        if minimum is None:
            raise CustomizationError(f"Palier {row.get('key')} : borne basse manquante.")
        if maximum is not None and maximum <= minimum:
            raise CustomizationError(
                f"Palier {row.get('key')} : la borne haute doit dépasser la borne basse."
            )
        quota_min, quota_max = row.get("quota_min"), row.get("quota_max")
        if quota_min is not None and quota_max is not None and quota_max < quota_min:
            raise CustomizationError(
                f"Palier {row.get('key')} : le quota maximum est inférieur au minimum."
            )

    with connect(settings) as conn:
        for row in rows:
            conn.execute(
                "UPDATE tiers SET label = ?, emoji = ?, min_price = ?, max_price = ?, "
                "                 quota_min = ?, quota_max = ? WHERE key = ?",
                (
                    row["label"],
                    row["emoji"],
                    row["min_price"],
                    row["max_price"],
                    row["quota_min"],
                    row["quota_max"],
                    row["key"],
                ),
            )
    logger.info("Bandes de cotes mises a jour : %d paliers", len(rows))
