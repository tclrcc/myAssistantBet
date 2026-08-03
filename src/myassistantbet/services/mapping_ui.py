"""Vue de resolution manuelle des correspondances d'equipes.

Lit les evenements marques `mapping_pending` et les candidats memorises au
moment de l'echec, pour proposer un choix sans nouvel appel d'API.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from .context import KIND_MAPPING
from .matching import save_alias


@dataclass
class PendingTeam:
    """Un nom d'equipe a resoudre, et les candidats vus lors de la tentative."""

    oddsapi_name: str
    resolved: bool
    candidates: list[dict[str, Any]] = field(default_factory=list)


@dataclass
class PendingEvent:
    """Un evenement dont le mapping attend une decision humaine."""

    event_id: int
    home: str
    away: str
    competition: str
    commence_time: str
    reason: str = ""
    teams: list[PendingTeam] = field(default_factory=list)

    @property
    def affiche(self) -> str:
        # Le cyclisme n'a pas de second participant : pas de tiret orphelin.
        return f"{self.home} – {self.away}" if self.away else self.home

    @property
    def unresolved(self) -> list[PendingTeam]:
        return [team for team in self.teams if not team.resolved]


def pending_events(settings: Settings | None = None) -> list[PendingEvent]:
    """Evenements en attente de resolution, les plus proches d'abord."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, "
            "       COALESCE(c.label, '—') AS competition, ctx.payload_json "
            "FROM events e "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "LEFT JOIN context ctx ON ctx.event_id = e.id AND ctx.kind = ? "
            "WHERE e.mapping_pending = 1 "
            "ORDER BY e.commence_time",
            (KIND_MAPPING,),
        ).fetchall()

    events = []
    for row in rows:
        payload = json.loads(row["payload_json"]) if row["payload_json"] else {}
        events.append(
            PendingEvent(
                event_id=int(row["id"]),
                home=row["home"],
                away=row["away"],
                competition=row["competition"],
                commence_time=row["commence_time"],
                reason=payload.get("reason", ""),
                teams=[
                    PendingTeam(
                        oddsapi_name=team.get("oddsapi_name", ""),
                        resolved=bool(team.get("resolved")),
                        candidates=team.get("candidates") or [],
                    )
                    for team in payload.get("teams") or []
                ],
            )
        )
    return events


def pending_count(settings: Settings | None = None) -> int:
    with connect(settings) as conn:
        row = conn.execute("SELECT COUNT(*) AS n FROM events WHERE mapping_pending = 1").fetchone()
    return int(row["n"])


def resolve_manually(
    event_id: int,
    choices: dict[str, tuple[int, str]],
    settings: Settings | None = None,
) -> bool:
    """Enregistre les alias choisis a la main. Renvoie True si l'evenement est resolu.

    Un choix manuel est memorise pour toujours et prime sur toute deduction
    automatique ulterieure. L'evenement redevient enrichissable au prochain
    passage : c'est ce passage qui confirmera la correspondance du match.
    """
    settings = settings or get_settings()
    if not choices:
        return False

    for oddsapi_name, (apifootball_id, apifootball_name) in choices.items():
        save_alias(oddsapi_name, apifootball_id, apifootball_name, "manual", settings)

    remaining = [team for team in _teams_of(event_id, settings) if team.oddsapi_name not in choices]
    still_pending = any(not team.resolved for team in remaining)

    if not still_pending:
        with connect(settings) as conn:
            conn.execute("UPDATE events SET mapping_pending = 0 WHERE id = ?", (event_id,))
            conn.execute(
                "DELETE FROM context WHERE event_id = ? AND kind = ?", (event_id, KIND_MAPPING)
            )
    return not still_pending


def _teams_of(event_id: int, settings: Settings) -> list[PendingTeam]:
    for event in pending_events(settings):
        if event.event_id == event_id:
            return event.teams
    return []
