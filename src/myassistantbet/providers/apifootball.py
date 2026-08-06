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

#: Cles d'erreur d'enveloppe qui designent une saturation, donc un echec
#: passager : le meme appel aboutit une fois le compteur retombe.
TRANSIENT_ERROR_KEYS = frozenset({"ratelimit"})


def _envelope_errors(payload: Any) -> list[tuple[str, str]]:
    """Erreurs de l'enveloppe, en paires (cle, message).

    `errors` est tantot un dictionnaire, tantot une liste : les deux formes
    existent selon l'endpoint, et une seule des deux serait une source de bug.
    """
    if not isinstance(payload, dict):
        return []
    errors = payload.get("errors")
    if isinstance(errors, dict):
        return [(str(key), str(value)) for key, value in errors.items()]
    if isinstance(errors, list):
        return [("", str(item)) for item in errors]
    return []


class APIFootballClient(BaseHTTPClient):
    """Acces en lecture au contexte sportif : forme, classement, blessures, H2H."""

    provider_name = PROVIDER
    base_url = BASE_URL

    #: Le plafond est par minute : un backoff de une puis deux secondes ne
    #: laisserait pas le compteur retomber, et le retry ne servirait a rien.
    payload_retry_delay = 5.0

    def _transient_payload_error(self, data: Any) -> str | None:
        """Reconnait une saturation de debit, annoncee en HTTP 200.

        C'est le meme piege que les erreurs applicatives, avec une consequence
        differente : une cle invalide doit echouer tout de suite, un debit
        depasse doit etre retente. Sans cette distinction, un enrichissement
        d'une journee de championnat — quelques dizaines d'appels en rafale —
        laisse des trous definitifs dans le contexte la ou une seconde
        d'attente aurait suffi.
        """
        for key, message in _envelope_errors(data):
            if key.lower() in TRANSIENT_ERROR_KEYS or "too many requests" in message.lower():
                return f"debit depasse : {message}"
        return None

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

    async def _envelope(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any]:
        """Appelle un endpoint, comptabilise le quota et rend l'enveloppe validee."""
        result = await self._get(endpoint, params=params, headers=self._headers())
        self._account(endpoint, result)

        payload = result.data
        if not isinstance(payload, dict):
            raise ProviderError(
                PROVIDER, endpoint, f"enveloppe inattendue : {type(payload).__name__}"
            )

        errors = _envelope_errors(payload)
        if errors:
            detail = "; ".join(f"{key}: {value}" if key else value for key, value in errors)
            raise ProviderError(PROVIDER, endpoint, f"erreur applicative : {detail}")
        return payload

    async def _fetch(self, endpoint: str, params: dict[str, Any]) -> list[dict[str, Any]]:
        """Endpoint dont `response` est une liste — le cas courant."""
        response = (await self._envelope(endpoint, params)).get("response")
        return response if isinstance(response, list) else []

    async def _fetch_one(self, endpoint: str, params: dict[str, Any]) -> dict[str, Any] | None:
        """Endpoint dont `response` est un **objet** et non une liste.

        `/teams/statistics` est dans ce cas. Le lire avec `_fetch` renvoyait une
        liste vide, donc `None`, donc aucune ligne de forme : le bloc CONTEXTE
        n'a jamais porte « Forme 5 » ni « Dom/Ext », alors que leur rendu etait
        ecrit et teste. Une forme d'enveloppe non prevue ne fait pas de bruit,
        elle fait un trou.
        """
        response = (await self._envelope(endpoint, params)).get("response")
        return response if isinstance(response, dict) else None

    async def current_season(self, league_id: int) -> int:
        """Saison en cours d'une ligue, telle que le fournisseur la declare.

        La deduire de la date serait faux une fois sur deux : une Ligue 1 d'aout
        2026 est la saison 2026, mais un championnat qui se joue en annee civile
        (MLS, Bresil, Norvege) l'est aussi, et une Ligue 1 de fevrier 2027 est
        encore la saison 2026. Le fournisseur publie l'information, on la lit.
        """
        rows = await self._fetch("/leagues", {"id": league_id})
        for row in rows:
            for season in row.get("seasons") or []:
                if season.get("current"):
                    return int(season["year"])
        raise ProviderError(
            PROVIDER, "/leagues", f"aucune saison en cours pour la ligue {league_id}"
        )

    async def fixtures_by_date(
        self, date_iso: str, league_id: int, season: int
    ) -> list[dict[str, Any]]:
        """Matchs d'une ligue a une date donnee. Sert aussi a etablir le mapping.

        `season` n'est pas optionnel : interroger `/fixtures` avec une ligue mais
        sans saison fait repondre « season: The Season field is required », et
        l'absence de contexte qui en resultait passait pour un probleme de
        rapprochement de noms.
        """
        return await self._fetch(
            "/fixtures", {"date": date_iso, "league": league_id, "season": season}
        )

    async def team_statistics(
        self, league_id: int, season: int, team_id: int
    ) -> dict[str, Any] | None:
        """Forme et statistiques d'une equipe sur la saison.

        `response` est ici un objet, d'ou `_fetch_one` : le lire comme une liste
        renvoyait toujours `None`, et les lignes « Forme 5 » et « Dom/Ext »
        n'ont jamais ete rendues alors que leur code existait.
        """
        return await self._fetch_one(
            "/teams/statistics", {"league": league_id, "season": season, "team": team_id}
        )

    async def injuries(self, fixture_id: int) -> list[dict[str, Any]]:
        """Blesses et suspendus declares pour un match."""
        return await self._fetch("/injuries", {"fixture": fixture_id})

    async def fixture_statistics(self, fixture_id: int) -> list[dict[str, Any]]:
        """Statistiques d'un match joue : corners, cartons, tirs, possession.

        Un seul appel rend les deux equipes, ce qui donne du meme coup le
        « concede » : les corners d'une equipe sont ceux que l'autre a pris.
        """
        return await self._fetch("/fixtures/statistics", {"fixture": fixture_id})

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
