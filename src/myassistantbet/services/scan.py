"""Etage A — scan large des competitions actives.

Recupere les cotes 1N2 et totals de chaque competition active, puis met a jour la
base. Aucun appel HTTP direct ici : le client est injecte.

Idempotence : un evenement est identifie par son `oddsapi_event_id` (upsert), et
les cotes d'un triplet (evenement, bookmaker, marche) sont remplacees en bloc a
chaque scan — un marche peut changer d'ensemble d'issues entre deux releves.
"""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import UTC, datetime, time, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError
from ..providers.oddsapi import SCAN_MARKETS, OddsAPIClient
from .render import LEAD_TIME_MIN_MINUTES

logger = logging.getLogger(__name__)


@dataclass
class CompetitionScan:
    """Resultat du scan d'une competition."""

    label: str
    oddsapi_key: str
    events: int = 0
    odds_rows: int = 0
    cost: int = 0
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.error is None


@dataclass
class ScanReport:
    """Bilan d'un scan complet."""

    started_at: str
    finished_at: str
    competitions: list[CompetitionScan] = field(default_factory=list)

    @property
    def total_cost(self) -> int:
        return sum(item.cost for item in self.competitions)

    @property
    def total_events(self) -> int:
        return sum(item.events for item in self.competitions)

    @property
    def total_odds(self) -> int:
        return sum(item.odds_rows for item in self.competitions)

    @property
    def failures(self) -> list[CompetitionScan]:
        return [item for item in self.competitions if not item.ok]


def scan_window(settings: Settings, now: datetime | None = None) -> tuple[datetime, datetime]:
    """Fenetre glissante du scan, en UTC.

    Debute a l'instant present et se termine a la fin de la journee locale
    J+(scan_window_days - 1) : avec la valeur par defaut 2, J+0 et J+1 complets.
    """
    now = now or datetime.now(UTC)
    local_tz = ZoneInfo(settings.tz)
    local_now = now.astimezone(local_tz)
    last_day = local_now.date() + timedelta(days=settings.scan_window_days - 1)
    end_local = datetime.combine(last_day, time(23, 59, 59), tzinfo=local_tz)
    return now.astimezone(UTC), end_local.astimezone(UTC)


def active_competitions(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Competitions actives interrogeables via The Odds API, par priorite decroissante."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.oddsapi_key, c.sport_id, s.key AS sport_key "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "WHERE c.active = 1 AND c.oddsapi_key IS NOT NULL "
            "ORDER BY c.priority DESC, c.label"
        ).fetchall()
    return [dict(row) for row in rows]


def _shift_columns(conn: sqlite3.Connection, payload: dict[str, Any]) -> tuple[str, str] | None:
    """`(ancien coup d'envoi, instant du constat)` quand l'horaire a bouge.

    **Le fait dominant d'une soiree peut etre un report**, et l'application
    l'effacait a chaque scan : une journee d'orages a Cincinnati a repousse tout
    le programme de cinq heures — 17:30 au releve de 12:42, 22:30 a celui de
    22:15 — et le prompt ne portait que la derniere heure. Le decalage a du etre
    retrouve dans la presse alors que les deux relevés etaient passes par ici.

    Le seuil est celui de l'age d'un releve (`LEAD_TIME_MIN_MINUTES`, 15) plutot
    qu'un nombre invente a cote : c'est la meme question — a partir de quand un
    ecart de temps veut dire quelque chose — et deux reponses differentes a une
    meme question finissent par diverger.

    Un decalage deja enregistre **n'est pas efface** par un scan qui ne bouge
    plus : le report a eu lieu, et c'est lui qui decrit la soiree.
    """
    row = conn.execute(
        "SELECT commence_time FROM events WHERE oddsapi_event_id = ?", (payload["id"],)
    ).fetchone()
    if row is None:
        return None
    avant, apres = _moment(row["commence_time"]), _moment(payload.get("commence_time"))
    if avant is None or apres is None:
        return None
    if abs(apres - avant) < timedelta(minutes=LEAD_TIME_MIN_MINUTES):
        return None
    return str(row["commence_time"]), utcnow()


