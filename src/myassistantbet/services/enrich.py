"""Etage B — marches profonds, match par match.

Chaque marche demande coute 1 credit (un seul bookmaker, donc une seule region).
Le cout est donc parfaitement previsible : il est estime avant l'appel, affiche
dans l'UI, et compare au plancher `ODDS_API_CREDIT_FLOOR` avant tout depart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from ..providers.apifootball import APIFootballClient
from ..providers.base import ProviderError, last_known_quota
from ..providers.oddsapi import DEFAULT_BOOKMAKER, PROVIDER, OddsAPIClient, expected_cost
from ..providers.tennisabstract import TennisAbstractClient
from . import coverage, elo, reference
from .context import fetch_context
from .labels import affiche
from .scan import replace_odds  # meme regle de remplacement qu'a l'etage A
from .session import has_started

logger = logging.getLogger(__name__)

#: Marches profonds football (SPEC.md section 4).
FOOTBALL_MARKETS: tuple[str, ...] = (
    "correct_score",
    "correct_score_h1",
    "totals_h1",
    "alternate_totals",
    "btts",
    "btts_h1",
    "double_chance",
    "halftime_fulltime",
    "team_totals",
    "alternate_team_totals",
    "alternate_totals_corners",
    "alternate_totals_cards",
    "corners_1x2",
    "alternate_spreads",
)

#: Marches profonds tennis (SPEC.md section 4).
TENNIS_MARKETS: tuple[str, ...] = (
    "h2h",
    "spreads",
    "totals",
    "h2h_s1",
    "h2h_s2",
    "spreads_s1",
    "totals_s1",
    "alternate_totals_s1",
)

#: Props buteurs : servies uniquement sur quelques competitions (liste blanche).
PLAYER_PROP_MARKETS: tuple[str, ...] = (
    "player_goal_scorer_anytime",
    "player_first_goal_scorer",
)


@dataclass
class EnrichTarget:
    """Un evenement a enrichir, et le detail de ce qu'il va couter."""

    event_id: int
    oddsapi_event_id: str
    sport_key: str
    oddsapi_sport_key: str
    label: str
    markets: tuple[str, ...]
    #: Competition d'appartenance, pour memoriser les marches qu'elle sert.
    competition_id: int | None = None
    #: Necessaire au contexte API-Football. Absent = pas de contexte possible.
    apifootball_league_id: int | None = None
    home: str = ""
    away: str = ""
    commence_time: str = ""

    #: Books interroges pour cet evenement. Jusqu'a dix, le cout est le meme.
    bookmakers: tuple[str, ...] = (DEFAULT_BOOKMAKER,)

    @property
    def cost(self) -> int:
        return expected_cost(list(self.markets), list(self.bookmakers))

    @property
    def context_possible(self) -> bool:
        return self.sport_key == "football" and self.apifootball_league_id is not None

    def as_event(self) -> dict[str, Any]:
        """Vue attendue par `services.context`."""
        return {
            "id": self.event_id,
            "home": self.home,
            "away": self.away,
            "commence_time": self.commence_time,
            "apifootball_league_id": self.apifootball_league_id,
        }


@dataclass
class Estimate:
    """Estimation affichee avant le clic sur « Enrichir la selection »."""

    targets: list[EnrichTarget] = field(default_factory=list)
    remaining: int | None = None
    floor: int = 500
    skipped: list[str] = field(default_factory=list)
    #: Evenements deja commences. Payer leurs marches profonds serait acheter
    #: des cotes qu'aucun pari avant-match ne peut plus utiliser.
    started: list[str] = field(default_factory=list)
    #: Evenements ecartes parce que le bookmaker n'a jamais servi le moindre
    #: marche profond sur leur competition. Distinct de `skipped` : ceux-la
    #: sont couverts par l'API, il n'y a simplement rien de plus a acheter.
    barren: list[str] = field(default_factory=list)
    #: Evenements de la shortlist, quel que soit leur sort. Sans ce compteur,
    #: « rien a enrichir » et « rien de coche » seraient indiscernables.
    considered: int = 0

    @property
    def events(self) -> int:
        return len(self.targets)

    @property
    def cost(self) -> int:
        return sum(target.cost for target in self.targets)

    @property
    def remaining_after(self) -> int | None:
        return None if self.remaining is None else self.remaining - self.cost

    @property
    def allowed(self) -> bool:
        if not self.targets:
            return False
        if self.remaining is None:
            # Quota inconnu : on n'a jamais appele l'API. On laisse partir, le
            # plancher sera verifiable des le premier appel.
            return True
        return self.remaining_after is not None and self.remaining_after >= self.floor

    @property
    def blocked_reason(self) -> str | None:
        """Pourquoi le bouton est inactif. Jamais un motif faux : dire « rien de
        coche » alors que des matchs le sont enverrait chercher le probleme au
        mauvais endroit."""
        if not self.targets:
            if not self.considered:
                return "Aucun evenement selectionne."
            if self.started and not self.skipped and not self.barren:
                return "Rien a enrichir : tous les matchs selectionnes ont deja commence."
            if self.barren and not self.skipped:
                return (
                    "Rien a enrichir : le bookmaker ne sert aucun marche profond "
                    "sur ces competitions. Passe par la saisie groupee."
                )
            if self.skipped and not self.barren:
                return "Rien a enrichir : aucun de ces evenements n'est servi par l'API."
            return "Rien a enrichir sur cette selection."
        if self.allowed:
            return None
        return (
            f"Enrichissement bloque : {self.cost} credits necessaires, "
            f"il resterait {self.remaining_after} sous le plancher de {self.floor}."
        )


