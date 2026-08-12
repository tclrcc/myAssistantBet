"""Fiche d'un evenement : toutes ses cotes, sans rien tronquer.

`render.py` sert l'analyse : il fusionne, limite et hierarchise pour tenir dans
un prompt. Cette vue-ci sert la verification humaine : elle montre tout ce que
la base contient pour un evenement, y compris les marches qu'aucun rendu dedie
ne connait. Aucun appel externe : c'est une lecture locale, gratuite.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect
from .labels import UNTIMED_BOOKMAKERS, affiche, bookmaker_label, is_reference, primary_book
from .render import MARKET_ORDER, MARKET_ORDER_BY_SPORT

#: Marches releves par l'etage A : leur seule presence signale un evenement
#: pas encore enrichi.
SCAN_ONLY_MARKETS = frozenset({"h2h", "totals"})


@dataclass
class OutcomeRow:
    """Une issue, telle qu'elle est stockee."""

    name: str
    price: float
    point: float | None = None
    description: str | None = None

    @property
    def full_name(self) -> str:
        """`Haaland` pour une prop buteur, `Over 2.5` pour un total."""
        parts = [self.description or self.name]
        if self.description:
            parts.append(f"({self.name})")
        return " ".join(parts)


@dataclass
class MarketBlock:
    """Un marche et ses issues, dans l'ordre du rendu compact."""

    key: str
    label: str
    outcomes: list[OutcomeRow] = field(default_factory=list)
    #: Vrai si aucun rendu dedie ne connait ce marche : il est montre brut
    #: plutot que perdu silencieusement.
    unmodelled: bool = False
    #: Books autres que le principal ayant servi ce marche. Verifier un prix
    #: suppose de savoir chez qui il a ete releve.
    others: list[str] = field(default_factory=list)

    @property
    def source_note(self) -> str:
        """Sources a mentionner, vide quand le marche vient du book principal."""
        return " + ".join(bookmaker_label(book) for book in self.others)

    @property
    def count(self) -> int:
        return len(self.outcomes)

    @property
    def deep(self) -> bool:
        return self.key not in SCAN_ONLY_MARKETS


@dataclass
class EventOdds:
    """Tout ce qu'il faut pour afficher la fiche d'un evenement."""

    event_id: int
    home: str
    away: str
    sport_key: str
    sport_label: str
    competition: str
    local_time: datetime
    #: Heure de coup d'envoi en UTC, telle que stockee. Le contexte la relit
    #: pour calculer les jours de repos : l'heure locale d'affichage ne le
    #: permettrait pas sans reconversion.
    commence_utc: str = ""
    selected: bool = False
    source: str = ""
    #: Match API-Football rattache. Sans lui, ni contexte ni cotes de
    #: substitution : la fiche le dit plutot que de proposer un bouton inerte.
    apifootball_fixture_id: int | None = None
    #: De quoi assembler le bloc CONTEXTE comme le fait le prompt : la
    #: competition porte la surface et sa correspondance de tournoi, et sa cle
    #: The Odds API sert au rapprochement Elo.
    competition_id: int | None = None
    oddsapi_key: str | None = None
    surface: str | None = None
    blocks: list[MarketBlock] = field(default_factory=list)
    bookmakers: list[str] = field(default_factory=list)
    #: Cle de la source principale, celle dont les prix seraient jouables. Le
    #: champ `bookmakers` porte des libelles, bons a lire et impropres a tester.
    primary: str = ""
    fetched_local: datetime | None = None
    note: str = ""
    #: Ce qui empeche cette rencontre programmee d'avoir ete disputee, quand on
    #: le sait. Vide est le cas ordinaire, et ne veut pas dire « disputee » : il
    #: dit que rien, dans ce que nous savons, ne s'y oppose.
    match_outcome_type: str = ""

    @property
    def affiche(self) -> str:
        return affiche(self.home, self.away)

    @property
    def market_count(self) -> int:
        return len(self.blocks)

    @property
    def outcome_count(self) -> int:
        return sum(block.count for block in self.blocks)

    @property
    def deep_market_count(self) -> int:
        return sum(1 for block in self.blocks if block.deep)

    @property
    def enriched(self) -> bool:
        return self.deep_market_count > 0

    @property
    def is_empty(self) -> bool:
        return not self.blocks

    @property
    def reference_only(self) -> bool:
        """Des cotes, mais aucune du book principal — donc aucune jouable.

        C'est l'etat d'un match dont la competition n'est pas servie par
        Betclic : les tours preliminaires de coupe d'Europe n'en ont que le 1N2,
        releve chez un book de reference. Il ouvre le releve de substitution au
        meme titre qu'un match sans aucune cote — l'objection d'origine, « il
        n'ajouterait qu'un prix non jouable a cote d'un prix jouable », ne tient
        pas quand il n'y a aucun prix jouable.
        """
        return bool(self.blocks) and is_reference(self.primary)


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _ordered_blocks(sport_key: str, grouped: dict[str, list[OutcomeRow]]) -> list[MarketBlock]:
    """Ordonne les marches comme le rendu compact, puis ajoute les inconnus.

    Un marche paye mais non modelise est rendu brut plutot que perdu : c'est la
    meme regle que `render.py`, et pour la meme raison.
    """
    order = MARKET_ORDER_BY_SPORT.get(sport_key, MARKET_ORDER)
    blocks: list[MarketBlock] = []
    placed: set[str] = set()

    for key, label in order:
        if key in placed or key not in grouped:
            continue
        placed.add(key)
        blocks.append(MarketBlock(key=key, label=label, outcomes=grouped[key]))

    for key in sorted(set(grouped) - placed):
        blocks.append(MarketBlock(key=key, label=key, outcomes=grouped[key], unmodelled=True))

    return blocks


