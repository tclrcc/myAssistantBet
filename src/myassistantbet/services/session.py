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
from ..providers.oddsapi import DEFAULT_BOOKMAKER
from . import coverage, elo
from .context import context_lines
from .labels import UNTIMED_BOOKMAKERS, affiche, bookmaker_label, primary_book
from .render import (
    MARKET_ORDER,
    MARKET_ORDER_BY_SPORT,
    Outcome,
    RenderableEvent,
    render_event,
)

#: Marches releves par l'etage A. Leur presence seule signale un evenement
#: pas encore enrichi.
SCAN_ONLY_MARKETS = frozenset({"h2h", "totals"})


def has_started(commence_time: str, now: datetime | None = None) -> bool:
    """Vrai si le match a deja commence : il n'y a plus rien a parier avant-match.

    Meme borne que le debut de la fenetre du board : un evenement qui disparait
    du board ne doit pas continuer a peser dans la selection. Il reste attache a
    la session — l'historique des picks en a besoin — mais sort du prompt, de
    l'enrichissement et du compteur de selection.
    """
    try:
        moment = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        # Date illisible : ne jamais ecarter un match sur une lecture ratee.
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment <= (now or datetime.now(UTC))


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
    #: Le match a commence : conserve dans la liste, exclu du prompt.
    started: bool = False

    @property
    def affiche(self) -> str:
        return affiche(self.home, self.away)

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

    @property
    def upcoming(self) -> int:
        """Matchs encore a venir : ceux qui alimentent le prompt."""
        return sum(1 for _, events in self.groups for event in events if not event.started)

    @property
    def started_count(self) -> int:
        return self.count - self.upcoming


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


def remove_event(session_id: int, event_id: int, settings: Settings | None = None) -> None:
    """Retire un evenement de la session, quelle que soit la session visee.

    Distinct de `board.toggle_selection`, qui ne connait que la session du jour :
    un match deja commence a quitte le board et ne peut plus y etre decoche.
    """
    with connect(settings) as conn:
        conn.execute(
            "DELETE FROM session_events WHERE session_id = ? AND event_id = ?",
            (session_id, event_id),
        )


def started_labels(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Affiches des matchs ecartes parce qu'ils ont commence.

    Le prompt ne les contient pas ; la page doit le dire plutot que de laisser
    croire que la selection entiere a ete analysee.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.home, e.away, e.commence_time "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time",
            (session_id,),
        ).fetchall()
    return [
        affiche(row["home"], row["away"]) for row in rows if has_started(row["commence_time"], now)
    ]


