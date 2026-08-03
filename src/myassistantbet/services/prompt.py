"""Assemblage du prompt final a partir des blocs de rendu et d'un template Jinja.

Le livrable de l'application est ce bloc de texte. L'app ne l'envoie nulle part :
elle le rend, l'humain le copie.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from jinja2 import Environment, FileSystemLoader, TemplateNotFound, select_autoescape

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
