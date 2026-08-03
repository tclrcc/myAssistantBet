"""Lecture d'une session : shortlist, notes, et assemblage des blocs de rendu.

Aucun appel externe : uniquement des lectures et ecritures locales.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect
from .render import Outcome, RenderableEvent, render_event

#: Marches releves par l'etage A. Leur presence seule signale un evenement
#: pas encore enrichi.
SCAN_ONLY_MARKETS = frozenset({"h2h", "totals"})

BOOKMAKER_LABELS = {"betclic_fr": "Betclic"}


@dataclass
class ShortlistEvent:
    """Un evenement de la shortlist, avec son etat d'enrichissement."""

    event_id: int
    sport_key: str
    sport_label: str
    competition: str
    home: str
    away: str
    local_time: datetime
    note: str = ""
    markets: int = 0
    deep_markets: int = 0

    @property
    def affiche(self) -> str:
        return f"{self.home} – {self.away}"

    @property
    def enriched(self) -> bool:
        return self.deep_markets > 0


@dataclass
class SessionView:
    """Shortlist regroupee par sport."""

    session_id: int
    label: str
    groups: list[tuple[str, list[ShortlistEvent]]] = field(default_factory=list)

    @property
    def count(self) -> int:
        return sum(len(events) for _, events in self.groups)


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _rows(session_id: int, settings: Settings) -> list[Any]:
    with connect(settings) as conn:
        return conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, se.note, "
            "       s.key AS sport_key, s.label AS sport_label, "
            "       COALESCE(c.label, '—') AS competition "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY s.id, e.commence_time",
            (session_id,),
        ).fetchall()


def session_label(session_id: int, settings: Settings | None = None) -> str:
    with connect(settings) as conn:
        row = conn.execute("SELECT label FROM sessions WHERE id = ?", (session_id,)).fetchone()
    return (row["label"] if row and row["label"] else f"Session {session_id}") if row else ""


def session_exists(session_id: int, settings: Settings | None = None) -> bool:
    with connect(settings) as conn:
        return (
            conn.execute("SELECT 1 FROM sessions WHERE id = ?", (session_id,)).fetchone()
            is not None
        )


def set_note(session_id: int, event_id: int, note: str, settings: Settings | None = None) -> None:
    """Enregistre la note libre d'un evenement, injectee telle quelle dans le prompt."""
    with connect(settings) as conn:
        conn.execute(
            "UPDATE session_events SET note = ? WHERE session_id = ? AND event_id = ?",
            (note.strip() or None, session_id, event_id),
        )


def build_view(session_id: int, settings: Settings | None = None) -> SessionView:
    """Shortlist de la session, regroupee par sport."""
    settings = settings or get_settings()
    rows = _rows(session_id, settings)

    counts = _market_counts([int(row["id"]) for row in rows], settings)
    groups: dict[str, list[ShortlistEvent]] = {}
    for row in rows:
        event_id = int(row["id"])
        all_markets, deep = counts.get(event_id, (0, 0))
        groups.setdefault(row["sport_label"], []).append(
            ShortlistEvent(
                event_id=event_id,
                sport_key=row["sport_key"],
                sport_label=row["sport_label"],
                competition=row["competition"],
                home=row["home"],
                away=row["away"],
                local_time=_local(row["commence_time"], settings.tz),
                note=row["note"] or "",
                markets=all_markets,
                deep_markets=deep,
            )
        )

    return SessionView(
        session_id=session_id,
        label=session_label(session_id, settings),
        groups=list(groups.items()),
    )


def _market_counts(event_ids: list[int], settings: Settings) -> dict[int, tuple[int, int]]:
    """Pour chaque evenement : (marches distincts, marches profonds)."""
    if not event_ids:
        return {}
    placeholders = ",".join("?" * len(event_ids))
    with connect(settings) as conn:
        rows = conn.execute(
            f"SELECT event_id, market_key FROM odds WHERE event_id IN ({placeholders}) "
            f"GROUP BY event_id, market_key",
            event_ids,
        ).fetchall()

    counts: dict[int, tuple[int, int]] = {}
    for row in rows:
        event_id = int(row["event_id"])
        total, deep = counts.get(event_id, (0, 0))
        is_deep = row["market_key"] not in SCAN_ONLY_MARKETS
        counts[event_id] = (total + 1, deep + (1 if is_deep else 0))
    return counts


def renderable_events(session_id: int, settings: Settings | None = None) -> list[RenderableEvent]:
    """Construit les blocs de rendu de la session, dans l'ordre chronologique."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, se.note, "
            "       s.key AS sport_key, COALESCE(c.label, '—') AS competition "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time, e.id",
            (session_id,),
        ).fetchall()

        events: list[RenderableEvent] = []
        for index, row in enumerate(rows, start=1):
            odds = conn.execute(
                "SELECT bookmaker, market_key, outcome_name, description, point, price, "
                "       fetched_at FROM odds WHERE event_id = ? ORDER BY market_key, price",
                (int(row["id"]),),
            ).fetchall()

            markets: dict[str, list[Outcome]] = {}
            bookmaker = None
            fetched: str | None = None
            for odd in odds:
                markets.setdefault(odd["market_key"], []).append(
                    Outcome(
                        name=odd["outcome_name"],
                        price=float(odd["price"]),
                        point=odd["point"],
                        description=odd["description"],
                    )
                )
                bookmaker = bookmaker or odd["bookmaker"]
                if fetched is None or odd["fetched_at"] > fetched:
                    fetched = odd["fetched_at"]

            events.append(
                RenderableEvent(
                    index=index,
                    sport_key=row["sport_key"],
                    competition=row["competition"],
                    home=row["home"],
                    away=row["away"],
                    commence_local=_local(row["commence_time"], settings.tz),
                    markets=markets,
                    note=row["note"] or None,
                    bookmaker_label=BOOKMAKER_LABELS.get(bookmaker or "", bookmaker or "—"),
                    fetched_local=_local(fetched, settings.tz) if fetched else None,
                )
            )
    return events


def render_blocks(session_id: int, settings: Settings | None = None) -> list[str]:
    """Blocs texte compacts de la session, prets pour le template de prompt."""
    return [render_event(event) for event in renderable_events(session_id, settings)]
