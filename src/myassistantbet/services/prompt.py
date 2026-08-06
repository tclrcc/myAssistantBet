"""Assemblage du prompt final a partir des blocs de rendu et d'un template Jinja.

Le livrable de l'application est ce bloc de texte. L'app ne l'envoie nulle part :
elle le rend, l'humain le copie.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
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
from ..providers.oddsapi import SCAN_MARKETS
from .enrich import markets_for
from .history import feedback
from .render import estimate_tokens, ordered_labels, render_event
from .session import has_started, renderable_events, session_label, started_labels

logger = logging.getLogger(__name__)

TEMPLATES_DIR = PACKAGE_DIR / "templates" / "prompts"
DEFAULT_TEMPLATE = "session_default.md.j2"
TEMPLATE_SUFFIX = ".md.j2"

MOIS_FR = (
    "janvier",
    "février",
    "mars",
    "avril",
    "mai",
    "juin",
    "juillet",
    "août",
    "septembre",
    "octobre",
    "novembre",
    "décembre",
)

#: Le jour de la semaine porte du sens : calendrier, session de nuit, reprise
#: apres week-end. Le laisser deduire de la date etait une devinette inutile.
JOURS_FR = (
    "lundi",
    "mardi",
    "mercredi",
    "jeudi",
    "vendredi",
    "samedi",
    "dimanche",
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
class Catalogue:
    """Ce que l'app sait demander a l'API pour un sport du lot.

    Sans cette annonce, un marche absent partout est indiscernable d'un marche
    que personne n'a jamais demande : la section F reclame alors des marches que
    le fournisseur ne sert pas — scores exacts ou tie-break en tennis — et la
    boucle de retour tourne a vide.
    """

    sport: str
    markets: list[str] = field(default_factory=list)

    @property
    def line(self) -> str:
        if not self.markets:
            return f"{self.sport} : aucun marché d'API, saisie manuelle uniquement."
        return f"{self.sport} : {', '.join(self.markets)}."


@dataclass
class CompetitionNote:
    """Fiche d'une competition presente dans le lot.

    Rendue une seule fois, et non a chaque match : le format d'une coupe, la
    phase en cours ou une reprise de championnat ne changent pas d'une affiche
    a l'autre. Les repeter couterait des tokens sans rien apprendre de plus.
    """

    label: str
    notes: str

    @property
    def line(self) -> str:
        return f"{self.label} : {self.notes}"


@dataclass
class RenderedPrompt:
    """Le prompt genere, avant sauvegarde."""

    template_name: str
    body: str
    blocks: int
    #: Matchs de la session absents du prompt parce qu'ils ont commence. Une
    #: selection amputee en silence se remarquerait trop tard.
    started: list[str] = field(default_factory=list)

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
    jour = JOURS_FR[moment.weekday()]
    return f"{jour} {moment.day} {MOIS_FR[moment.month - 1]} {moment.year}"


def catalogues(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[Catalogue]:
    """Marches demandes a l'API, par sport du lot. Lecture locale, aucun appel.

    Les marches de l'etage A comptent : le bloc les affiche, taire qu'on les
    demande laisserait croire que le « 1N2 » vient d'ailleurs.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT s.key AS sport_key, s.label AS sport_label, "
            "       c.oddsapi_key AS competition_key, e.commence_time "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY s.id",
            (session_id,),
        ).fetchall()

    asked: dict[str, tuple[str, set[str]]] = {}
    for row in rows:
        if has_started(row["commence_time"], now):
            continue
        label, keys = asked.setdefault(row["sport_key"], (row["sport_label"], set()))
        if not row["competition_key"]:
            # Evenement saisi a la main : aucun appel possible, rien a annoncer.
            continue
        keys.update(SCAN_MARKETS)
        keys.update(markets_for(row["sport_key"], row["competition_key"], settings))

    return [
        Catalogue(sport=label, markets=ordered_labels(sport_key, keys))
        for sport_key, (label, keys) in asked.items()
    ]