@dataclass
class EnrichResult:
    """Resultat de l'enrichissement d'un evenement."""

    label: str
    markets_received: int = 0
    odds_rows: int = 0
    cost: int = 0
    error: str | None = None
    context_kinds: list[str] = field(default_factory=list)
    context_errors: list[str] = field(default_factory=list)
    mapping_pending: bool = False
    #: Marches obtenus d'un book de reference : {marche: book}. Ils disent ou
    #: se situe le marche, pas le prix qu'on obtiendra chez le principal.
    borrowed: dict[str, str] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def context_note(self) -> str:
        """Ce qui manque cote contexte, formule pour l'UI. Vide si tout va bien."""
        if self.mapping_pending:
            return "équipes non identifiées côté API-Football — résolution manuelle requise"
        if self.context_errors:
            return "contexte partiel : " + " ; ".join(self.context_errors)
        return ""


@dataclass
class EnrichReport:
    """Bilan complet, et progression pendant l'execution."""

    total: int = 0
    done: int = 0
    results: list[EnrichResult] = field(default_factory=list)
    finished: bool = False

    @property
    def cost(self) -> int:
        return sum(result.cost for result in self.results)

    @property
    def failures(self) -> list[EnrichResult]:
        return [result for result in self.results if not result.ok]

    @property
    def context_notes(self) -> list[EnrichResult]:
        """Matchs dont le contexte est incomplet, a signaler visiblement dans l'UI."""
        return [result for result in self.results if result.context_note]

    @property
    def percent(self) -> int:
        return 100 if self.finished else int(100 * self.done / self.total) if self.total else 0


def markets_for(sport_key: str, oddsapi_sport_key: str, settings: Settings) -> tuple[str, ...]:
    """Marches a demander pour cet evenement, props incluses si la ligue y donne droit."""
    if sport_key == "tennis":
        return TENNIS_MARKETS
    if sport_key != "football":
        return ()
    if oddsapi_sport_key in settings.player_props_whitelist:
        return FOOTBALL_MARKETS + PLAYER_PROP_MARKETS
    return FOOTBALL_MARKETS


def build_estimate(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Estimate:
    """Cout previsionnel de l'enrichissement d'une session, avant tout appel."""
    settings = settings or get_settings()
    estimate = Estimate(floor=settings.odds_api_credit_floor)

    quota = last_known_quota(PROVIDER, settings)
    if quota:
        estimate.remaining = quota["remaining"]

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.oddsapi_event_id, e.home, e.away, e.commence_time, "
            "       s.key AS sport_key, c.oddsapi_key AS competition_key, "
            "       c.id AS competition_id, c.apifootball_league_id "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time",
            (session_id,),
        ).fetchall()

    estimate.considered = len(rows)
    for row in rows:
        label = affiche(row["home"], row["away"])
        if has_started(row["commence_time"], now):
            estimate.started.append(label)
            continue
        if not row["oddsapi_event_id"] or not row["competition_key"]:
            # Evenement manuel (cyclisme, ATP 250) : aucun appel possible.
            estimate.skipped.append(label)
            continue
        markets = markets_for(row["sport_key"], row["competition_key"], settings)
        if not markets:
            estimate.skipped.append(label)
            continue
        # Ne pas repayer un constat deja fait : les marches que cette competition
        # n'a jamais servis sont retires, et si plus rien d'utile ne reste,
        # l'evenement est ecarte plutot qu'appele pour rien.
        markets = coverage.useful(row["competition_id"], markets, settings)
        if not markets:
            estimate.barren.append(label)
            continue
        estimate.targets.append(
            EnrichTarget(
                event_id=int(row["id"]),
                oddsapi_event_id=row["oddsapi_event_id"],
                sport_key=row["sport_key"],
                oddsapi_sport_key=row["competition_key"],
                label=label,
                markets=markets,
                bookmakers=(DEFAULT_BOOKMAKER, *settings.reference_books),
                competition_id=row["competition_id"],
                apifootball_league_id=row["apifootball_league_id"],
                home=row["home"],
                away=row["away"],
                commence_time=row["commence_time"],
            )
        )
    return estimate


def _store(event_id: int, payload: dict[str, Any], settings: Settings) -> tuple[int, int]:
    """Ecrit les cotes profondes. Renvoie (marches recus, lignes inserees)."""
    markets = sum(
        len(bookmaker.get("markets") or []) for bookmaker in payload.get("bookmakers") or []
    )
    with connect(settings) as conn:
        rows = replace_odds(conn, event_id, payload)
    return markets, rows


