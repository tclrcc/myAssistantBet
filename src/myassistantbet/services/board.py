"""Lecture des donnees affichees par le Board, et gestion de la selection.

Ne fait aucun appel externe : uniquement des lectures et ecritures locales.
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.oddsapi import PROVIDER as ODDSAPI_PROVIDER
from .labels import affiche, sort_key, sport_emoji
from .mapping_ui import pending_count
from .scan import scan_window
from .session import has_started

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
        return affiche(self.home, self.away)

    @property
    def sport_emoji(self) -> str:
        return sport_emoji(self.sport_key)

    @property
    def has_odds(self) -> bool:
        return self.home_price is not None or self.total_point is not None


@dataclass
class Banner:
    """Bandeau permanent : quota, dernier scan, taille de la selection."""

    credits_remaining: int | None = None
    last_scan_at: datetime | None = None
    #: Matchs coches encore a venir : ceux qui iront dans le prompt.
    selected_count: int = 0
    credit_floor: int = 500
    session_id: int | None = None
    mapping_pending: int = 0
    #: Matchs coches qui ont commence depuis. Affiches a part, jamais comptes
    #: avec les autres : ils ne seront ni enrichis ni analyses.
    started_count: int = 0

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


def toggle_filtered(
    filters: Filters | None = None,
    selected: bool = True,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Coche ou decoche tous les evenements du filtre courant. Renvoie le nombre touche.

    Porte sur ce que le filtre laisse voir, jamais sur toute la base : selectionner
    en masse ne doit pas embarquer des matchs que l'utilisateur n'a pas sous les yeux.
    """
    settings = settings or get_settings()
    session_id = current_session(settings)
    rows = list_rows(filters, settings, now)
    if not rows:
        return 0

    ids = [row.event_id for row in rows]
    placeholders = ",".join("?" * len(ids))
    with connect(settings) as conn:
        if selected:
            conn.executemany(
                "INSERT INTO session_events (session_id, event_id) VALUES (?, ?) "
                "ON CONFLICT(session_id, event_id) DO NOTHING",
                [(session_id, event_id) for event_id in ids],
            )
        else:
            conn.execute(
                f"DELETE FROM session_events WHERE session_id = ? AND event_id IN ({placeholders})",
                [session_id, *ids],
            )
    return len(ids)


def selected_event_ids(settings: Settings | None = None) -> set[int]:
    settings = settings or get_settings()
    session_id = current_session(settings)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT event_id FROM session_events WHERE session_id = ?", (session_id,)
        ).fetchall()
    return {int(row["event_id"]) for row in rows}


def selection_counts(
    settings: Settings | None = None,
    now: datetime | None = None,
) -> tuple[int, int]:
    """Selection courante : (matchs a venir, matchs deja commences)."""
    settings = settings or get_settings()
    session_id = current_session(settings)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.commence_time FROM session_events se "
            "JOIN events e ON e.id = se.event_id WHERE se.session_id = ?",
            (session_id,),
        ).fetchall()
    started = sum(1 for row in rows if has_started(row["commence_time"], now))
    return len(rows) - started, started


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


def filter_options(
    settings: Settings | None = None, sport: str = ""
) -> dict[str, list[dict[str, Any]]]:
    """Valeurs proposees dans les listes deroulantes de filtre.

    Un sport choisi restreint la liste des competitions : proposer les ligues de
    football quand on regarde le tennis n'offre que des filtres qui vident le
    board sans rien expliquer.

    Les competitions sont **groupees par sport puis triees alphabetiquement**.
    L'ordre par priorite decroissante servait le scan, pas la lecture : dans la
    liste il melangeait les sports et ne laissait aucun moyen de deviner ou
    chercher « ATP Canadian Open » parmi cent entrees. La priorite continue de
    trier les lignes du board, ou elle a un sens.
    """
    with connect(settings) as conn:
        sports = [
            {**dict(row), "emoji": sport_emoji(row["key"])}
            for row in conn.execute("SELECT key, label FROM sports ORDER BY id").fetchall()
        ]
        competitions = [
            dict(row)
            for row in conn.execute(
                "SELECT c.id, c.label, s.key AS sport_key, s.label AS sport_label "
                "FROM competitions c JOIN sports s ON s.id = c.sport_id WHERE c.active = 1 "
                "ORDER BY s.id"
            ).fetchall()
        ]
    if sport:
        competitions = [row for row in competitions if row["sport_key"] == sport]

    order = {row["key"]: index for index, row in enumerate(sports)}
    competitions.sort(key=lambda row: (order.get(row["sport_key"], 99), sort_key(row["label"])))
    return {
        "sports": sports,
        "competitions": competitions,
        "competition_groups": _by_sport(competitions),
    }


def _by_sport(competitions: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Competitions regroupees par sport, dans l'ordre ou elles arrivent.

    Le groupement est fait ici et non dans le template : le filtre `groupby` de
    Jinja retrie par ordre alphabetique, ce qui remettrait le tennis avant le
    football et defaireait l'ordre voulu.
    """
    groups: list[dict[str, Any]] = []
    for row in competitions:
        if not groups or groups[-1]["label"] != row["sport_label"]:
            groups.append(
                {"key": row["sport_key"], "label": row["sport_label"], "competitions": []}
            )
        groups[-1]["competitions"].append(row)
    return groups


def coherent(filters: Filters, options: dict[str, list[dict[str, Any]]]) -> Filters:
    """Retire un filtre de competition qui n'appartient pas au sport choisi.

    Sans cela, changer de sport laisse en place une competition devenue
    invisible dans la liste, et le board se vide sans motif affiche.
    """
    if filters.competition_id is None:
        return filters
    known = {row["id"] for row in options["competitions"]}
    if filters.competition_id in known:
        return filters
    return replace(filters, competition_id=None)


def banner(settings: Settings | None = None, now: datetime | None = None) -> Banner:
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
    state.selected_count, state.started_count = selection_counts(settings, now)
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
    options = filter_options(settings, filters.sport)
    filters = coherent(filters, options)
    return BoardView(
        rows=list_rows(filters, settings, now),
        banner=banner(settings, now),
        options=options,
        filters=filters,
    )