def competition_notes(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[CompetitionNote]:
    """Fiches des competitions du lot. Lecture locale, aucun appel.

    Un match deja commence n'entre pas dans le prompt : sa competition n'a donc
    pas a y apporter sa fiche, sans quoi le lot semblerait plus large qu'il
    n'est.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT c.label, c.notes, e.commence_time "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? AND c.notes IS NOT NULL AND TRIM(c.notes) <> '' "
            "ORDER BY c.label",
            (session_id,),
        ).fetchall()

    seen: dict[str, CompetitionNote] = {}
    for row in rows:
        if has_started(row["commence_time"], now) or row["label"] in seen:
            continue
        seen[row["label"]] = CompetitionNote(label=row["label"], notes=row["notes"].strip())
    return list(seen.values())


def build_prompt(
    session_id: int,
    template_name: str = DEFAULT_TEMPLATE,
    settings: Settings | None = None,
    now: datetime | None = None,
    competition_id: int | None = None,
) -> RenderedPrompt:
    """Rend le prompt d'une session. Ne touche ni au reseau ni a la base d'ecriture."""
    settings = settings or get_settings()
    if template_name not in list_templates():
        raise TemplateNotFound(template_name)

    moment = (now or datetime.now(ZoneInfo(settings.tz))).astimezone(ZoneInfo(settings.tz))
    events = renderable_events(session_id, settings, moment, competition_id)
    blocks = [render_event(event) for event in events]

    body = (
        _environment()
        .get_template(template_name)
        .render(
            date_fr=date_fr(moment),
            event_blocks=blocks,
            session_label=session_label(session_id, settings),
            tiers=load_tiers(settings),
            tz=settings.tz,
            catalogues=catalogues(session_id, settings, moment),
            competition_notes=competition_notes(session_id, settings, moment),
            # Les consignes permanentes et le retour d'experience ne coutent
            # aucun appel : ils sortent de la base, donc ils sont toujours la.
            preferences=read_preference(PREFERENCE_NOTES, settings),
            feedback=feedback(settings),
            # Le multichoix scores exacts n'a de sens que si un bloc sert
            # vraiment ce marche : l'imposer a un lot de tennis fait ecrire
            # « impossible » a chaque session, ce qui n'apprend rien.
            exact_scores=any(
                key.startswith("correct_score") for event in events for key in event.markets
            ),
        )
    )
    return RenderedPrompt(
        template_name=template_name,
        body=body,
        blocks=len(blocks),
        started=started_labels(session_id, settings, moment, competition_id),
    )


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


# -- Personnalisation --------------------------------------------------------

#: Un nom de template est un simple slug : aucune traversee de repertoire possible.
TEMPLATE_NAME = re.compile(r"^[a-z0-9][a-z0-9_-]*\.md\.j2$")

#: Consignes permanentes de l'utilisateur, injectees dans chaque prompt.
PREFERENCE_NOTES = "session_notes"

#: Garde-fou de taille : ces consignes sont recopiees dans chaque prompt, et
#: un pave de plusieurs pages y noierait les blocs de match qu'il doit servir.
PREFERENCE_MAX_LENGTH = 4000


def read_preference(key: str, settings: Settings | None = None) -> str:
    """Valeur d'une consigne permanente, ou chaine vide si elle n'existe pas."""
    with connect(settings) as conn:
        row = conn.execute("SELECT value FROM preferences WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else ""


def save_preference(key: str, value: str, settings: Settings | None = None) -> str:
    """Ecrit une consigne permanente. Vide, elle est supprimee plutot que stockee.

    Ce texte part tel quel dans le prompt : il n'est ni compile ni interprete,
    donc rien ne peut le casser — seule sa longueur est bornee.
    """
    cleaned = (value or "").strip()
    if len(cleaned) > PREFERENCE_MAX_LENGTH:
        raise CustomizationError(
            f"Consignes trop longues : {len(cleaned)} caractères pour un maximum "
            f"de {PREFERENCE_MAX_LENGTH}. Elles sont recopiées dans chaque prompt."
        )

    with connect(settings) as conn:
        if not cleaned:
            conn.execute("DELETE FROM preferences WHERE key = ?", (key,))
        else:
            conn.execute(
                "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
                "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
                "                               updated_at = excluded.updated_at",
                (key, cleaned, utcnow()),
            )
    logger.info("Consigne « %s » : %d caracteres", key, len(cleaned))
    return cleaned


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
