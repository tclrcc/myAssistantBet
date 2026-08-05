"""Client The Odds API v4.

Regle de cout a respecter (SPEC.md section 4) : `cout = nb_marches x nb_regions`.
Le parametre `bookmakers` prime sur `regions` et chaque groupe de 10 bookmakers
compte pour une region : en n'interrogeant que `betclic_fr`, le cout vaut donc
exactement le nombre de marches demandes.

`/sports` et `/sports/{sport}/events` sont gratuits, et une reponse vide n'est
pas facturee.
"""

from __future__ import annotations

import logging
import math
from typing import Any

from .base import BaseHTTPClient, ProviderResponse, record_api_usage

logger = logging.getLogger(__name__)

PROVIDER = "oddsapi"
BASE_URL = "https://api.the-odds-api.com/v4"
DEFAULT_BOOKMAKER = "betclic_fr"
BOOKMAKERS_PER_REGION = 10

#: Marches de l'etage A : le strict necessaire pour afficher le board.
SCAN_MARKETS = ("h2h", "totals")


def expected_cost(
    markets: list[str] | tuple[str, ...], bookmakers: list[str] | tuple[str, ...]
) -> int:
    """Cout en credits d'un appel cotes, avant envoi.

    Un groupe entame de 10 bookmakers compte pour une region. Sans marche
    demande, l'appel ne coute rien.
    """
    if not markets:
        return 0
    regions = max(1, math.ceil(len(bookmakers) / BOOKMAKERS_PER_REGION)) if bookmakers else 1
    return len(markets) * regions


class OddsAPIClient(BaseHTTPClient):
    """Acces en lecture a The Odds API, avec comptabilisation du quota."""

    provider_name = PROVIDER
    base_url = BASE_URL

    def _params(self, **extra: Any) -> dict[str, Any]:
        return {"apiKey": self._settings.odds_api_key, **extra}

    def _account(self, endpoint: str, response: ProviderResponse, estimated_cost: int) -> int:
        """Trace la consommation reelle de l'appel et renvoie le cout retenu.

        Le header `x-requests-last` donne le cout facture par le fournisseur : il
        fait foi quand il est present, l'estimation locale ne sert que de repli.
        """
        if response.from_cache:
            logger.info("%s %s servi par le cache dev — cout 0", PROVIDER, endpoint)
            return 0

        headers = response.headers
        remaining = _as_int(headers.get("x-requests-remaining"))
        used = _as_int(headers.get("x-requests-used"))
        billed = _as_int(headers.get("x-requests-last"))
        cost = billed if billed is not None else estimated_cost

        record_api_usage(PROVIDER, endpoint, cost, remaining, self._settings)
        logger.info(
            "%s %s — cout %d (estime %d), restant %s, utilise %s, %d ms",
            PROVIDER,
            endpoint,
            cost,
            estimated_cost,
            remaining if remaining is not None else "?",
            used if used is not None else "?",
            response.duration_ms,
        )
        return cost

    async def get_sports(self, *, include_inactive: bool = False) -> list[dict[str, Any]]:
        """Liste des sports disponibles. Endpoint gratuit."""
        endpoint = "/sports"
        params = self._params()
        if include_inactive:
            params["all"] = "true"
        response = await self._get(endpoint, params=params)
        self._account(endpoint, response, estimated_cost=0)
        return response.data

    async def get_odds(
        self,
        sport_key: str,
        *,
        markets: tuple[str, ...] = SCAN_MARKETS,
        bookmakers: tuple[str, ...] = (DEFAULT_BOOKMAKER,),
        commence_time_to: str | None = None,
    ) -> tuple[list[dict[str, Any]], int]:
        """Etage A : cotes d'une competition entiere.

        Renvoie les evenements bruts et le cout reellement facture.
        """
        endpoint = f"/sports/{sport_key}/odds"
        params = self._params(
            bookmakers=",".join(bookmakers),
            markets=",".join(markets),
            oddsFormat="decimal",
            dateFormat="iso",
        )
        if commence_time_to:
            params["commenceTimeTo"] = commence_time_to

        response = await self._get(endpoint, params=params)
        cost = self._account(
            endpoint, response, estimated_cost=expected_cost(list(markets), list(bookmakers))
        )
        return response.data, cost

    async def get_event_odds(
        self,
        sport_key: str,
        event_id: str,
        *,
        markets: tuple[str, ...],
        bookmakers: tuple[str, ...] = (DEFAULT_BOOKMAKER,),
        regions: str = "",
    ) -> tuple[dict[str, Any], int]:
        """Etage B : marches profonds d'un match. Coute 1 credit par marche.

        Un marche absent pour ce match n'est pas une erreur : il manque
        simplement de la reponse.

        `regions` sert au sondage de couverture : il interroge tous les
        bookmakers d'une zone au lieu d'une liste nommee. Il coute le meme prix
        qu'une liste (1 region), mais ne doit pas servir a la collecte courante :
        on ne veut en base que les books sur lesquels on joue.
        """
        endpoint = f"/sports/{sport_key}/events/{event_id}/odds"
        extra = {"regions": regions} if regions else {"bookmakers": ",".join(bookmakers)}
        params = self._params(
            **extra,
            markets=",".join(markets),
            oddsFormat="decimal",
            dateFormat="iso",
        )
        response = await self._get(endpoint, params=params)
        cost = self._account(
            endpoint,
            response,
            estimated_cost=expected_cost(list(markets), [] if regions else list(bookmakers)),
        )
        return response.data, cost

    async def get_events(self, sport_key: str) -> list[dict[str, Any]]:
        """Evenements a venir d'une competition, sans cotes. Endpoint gratuit."""
        endpoint = f"/sports/{sport_key}/events"
        response = await self._get(endpoint, params=self._params(dateFormat="iso"))
        self._account(endpoint, response, estimated_cost=0)
        return response.data


def _as_int(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        return int(float(value))
    except ValueError:
        return None
