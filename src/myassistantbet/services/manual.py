"""Evenements saisis a la main.

Indispensable pour ce qu'aucune API ne couvre : le cyclisme entierement, et le
tennis en dehors des Grands Chelems et des Masters (SPEC.md section 4).

Un evenement manuel vit dans les memes tables qu'un evenement d'API : il apparait
sur le board, se coche, et se rend avec le meme moteur. Seules ses cotes portent
le bookmaker `manual` et son enrichissement ne declenche aucun appel.
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from datetime import datetime
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .labels import sort_key

logger = logging.getLogger(__name__)

MANUAL_BOOKMAKER = "manual"
MANUAL_MARKET = "outright"
KIND_MANUAL_NOTE = "manual_note"

#: `Pogacar 2.50`, `Pogacar = 2.50`, `Pogacar; 2,50` — on accepte les trois.
ODDS_LINE = re.compile(r"^\s*(?P<name>.+?)\s*[;=]?\s*(?P<price>\d+[.,]\d+|\d+)\s*$")

#: Libelles des deux participants, selon le sport.
PARTICIPANT_LABELS = {
    "football": ("Équipe à domicile", "Équipe à l'extérieur"),
    "tennis": ("Joueur 1", "Joueur 2"),
    # En cyclisme la course est la « competition » et l'etape tient lieu d'affiche :
    # il n'y a pas de second participant.
    "cycling": ("Étape", ""),
}


@dataclass
class ParsedOdds:
    """Cotes saisies a la main, et ce qui n'a pas pu etre lu."""

    outcomes: list[tuple[str, float]] = field(default_factory=list)
    rejected: list[str] = field(default_factory=list)


@dataclass
class ManualEvent:
    """Saisie du formulaire, apres validation."""

    sport_key: str
    competition: str
    home: str
    away: str
    commence_local: datetime
    odds: ParsedOdds
    links: list[str] = field(default_factory=list)
    notes: str = ""
    #: Specifiques au cyclisme (SPEC section 7), vides ailleurs.
    profile: str = ""
    startlist: str = ""


class ManualError(ValueError):
    """Saisie invalide. Le message est affiche tel quel a l'utilisateur."""


def parse_odds(raw: str) -> ParsedOdds:
    """Lit un bloc `nom cote` par ligne. Une ligne illisible est signalee, pas ignoree."""
    parsed = ParsedOdds()
    for line in (raw or "").splitlines():
        if not line.strip():
            continue
        match = ODDS_LINE.match(line)
        if not match:
            parsed.rejected.append(line.strip())
            continue
        try:
            price = float(match.group("price").replace(",", "."))
        except ValueError:
            parsed.rejected.append(line.strip())
            continue
        name = match.group("name").strip(" ;=")
        if not name or price <= 1.0:
            parsed.rejected.append(line.strip())
            continue
        parsed.outcomes.append((name, price))
    return parsed


def parse_links(raw: str) -> list[str]:
    """Une URL par ligne. Tout ce qui ne ressemble pas a une URL est ecarte."""
    links = []
    for line in (raw or "").splitlines():
        candidate = line.strip()
        if candidate.startswith(("http://", "https://")):
            links.append(candidate)
    return links


def parse_datetime(date_value: str, time_value: str, settings: Settings) -> datetime:
    """`2026-08-04` + `13:00` en heure locale -> instant aware."""
    try:
        naive = datetime.strptime(f"{date_value} {time_value}", "%Y-%m-%d %H:%M")
    except ValueError as exc:
        raise ManualError("Date ou heure invalide (attendu AAAA-MM-JJ et HH:MM).") from exc
    return naive.replace(tzinfo=ZoneInfo(settings.tz))


def build(
    sport_key: str,
    competition: str,
    home: str,
    away: str,
    date_value: str,
    time_value: str,
    odds_raw: str,
    links_raw: str,
    notes: str,
    profile: str = "",
    startlist: str = "",
    settings: Settings | None = None,
) -> ManualEvent:
    """Valide la saisie du formulaire. Leve `ManualError` avec un message lisible."""
    settings = settings or get_settings()

    if sport_key not in PARTICIPANT_LABELS:
        raise ManualError(f"Sport inconnu : {sport_key}")
    first, second = PARTICIPANT_LABELS[sport_key]
    if not home.strip():
        raise ManualError(f"« {first} » est obligatoire.")
    if not away.strip() and sport_key != "cycling":
        raise ManualError(f"« {second} » est obligatoire.")

    return ManualEvent(
        sport_key=sport_key,
        competition=competition.strip() or "Saisie manuelle",
        home=home.strip(),
        away=away.strip(),
        commence_local=parse_datetime(date_value, time_value, settings),
        odds=parse_odds(odds_raw),
        links=parse_links(links_raw),
        notes=notes.strip(),
        profile=profile.strip(),
        startlist=startlist.strip(),
    )