async def _add_context(
    context_client: APIFootballClient,
    target: EnrichTarget,
    result: EnrichResult,
    settings: Settings,
    cache: dict[str, Any],
) -> None:
    """Ajoute le contexte sportif. Un echec ici ne remet jamais en cause les cotes."""
    try:
        report = await fetch_context(context_client, target.as_event(), settings, cache)
    except Exception as exc:  # noqa: BLE001 — le contexte est un bonus, jamais bloquant
        result.context_errors.append(f"{type(exc).__name__}: {exc}")
        logger.exception("Contexte indisponible pour %s", target.label)
        return
    result.context_kinds = report.kinds
    result.context_errors = report.errors
    result.mapping_pending = report.mapping_pending


def has_tennis(session_id: int, settings: Settings) -> bool:
    """Vrai si la session contient au moins un match de tennis.

    Porte sur toute la session et non sur les seules cibles payantes : un match
    dont plus aucun marche n'est a acheter garde son bloc, et son Elo l'interesse
    autant que les autres.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT 1 FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "WHERE se.session_id = ? AND s.key = 'tennis' LIMIT 1",
            (session_id,),
        ).fetchone()
    return row is not None


async def _refresh_elo(
    elo_client: TennisAbstractClient,
    session_id: int,
    settings: Settings,
    now: datetime | None,
) -> None:
    """Met a jour les classements Elo si la session en a l'usage.

    Gratuit et sans quota : aucun garde-fou de credit ne s'applique. Un echec
    n'interrompt rien — le bloc CONTEXTE perdra sa ligne Elo, comme avant.
    """
    if not has_tennis(session_id, settings):
        return
    try:
        await elo.refresh(elo_client, settings, now=now)
    except Exception as exc:  # noqa: BLE001 — l'Elo est un bonus, jamais bloquant
        logger.exception("Rafraichissement Elo impossible : %s", exc)


async def run_enrich(
    client: OddsAPIClient,
    session_id: int,
    settings: Settings | None = None,
    on_progress: Callable[[EnrichReport], None] | None = None,
    context_client: APIFootballClient | None = None,
    now: datetime | None = None,
    elo_client: TennisAbstractClient | None = None,
) -> EnrichReport:
    """Enrichit tous les evenements d'une session : marches profonds puis contexte.

    Le garde-fou de quota est verifie avant de partir. Un evenement en echec
    n'interrompt pas les suivants, et un contexte manquant n'empeche jamais les
    cotes d'etre recuperees.
    """
    settings = settings or get_settings()
    # Avant tout garde-fou de credit : l'Elo est gratuit, et c'est justement
    # quand il n'y a plus un seul marche a acheter qu'il faut le recuperer.
    if elo_client is not None:
        await _refresh_elo(elo_client, session_id, settings, now)

    estimate = build_estimate(session_id, settings, now)
    report = EnrichReport(total=estimate.events)

    if not estimate.allowed:
        report.finished = True
        reason = estimate.blocked_reason or "Enrichissement impossible."
        report.results.append(EnrichResult(label="—", error=reason))
        logger.warning("Enrichissement refuse : %s", reason)
        if on_progress:
            on_progress(report)
        return report

    # Classements et statistiques d'equipe sont partages entre les matchs d'une
    # meme ligue : on ne les paie qu'une fois par enrichissement.
    context_cache: dict[str, Any] = {}

    for target in estimate.targets:
        result = EnrichResult(label=target.label)
        try:
            payload, cost = await client.get_event_odds(
                target.oddsapi_sport_key,
                target.oddsapi_event_id,
                markets=target.markets,
                bookmakers=target.bookmakers,
            )
            result.cost = cost
            # Une seule source par marche : le principal d'abord, un book de
            # reference seulement pour ce qu'il ne sert pas.
            payload = reference.merge(payload, DEFAULT_BOOKMAKER, settings.reference_books)
            result.borrowed = reference.borrowed_markets(payload, DEFAULT_BOOKMAKER)
            result.markets_received, result.odds_rows = _store(target.event_id, payload, settings)
            # Le constat ne vaut que pour les books qui l'ont produit : les
            # memoriser avec lui, sinon elargir la liste ne rouvrirait rien.
            coverage.record(
                target.competition_id, target.markets, payload, settings, target.bookmakers
            )
        except ProviderError as exc:
            result.error = str(exc)
            logger.warning("Enrichissement echoue pour %s : %s", target.label, exc)
        except Exception as exc:  # noqa: BLE001 — reponse inattendue, jamais fatale
            # Une reponse de forme imprevue ne doit pas tuer l'enrichissement des
            # autres matchs, ni mourir en silence dans une tache de fond.
            result.error = f"reponse inexploitable : {type(exc).__name__}: {exc}"
            logger.exception("Reponse inexploitable pour %s", target.label)

        if context_client is not None and target.context_possible:
            await _add_context(context_client, target, result, settings, context_cache)

        report.results.append(result)
        report.done += 1
        if on_progress:
            on_progress(report)

    report.finished = True
    logger.info(
        "Enrichissement termine : %d/%d evenements, cout %d credits, %d echec(s)",
        report.done - len(report.failures),
        report.total,
        report.cost,
        len(report.failures),
    )
    if on_progress:
        on_progress(report)
    return report