def build_view(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> SessionView:
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
                started=has_started(row["commence_time"], now),
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


def _context_for(row: Any, settings: Settings) -> list[tuple[str, str]]:
    """Lignes du bloc CONTEXTE, toutes sources confondues.

    Le contexte football vient d'API-Football, le tennis de son classement Elo :
    deux sources disjointes, assemblees ici plutot que dans `context.py`, qui
    n'a pas a connaitre le tennis.
    """
    lines = context_lines(int(row["id"]), row["home"], row["away"], row["commence_time"], settings)
    if row["sport_key"] == "tennis":
        lines += elo.lines(row["home"], row["away"], row["oddsapi_key"], row["surface"], settings)
    return lines


def _is_substitute(primary: str) -> bool:
    """Vrai si la source principale est un book de repli, pas le fournisseur.

    Sur ces matchs, The Odds API ne connait pas la rencontre : rien ne lui a
    ete demande, donc `coverage` n'a aucun constat a offrir.
    """
    return bool(primary) and primary not in {DEFAULT_BOOKMAKER, *UNTIMED_BOOKMAKERS}


def _unserved_for(
    row: Any, present: set[str], primary: str, unserved: dict[int, set[str]]
) -> list[str]:
    """Marches modelises absents du bloc, et pour la bonne raison.

    Sur un evenement servi par The Odds API, la reponse vient de `coverage` :
    ce que le book a deja refuse de servir sur cette competition. Sur un
    evenement releve chez un book de substitution, il n'y a aucun constat a
    consulter — le marche manque parce que ce book ne l'offre pas ici. Sans
    cette ligne, l'absence d'un handicap se lisait comme un oubli de l'outil,
    et rien ne permettait de trancher.
    """
    if not _is_substitute(primary):
        return sorted(unserved.get(row["competition_id"], set()))
    order = MARKET_ORDER_BY_SPORT.get(row["sport_key"], MARKET_ORDER)
    return [key for key, _ in order if key not in present and key != "outright"]


def renderable_events(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[RenderableEvent]:
    """Blocs de rendu de la session, dans l'ordre chronologique.

    Les matchs deja commences sont ecartes : les faire analyser reviendrait a
    demander un pari sur un resultat en partie connu.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, e.competition_id, se.note, "
            "       s.key AS sport_key, COALESCE(c.label, '—') AS competition, "
            "       c.oddsapi_key, c.surface "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time, e.id",
            (session_id,),
        ).fetchall()

    upcoming = [row for row in rows if not has_started(row["commence_time"], now)]
    # Ce que l'API a deja refuse de servir sur ces competitions. Une absence
    # constatee est une information : le bloc doit la porter, pas la taire.
    unserved = coverage.barren_by_competition(
        [row["competition_id"] for row in upcoming if row["competition_id"]], settings
    )

    with connect(settings) as conn:
        events: list[RenderableEvent] = []
        for index, row in enumerate(upcoming, start=1):
            odds = conn.execute(
                "SELECT bookmaker, market_key, outcome_name, description, point, price, "
                "       fetched_at FROM odds WHERE event_id = ? ORDER BY market_key, price",
                (int(row["id"]),),
            ).fetchall()

            markets: dict[str, list[Outcome]] = {}
            books: list[str] = []
            fetched: str | None = None
            for odd in odds:
                markets.setdefault(odd["market_key"], []).append(
                    Outcome(
                        name=odd["outcome_name"],
                        price=float(odd["price"]),
                        point=odd["point"],
                        description=odd["description"],
                        bookmaker=odd["bookmaker"],
                    )
                )
                # Un match peut melanger un releve d'API et une saisie a la main.
                # N'en annoncer qu'un seul attribuerait les cotes a la mauvaise
                # source ; l'horodatage ne vaut que pour la partie relevee.
                if odd["bookmaker"] not in books:
                    books.append(odd["bookmaker"])
                if odd["bookmaker"] in UNTIMED_BOOKMAKERS:
                    continue
                if fetched is None or odd["fetched_at"] > fetched:
                    fetched = odd["fetched_at"]

            primary = primary_book(books)
            events.append(
                RenderableEvent(
                    index=index,
                    sport_key=row["sport_key"],
                    competition=row["competition"],
                    home=row["home"],
                    away=row["away"],
                    commence_local=_local(row["commence_time"], settings.tz),
                    markets=markets,
                    context_lines=_context_for(row, settings),
                    note=row["note"] or None,
                    # L'en-tete ne nomme que la source principale : les autres
                    # sont portees ligne par ligne. Un en-tete « Betclic +
                    # Pinnacle (ref.) » laissait deviner quelle cote etait
                    # jouable et laquelle ne faisait que situer le marche.
                    bookmaker_label=bookmaker_label(primary) if primary else "—",
                    primary_book=primary,
                    unserved=_unserved_for(row, set(markets), primary, unserved),
                    substitute=_is_substitute(primary),
                    # `fetched` n'a ete alimente que par les bookmakers relevables :
                    # l'heure d'une saisie est celle de la frappe, pas celle d'un
                    # releve de marche, et l'afficher tromperait sur la fraicheur.
                    fetched_local=_local(fetched, settings.tz) if fetched else None,
                )
            )
    return events


def render_blocks(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> list[str]:
    """Blocs texte compacts de la session, prets pour le template de prompt."""
    return [render_event(event) for event in renderable_events(session_id, settings, now)]