def _competition_id(conn, sport_key: str, label: str) -> int:
    """Competition manuelle, creee au besoin. Reconnaissable a son `oddsapi_key` nul.

    **Le rapprochement ignore la casse et les accents** (`labels.sort_key`),
    comme les deux portes de creation de `competitions.py`. L'egalite stricte
    d'origine faisait de « Qualifications US Open » et « qualifications us
    open » deux competitions distinctes, que rien ne distinguait a l'ecran et
    qui se partageaient les matchs — le doublon exact que ces portes refusent.
    Il se voyait d'autant moins que le champ est libre : on retape le libelle a
    chaque match, et il suffit d'une majuscule pour forger un jumeau.
    """
    sport = conn.execute("SELECT id FROM sports WHERE key = ?", (sport_key,)).fetchone()
    if sport is None:
        raise ManualError(f"Sport inconnu : {sport_key}")

    cible = sort_key(label)
    for row in conn.execute(
        "SELECT id, label FROM competitions WHERE sport_id = ? AND oddsapi_key IS NULL",
        (sport["id"],),
    ).fetchall():
        if sort_key(row["label"]) == cible:
            return int(row["id"])

    cursor = conn.execute(
        "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active) "
        "VALUES (?, NULL, ?, 0, 1)",
        (sport["id"], label),
    )
    return int(cursor.lastrowid)


def save(event: ManualEvent, settings: Settings | None = None) -> int:
    """Enregistre l'evenement, ses cotes et ses references. Renvoie son id."""
    settings = settings or get_settings()
    commence_utc = event.commence_local.astimezone(ZoneInfo("UTC")).strftime("%Y-%m-%dT%H:%M:%SZ")

    with connect(settings) as conn:
        sport = conn.execute("SELECT id FROM sports WHERE key = ?", (event.sport_key,)).fetchone()
        competition_id = _competition_id(conn, event.sport_key, event.competition)

        cursor = conn.execute(
            "INSERT INTO events (sport_id, competition_id, home, away, commence_time, "
            "                    source, created_at) VALUES (?, ?, ?, ?, ?, 'manual', ?)",
            (sport["id"], competition_id, event.home, event.away, commence_utc, utcnow()),
        )
        event_id = int(cursor.lastrowid)

        for name, price in event.odds.outcomes:
            conn.execute(
                "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, "
                "                  fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, MANUAL_BOOKMAKER, MANUAL_MARKET, name, price, utcnow()),
            )

        payload = {
            "links": event.links,
            "notes": event.notes,
            "profile": event.profile,
            "startlist": event.startlist,
        }
        if any(payload.values()):
            conn.execute(
                "INSERT INTO context (event_id, kind, payload_json, fetched_at) "
                "VALUES (?, ?, ?, ?)",
                (event_id, KIND_MANUAL_NOTE, json.dumps(payload, ensure_ascii=False), utcnow()),
            )

    logger.info(
        "Evenement manuel cree : %s – %s (%s, %d cote(s))",
        event.home,
        event.away,
        event.sport_key,
        len(event.odds.outcomes),
    )
    return event_id


@dataclass
class AttachResult:
    """Bilan d'une saisie de cotes sur un evenement deja connu."""

    written: int = 0
    removed: int = 0
    rejected: list[str] = field(default_factory=list)


def attach_odds(
    event_id: int,
    raw: str,
    replace: bool = False,
    settings: Settings | None = None,
) -> AttachResult:
    """Ajoute des cotes saisies a la main a un evenement existant.

    Sert a completer ce que l'API ne donne pas : un marche absent de Betclic,
    une cote relevee a l'ecran. Ne touche **que** les lignes portant le
    bookmaker `manual` : les cotes d'API ne sont jamais ecrasees, sans quoi un
    releve de marche serait remplace par une frappe au clavier.

    Ressaisir un nom deja present met sa cote a jour au lieu de la dupliquer.
    """
    settings = settings or get_settings()
    parsed = parse_odds(raw)
    if not parsed.outcomes:
        raise ManualError(
            "Aucune cote lisible. Une ligne par cote, au format « Nom 2.50 »."
            if parsed.rejected
            else "Saisis au moins une cote."
        )

    result = AttachResult(rejected=parsed.rejected)
    with connect(settings) as conn:
        event = conn.execute("SELECT id FROM events WHERE id = ?", (event_id,)).fetchone()
        if event is None:
            raise ManualError("Cet evenement n'existe pas.")

        if replace:
            cursor = conn.execute(
                "DELETE FROM odds WHERE event_id = ? AND bookmaker = ?",
                (event_id, MANUAL_BOOKMAKER),
            )
            result.removed = cursor.rowcount

        for name, price in parsed.outcomes:
            if not replace:
                conn.execute(
                    "DELETE FROM odds WHERE event_id = ? AND bookmaker = ? AND market_key = ? "
                    "AND outcome_name = ?",
                    (event_id, MANUAL_BOOKMAKER, MANUAL_MARKET, name),
                )
            conn.execute(
                "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, "
                "                  fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                (event_id, MANUAL_BOOKMAKER, MANUAL_MARKET, name, price, utcnow()),
            )
            result.written += 1

    logger.info(
        "Cotes manuelles sur l'evenement %d : %d ecrite(s), %d retiree(s), %d refusee(s)",
        event_id,
        result.written,
        result.removed,
        len(result.rejected),
    )
    return result


def clear_manual_odds(event_id: int, settings: Settings | None = None) -> int:
    """Retire toutes les cotes manuelles d'un evenement. Renvoie le nombre retire."""
    with connect(settings) as conn:
        cursor = conn.execute(
            "DELETE FROM odds WHERE event_id = ? AND bookmaker = ?",
            (event_id, MANUAL_BOOKMAKER),
        )
        return cursor.rowcount
