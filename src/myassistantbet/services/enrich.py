"""Etage B — marches profonds, match par match.

Chaque marche demande coute 1 credit (un seul bookmaker, donc une seule region).
Le cout est donc parfaitement previsible : il est estime avant l'appel, affiche
dans l'UI, et compare au plancher `ODDS_API_CREDIT_FLOOR` avant tout depart.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from ..providers.base import ProviderError, last_known_quota
from ..providers.oddsapi import PROVIDER, OddsAPIClient, expected_cost
from .scan import replace_odds  # meme regle de remplacement qu'a l'etage A

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

    @property
    def cost(self) -> int:
        return expected_cost(list(self.markets), ["betclic_fr"])


@dataclass
class Estimate:
    """Estimation affichee avant le clic sur « Enrichir la selection »."""

    targets: list[EnrichTarget] = field(default_factory=list)
    remaining: int | None = None
    floor: int = 500
    skipped: list[str] = field(default_factory=list)

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
        if not self.targets:
            return "Aucun evenement selectionne."
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

    @property
    def ok(self) -> bool:
        return self.error is None


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


def build_estimate(session_id: int, settings: Settings | None = None) -> Estimate:
    """Cout previsionnel de l'enrichissement d'une session, avant tout appel."""
    settings = settings or get_settings()
    estimate = Estimate(floor=settings.odds_api_credit_floor)

    quota = last_known_quota(PROVIDER, settings)
    if quota:
        estimate.remaining = quota["remaining"]

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.oddsapi_event_id, e.home, e.away, s.key AS sport_key, "
            "       c.oddsapi_key AS competition_key "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time",
            (session_id,),
        ).fetchall()

    for row in rows:
        label = f"{row['home']} – {row['away']}"
        if not row["oddsapi_event_id"] or not row["competition_key"]:
            # Evenement manuel (cyclisme, ATP 250) : aucun appel possible.
            estimate.skipped.append(label)
            continue
        markets = markets_for(row["sport_key"], row["competition_key"], settings)
        if not markets:
            estimate.skipped.append(label)
            continue
        estimate.targets.append(
            EnrichTarget(
                event_id=int(row["id"]),
                oddsapi_event_id=row["oddsapi_event_id"],
                sport_key=row["sport_key"],
                oddsapi_sport_key=row["competition_key"],
                label=label,
                markets=markets,
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


async def run_enrich(
    client: OddsAPIClient,
    session_id: int,
    settings: Settings | None = None,
    on_progress: Callable[[EnrichReport], None] | None = None,
) -> EnrichReport:
    """Enrichit tous les evenements d'une session.

    Le garde-fou de quota est verifie avant de partir. Un evenement en echec
    n'interrompt pas les suivants.
    """
    settings = settings or get_settings()
    estimate = build_estimate(session_id, settings)
    report = EnrichReport(total=estimate.events)

    if not estimate.allowed:
        report.finished = True
        reason = estimate.blocked_reason or "Enrichissement impossible."
        report.results.append(EnrichResult(label="—", error=reason))
        logger.warning("Enrichissement refuse : %s", reason)
        if on_progress:
            on_progress(report)
        return report

    for target in estimate.targets:
        result = EnrichResult(label=target.label)
        try:
            payload, cost = await client.get_event_odds(
                target.oddsapi_sport_key,
                target.oddsapi_event_id,
                markets=target.markets,
            )
            result.cost = cost
            result.markets_received, result.odds_rows = _store(target.event_id, payload, settings)
        except ProviderError as exc:
            result.error = str(exc)
            logger.warning("Enrichissement echoue pour %s : %s", target.label, exc)
        except Exception as exc:  # noqa: BLE001 — reponse inattendue, jamais fatale
            # Une reponse de forme imprevue ne doit pas tuer l'enrichissement des
            # autres matchs, ni mourir en silence dans une tache de fond.
            result.error = f"reponse inexploitable : {type(exc).__name__}: {exc}"
            logger.exception("Reponse inexploitable pour %s", target.label)

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
