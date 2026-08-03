"""Client API-Football v3 (api-sports.io).

Particularite du fournisseur : les erreurs applicatives arrivent en HTTP 200,
dans le champ `errors` de l'enveloppe. Elles sont converties ici en
`ProviderError` pour que le metier les traite comme n'importe quelle panne —
et les affiche comme « donnee non disponible » plutot que de les taire.
"""

from __future__ import annotations

import logging
from typing import Any

from .base import BaseHTTPClient, ProviderError, ProviderResponse, record_api_usage

logger = logging.getLogger(__name__)

PROVIDER = "apifootball"
BASE_URL = "https://v3.football.api-sports.io"

#: Chaque appel compte pour une unite du quota journalier.
CALL_COST = 1


class APIFootballClient(BaseHTTPClient):
    """Acces en lecture au contexte sportif : forme, classement, blessures, H2H."""

    provider_name = PROVIDER
    base_url = BASE_URL

    def _headers(self) -> dict[str, str]:
        return {"x-apisports-key": self._settings.apifootball_key}

    def _account(self, endpoint: str, response: ProviderResponse) -> None:
        if response.from_cache:
            logger.info("%s %s servi par le cache dev", PROVIDER, endpoint)
            return
        remaining = _as_int(response.headers.get("x-ratelimit-requests-remaining"))
        record_api_usage(PROVIDER, endpoint, CALL_COST, remaining, self._settings)
        logger.info(
            "%s %s — cout %d, restant %s, %d ms",
            PROVIDER,
            endpoint,
            CALL_COST,
            remaining if remaining is not None else "?",
            response.duration_ms,
        )

    async def _fetch(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Appelle un endpoint et renvoie la liste `response` de l'enveloppe."""
        result = await self._get(endpoint, params=params, headers=self._headers())
        self._account(endpoint, result)

        payload = result.data
        if not isinstance(payload, dict):
            raise ProviderError(
                PROVIDER, endpoint, f"enveloppe inattendue : {type(payload).__name__}"
            )

        errors = payload.get("errors")
        if errors:
            # `errors` est tantot une liste, tantot un dictionnaire.
            detail = (
                "; ".join(f"{key}: {value}" for key, value in errors.items())
                if isinstance(errors, dict)
                else "; ".join(str(item) for item in errors)
            )
            raise ProviderError(PROVIDER, endpoint, f"erreur applicative : {detail}")

        response = payload.get("response")
        return response if isinstance(response, list) else []

    async def fixtures_by_date(self, date_iso: str, league_id: int) -> list[dict[str, Any]]:
        """Matchs d'une ligue a une date donnee. Sert aussi a etablir le mapping."""
        return await self._fetch("/fixtures", {"date": date_iso, "league": league_id})

    async def team_statistics(
        self, league_id: int, season: int, team_id: int
    ) -> dict[str, Any] | None:
        """Forme et statistiques d'une equipe sur la saison."""
        rows = await self._fetch(
            "/teams/statistics", {"league": league_id, "season": season, "team": team_id}
        )
        # Cet endpoint renvoie un objet, que l'enveloppe encapsule differemment
        # selon les versions : on tolere les deux formes.
        if rows:
            return rows[0] if isinstance(rows[0], dict) else None
        return None

    async def injuries(self, fixture_id: int) -> list[dict[str, Any]]:
        """Blesses et suspendus declares pour un match."""
        return await self._fetch("/injuries", {"fixture": fixture_id})

    async def head_to_head(self, home_id: int, away_id: int, last: int = 5) -> list[dict[str, Any]]:
        """Confrontations directes recentes."""
        return await self._fetch(
            "/fixtures/headtohead", {"h2h": f"{home_id}-{away_id}", "last": last}
        )

    async def standings(self, league_id: int, season: int) -> list[dict[str, Any]]:
        """Classement d'une ligue."""
        return await self._fetch("/standings", {"league": league_id, "season": season})

    async def last_fixtures(self, team_id: int, last: int = 5) -> list[dict[str, Any]]:
        """Derniers matchs joues par une equipe, avec scores et dates."""
        return await self._fetch("/fixtures", {"team": team_id, "last": last})

    async def lineups(self, fixture_id: int) -> list[dict[str, Any]]:
        """Compositions. Souvent indisponibles jusqu'a une heure du coup d'envoi."""
        return await self._fetch("/fixtures/lineups", {"fixture": fixture_id})


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None
