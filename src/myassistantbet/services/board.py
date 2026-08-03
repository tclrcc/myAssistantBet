"""Lecture des donnees affichees par le Board, et gestion de la selection.

Ne fait aucun appel externe : uniquement des lectures et ecritures locales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.oddsapi import PROVIDER as ODDSAPI_PROVIDER
from .mapping_ui import pending_count
from .scan import scan_window

H2H_MARKET = "h2h"
TOTALS_MARKET = "totals"


@dataclass
class Filters:
    """Criteres de filtrage du board. Une valeur vide signifie « pas de filtre »."""

    sport: str = ""
    competition_id: int | None = None
    hour_from: int | None = None
    hour_to: int | None = None
    text: str = ""


@dataclass
class BoardRow:
    """Une ligne du board : un evenement et ses cotes principales."""

    event_id: int
    sport_key: str
    sport_label: str
    competition_label: str
    home: str
    away: str
    commence_time: str
    local_time: datetime
    home_price: float | None = None
    draw_price: float | None = None
    away_price: float | None = None
    total_point: float | None = None
    over_price: float | None = None
    under_price: float | None = None
    selected: bool = False

    @property
    def affiche(self) -> str:
        # Le cyclisme n'a pas de second participant : pas de tiret orphelin.
        return f"{self.home} – {self.away}" if self.away else self.home

    @property
    def has_odds(self) -> bool:
        return self.home_price is not None or self.total_point is not None


@dataclass
class Banner:
    """Bandeau permanent : quota, dernier scan, taille de la selection."""

    credits_remaining: int | None = None
    last_scan_at: datetime | None = None
    selected_count: int = 0
    credit_floor: int = 500
    session_id: int | None = None
    mapping_pending: int = 0

    @property
    def below_floor(self) -> bool:
        return self.credits_remaining is not None and self.credits_remaining < self.credit_floor


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _main_total(
    lines: dict[float, dict[str, float]],
) -> tuple[float, float | None, float | None] | None:
    """Ligne O/U principale : celle dont les deux cotes sont les plus proches.

    C'est la definition usuelle de la ligne « de reference » d'un bookmaker.
    Les lignes incompletes (une seule issue) ne sont retenues qu'en dernier recours.
    """
    complete = {
        point: prices for point, prices in lines.items() if "Over" in prices and "Under" in prices
    }
    if complete:
        point = min(complete, key=lambda p: abs(complete[p]["Over"] - complete[p]["Under"]))
        return point, complete[point]["Over"], complete[point]["Under"]
    if lines:
        point = min(lines)
        return point, lines[point].get("Over"), lines[point].get("Under")
    return None


def current_session(settings: Settings | None = None) -> int:
    """Id de la session de travail courante, creee au besoin.

    Une session par jour local : cocher un match n'exige aucune action prealable.
    """
    settings = settings or get_settings()
    label = datetime.now(ZoneInfo(settings.tz)).strftime("%d/%m/%Y")
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT id FROM sessions WHERE label = ? ORDER BY id DESC LIMIT 1", (label,)
        ).fetchone()
        if row is not None:
            return int(row["id"])
        cursor = conn.execute(
            "INSERT INTO sessions (label, created_at) VALUES (?, ?)", (label, utcnow())
        )
        return int(cursor.lastrowid)


def toggle_selection(event_id: int, selected: bool, settings: Settings | None = None) -> int:
    """Ajoute ou retire un evenement de la session courante. Renvoie son id."""
    settings = settings or get_settings()
    session_id = current_session(settings)
    with connect(settings) as conn:
        if selected:
            conn.execute(
                "INSERT INTO session_events (session_id, event_id) VALUES (?, ?) "
                "ON CONFLICT(session_id, event_id) DO NOTHING",
                (session_id, event_id),
            )
        else:
            conn.execute(
                "DELETE FROM session_events WHERE session_id = ? AND event_id = ?",
                (session_id, event_id),
            )
    return session_id


def selected_event_ids(settings: Settings | None = None) -> set[int]:
    settings = settings or get_settings()
    session_id = current_session(settings)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT event_id FROM session_events WHERE session_id = ?", (session_id,)
        ).fetchall()
    return {int(row["event_id"]) for row in rows}


def list_rows(
    filters: Filters | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[BoardRow]:
    """Evenements de la fenetre courante, avec leurs cotes 1N2 et O/U principales."""
    settings = settings or get_settings()
    filters = filters or Filters()
    window_start, window_end = scan_window(settings, now)
    selected = selected_event_ids(settings)

    sql = [
        "SELECT e.id, e.home, e.away, e.commence_time, s.key AS sport_key, s.label AS sport_label,",
        "       COALESCE(c.label, '—') AS competition_label",
        "FROM events e",
        "JOIN sports s ON s.id = e.sport_id",
        "LEFT JOIN competitions c ON c.id = e.competition_id",
        "WHERE e.commence_time >= ? AND e.commence_time <= ?",
    ]
    params: list[Any] = [_iso(window_start), _iso(window_end)]

    if filters.sport:
        sql.append("AND s.key = ?")
        params.append(filters.sport)
    if filters.competition_id:
        sql.append("AND e.competition_id = ?")
        params.append(filters.competition_id)
    if filters.text:
        sql.append("AND (e.home LIKE ? OR e.away LIKE ? OR c.label LIKE ?)")
        needle = f"%{filters.text}%"
        params.extend([needle, needle, needle])

    sql.append("ORDER BY e.commence_time, c.priority DESC")

    with connect(settings) as conn:
        events = conn.execute("\n".join(sql), params).fetchall()
        if not events:
            return []

        ids = [int(row["id"]) for row in events]
        placeholders = ",".join("?" * len(ids))
        odds = conn.execute(
            f"SELECT event_id, market_key, outcome_name, point, price FROM odds "
            f"WHERE event_id IN ({placeholders}) AND market_key IN (?, ?)",
            [*ids, H2H_MARKET, TOTALS_MARKET],
        ).fetchall()

    h2h: dict[int, dict[str, float]] = {}
    totals: dict[int, dict[float, dict[str, float]]] = {}
    for row in odds:
        event_id = int(row["event_id"])
        if row["market_key"] == H2H_MARKET:
            h2h.setdefault(event_id, {})[row["outcome_name"]] = float(row["price"])
        elif row["point"] is not None:
            point = float(row["point"])
            totals.setdefault(event_id, {}).setdefault(point, {})[row["outcome_name"]] = float(
                row["price"]
            )

    rows: list[BoardRow] = []
    for event in events:
        event_id = int(event["id"])
        prices = h2h.get(event_id, {})
        row = BoardRow(
            event_id=event_id,
            sport_key=event["sport_key"],
            sport_label=event["sport_label"],
            competition_label=event["competition_label"],
            home=event["home"],
            away=event["away"],
            commence_time=event["commence_time"],
            local_time=_local(event["commence_time"], settings.tz),
            home_price=prices.get(event["home"]),
            draw_price=prices.get("Draw"),
            away_price=prices.get(event["away"]),
            selected=event_id in selected,
        )
        main = _main_total(totals.get(event_id, {}))
        if main:
            row.total_point, row.over_price, row.under_price = main

        if filters.hour_from is not None and row.local_time.hour < filters.hour_from:
            continue
        if filters.hour_to is not None and row.local_time.hour > filters.hour_to:
            continue
        rows.append(row)

    return rows


def filter_options(settings: Settings | None = None) -> dict[str, list[dict[str, Any]]]:
    """Valeurs proposees dans les listes deroulantes de filtre."""
    with connect(settings) as conn:
        sports = [
            dict(row)
            for row in conn.execute("SELECT key, label FROM sports ORDER BY id").fetchall()
        ]
        competitions = [
            dict(row)
            for row in conn.execute(
                "SELECT c.id, c.label, s.key AS sport_key FROM competitions c "
                "JOIN sports s ON s.id = c.sport_id WHERE c.active = 1 "
                "ORDER BY c.priority DESC, c.label"
            ).fetchall()
        ]
    return {"sports": sports, "competitions": competitions}


def banner(settings: Settings | None = None) -> Banner:
    """Etat du bandeau : credits restants, dernier scan, nombre de matchs coches."""
    settings = settings or get_settings()
    state = Banner(credit_floor=settings.odds_api_credit_floor)

    with connect(settings) as conn:
        row = conn.execute(
            "SELECT remaining, called_at FROM api_usage "
            "WHERE provider = ? AND remaining IS NOT NULL "
            "ORDER BY called_at DESC, id DESC LIMIT 1",
            (ODDSAPI_PROVIDER,),
        ).fetchone()
        if row is not None:
            state.credits_remaining = int(row["remaining"])
        last_scan = conn.execute(
            "SELECT MAX(called_at) AS at FROM api_usage WHERE provider = ? AND cost > 0",
            (ODDSAPI_PROVIDER,),
        ).fetchone()

    if last_scan is not None and last_scan["at"]:
        state.last_scan_at = _local(last_scan["at"], settings.tz)

    state.session_id = current_session(settings)
    state.selected_count = len(selected_event_ids(settings))
    state.mapping_pending = pending_count(settings)
    return state


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class BoardView:
    """Tout ce dont le template a besoin."""

    rows: list[BoardRow] = field(default_factory=list)
    banner: Banner = field(default_factory=Banner)
    options: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    filters: Filters = field(default_factory=Filters)


def build_view(
    filters: Filters | None = None,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> BoardView:
    settings = settings or get_settings()
    filters = filters or Filters()
    return BoardView(
        rows=list_rows(filters, settings, now),
        banner=banner(settings),
        options=filter_options(settings),
        filters=filters,
    )