def _moment(value: Any) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (AttributeError, TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _upsert_event(
    conn: sqlite3.Connection,
    payload: dict[str, Any],
    competition: dict[str, Any],
) -> int:
    """Insere ou met a jour un evenement, et renvoie son id interne."""
    moved = _shift_columns(conn, payload)
    conn.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "                    commence_time, source, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'api', ?) "
        "ON CONFLICT(oddsapi_event_id) DO UPDATE SET "
        "  sport_id = excluded.sport_id, "
        "  competition_id = excluded.competition_id, "
        "  home = excluded.home, "
        "  away = excluded.away, "
        "  commence_time = excluded.commence_time",
        (
            competition["sport_id"],
            competition["id"],
            payload["id"],
            payload["home_team"],
            payload["away_team"],
            payload["commence_time"],
            utcnow(),
        ),
    )
    if moved:
        conn.execute(
            "UPDATE events SET previous_commence_time = ?, commence_shifted_at = ? "
            "WHERE oddsapi_event_id = ?",
            (*moved, payload["id"]),
        )
    row = conn.execute(
        "SELECT id FROM events WHERE oddsapi_event_id = ?", (payload["id"],)
    ).fetchone()
    return int(row["id"])


def replace_odds(conn: sqlite3.Connection, event_id: int, payload: dict[str, Any]) -> int:
    """Remplace les cotes de l'evenement pour chaque (bookmaker, marche) recu."""
    inserted = 0
    for bookmaker in payload.get("bookmakers") or []:
        book_key = bookmaker.get("key")
        if not book_key:
            continue
        fetched_at = bookmaker.get("last_update") or utcnow()

        for market in bookmaker.get("markets") or []:
            market_key = market.get("key")
            if not market_key:
                continue

            conn.execute(
                "DELETE FROM odds WHERE event_id = ? AND bookmaker = ? AND market_key = ?",
                (event_id, book_key, market_key),
            )
            for outcome in market.get("outcomes") or []:
                price = outcome.get("price")
                name = outcome.get("name")
                if price is None or name is None:
                    continue
                conn.execute(
                    "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, "
                    "                  description, point, price, fetched_at) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        event_id,
                        book_key,
                        market_key,
                        name,
                        outcome.get("description"),
                        outcome.get("point"),
                        float(price),
                        fetched_at,
                    ),
                )
                inserted += 1
    return inserted


def _persist(
    events: list[dict[str, Any]],
    competition: dict[str, Any],
    window_end: datetime,
    settings: Settings,
) -> tuple[int, int]:
    """Ecrit les evenements et leurs cotes. Renvoie (evenements, lignes de cotes)."""
    kept = 0
    odds_rows = 0
    with connect(settings) as conn:
        for payload in events:
            if not _within_window(payload.get("commence_time"), window_end):
                continue
            event_id = _upsert_event(conn, payload, competition)
            odds_rows += replace_odds(conn, event_id, payload)
            kept += 1
    return kept, odds_rows


def _within_window(commence_time: str | None, window_end: datetime) -> bool:
    if not commence_time:
        return False
    try:
        moment = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except ValueError:
        logger.warning("Date de match illisible, evenement ignore : %r", commence_time)
        return False
    return moment <= window_end


async def run_scan(
    client: OddsAPIClient,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> ScanReport:
    """Scanne toutes les competitions actives et met la base a jour.

    Une competition en echec n'interrompt pas les autres : l'erreur est portee
    par le rapport, jamais levee.
    """
    settings = settings or get_settings()
    started_at = utcnow()
    _, window_end = scan_window(settings, now)
    report = ScanReport(started_at=started_at, finished_at=started_at)

    for competition in active_competitions(settings):
        result = CompetitionScan(label=competition["label"], oddsapi_key=competition["oddsapi_key"])
        try:
            events, cost = await client.get_odds(
                competition["oddsapi_key"],
                markets=SCAN_MARKETS,
                commence_time_to=window_end.strftime("%Y-%m-%dT%H:%M:%SZ"),
            )
            result.cost = cost
            result.events, result.odds_rows = _persist(events, competition, window_end, settings)
        except ProviderError as exc:
            result.error = str(exc)
            logger.warning("Scan echoue pour %s : %s", competition["label"], exc)
        report.competitions.append(result)

    report.finished_at = utcnow()
    logger.info(
        "Scan termine : %d evenements, %d cotes, cout %d credits, %d competition(s) en echec",
        report.total_events,
        report.total_odds,
        report.total_cost,
        len(report.failures),
    )
    return report