def _sorted_outcomes(outcomes: list[OutcomeRow]) -> list[OutcomeRow]:
    """Les marches a ligne se lisent par ligne croissante ; les autres gardent
    l'ordre du fournisseur, qui porte un sens (domicile, nul, exterieur)."""
    if any(outcome.point is not None for outcome in outcomes):
        return sorted(outcomes, key=lambda o: (o.point if o.point is not None else 0.0, o.name))
    return outcomes


def build(event_id: int, settings: Settings | None = None) -> EventOdds | None:
    """Fiche complete d'un evenement, ou None s'il n'existe pas."""
    settings = settings or get_settings()

    with connect(settings) as conn:
        row = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, e.source, e.apifootball_fixture_id, "
            "       s.key AS sport_key, s.label AS sport_label, "
            "       COALESCE(c.label, '—') AS competition, "
            "       e.competition_id, c.oddsapi_key, c.surface, e.match_outcome_type "
            "FROM events e "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE e.id = ?",
            (event_id,),
        ).fetchone()
        if row is None:
            return None

        odds = conn.execute(
            "SELECT bookmaker, market_key, outcome_name, description, point, price, fetched_at "
            "FROM odds WHERE event_id = ? ORDER BY id",
            (event_id,),
        ).fetchall()

        selection = conn.execute(
            "SELECT 1 FROM session_events WHERE event_id = ? LIMIT 1", (event_id,)
        ).fetchone()
        note_row = conn.execute(
            "SELECT note FROM session_events WHERE event_id = ? AND note IS NOT NULL LIMIT 1",
            (event_id,),
        ).fetchone()

    grouped: dict[str, list[OutcomeRow]] = {}
    sources: dict[str, list[str]] = {}
    books: list[str] = []
    fetched: str | None = None
    timed = False
    for odd in odds:
        grouped.setdefault(odd["market_key"], []).append(
            OutcomeRow(
                name=odd["outcome_name"],
                price=float(odd["price"]),
                point=odd["point"],
                description=odd["description"],
            )
        )
        book = odd["bookmaker"]
        if book not in books:
            books.append(book)
        if book not in sources.setdefault(odd["market_key"], []):
            sources[odd["market_key"]].append(book)
        if book not in UNTIMED_BOOKMAKERS:
            timed = True
            if fetched is None or odd["fetched_at"] > fetched:
                fetched = odd["fetched_at"]

    primary = primary_book(books)
    blocks = _ordered_blocks(row["sport_key"], grouped)
    for block in blocks:
        block.outcomes = _sorted_outcomes(block.outcomes)
        block.others = [book for book in sources.get(block.key, []) if book != primary]

    return EventOdds(
        event_id=int(row["id"]),
        home=row["home"],
        away=row["away"] or "",
        sport_key=row["sport_key"],
        sport_label=row["sport_label"],
        competition=row["competition"],
        local_time=_local(row["commence_time"], settings.tz),
        commence_utc=row["commence_time"],
        selected=selection is not None,
        source=row["source"] or "",
        apifootball_fixture_id=row["apifootball_fixture_id"],
        competition_id=row["competition_id"],
        oddsapi_key=row["oddsapi_key"],
        surface=row["surface"],
        blocks=blocks,
        bookmakers=[bookmaker_label(book) for book in books],
        primary=primary,
        # Une cote saisie a la main porte l'heure de la frappe, pas celle d'un
        # releve de marche : l'afficher tromperait sur la fraicheur.
        fetched_local=_local(fetched, settings.tz) if fetched and timed else None,
        note=(note_row["note"] if note_row else "") or "",
        match_outcome_type=row["match_outcome_type"] or "",
    )
