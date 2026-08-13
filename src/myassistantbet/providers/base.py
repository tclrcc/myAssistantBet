"""Socle commun aux clients d'APIs externes.

Contient tout ce qui n'est pas specifique a un fournisseur : timeouts, retry avec
backoff, cache disque de developpement, journalisation d'un appel, et ecriture de
la consommation de quota dans `api_usage`.

Ces modules ne connaissent rien du metier : ils rendent du JSON brut.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

DEFAULT_TIMEOUT = httpx.Timeout(15.0)
MAX_ATTEMPTS = 3
RETRY_STATUSES = frozenset({429, 500, 502, 503, 504})
#: Cles de parametres a ne jamais ecrire dans le cache disque ni dans les logs.
SECRET_PARAMS = frozenset({"apiKey", "api_key", "key"})


class ProviderError(RuntimeError):
    """Echec d'appel a une API externe, apres epuisement des tentatives."""

    def __init__(self, provider: str, endpoint: str, message: str, status_code: int | None = None):
        super().__init__(f"[{provider}] {endpoint} : {message}")
        self.provider = provider
        self.endpoint = endpoint
        self.status_code = status_code


@dataclass
class ProviderResponse:
    """Reponse normalisee d'un appel externe."""

    data: Any
    headers: dict[str, str] = field(default_factory=dict)
    duration_ms: int = 0
    from_cache: bool = False


def record_api_usage(
    provider: str,
    endpoint: str,
    cost: int,
    remaining: int | None,
    settings: Settings | None = None,
    outcome: str | None = None,
) -> None:
    """Trace la consommation de quota d'un appel dans `api_usage`.

    `outcome` NULL est le cas ordinaire : un appel abouti et facture. Une valeur
    designe une **lecture** de compteur prise sur une tentative qui n'a pas
    abouti, et `cost` vaut alors 0 — voir `record_quota_reading`.
    """
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at, outcome) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (provider, endpoint, cost, remaining, utcnow(), outcome),
        )


def record_quota_reading(
    provider: str,
    endpoint: str,
    remaining: int | None,
    outcome: str,
    settings: Settings | None = None,
) -> None:
    """Enregistre le compteur lu sur une tentative **qui n'a pas abouti**.

    **Le cout n'est pas connu ici, et c'est pour ca qu'il vaut 0.** Une tentative
    echouee consomme chez API-Football — un appel est un appel — et ne consomme
    pas chez The Odds API, qui facture au marche servi. Facturer generiquement
    aurait fausse l'un des deux ; ne rien ecrire les faussait tous les deux.

    Ce qui fait foi est donc la **difference entre deux lectures consecutives**
    de `remaining`. Tout ecart entre cette difference et la somme des couts
    journalises est de la consommation invisible, et c'est exactement ce qu'on
    cherche a rendre mesurable : le 06/08, le compteur a chute de 7 497 a 97
    pour 2 953 appels journalises.
    """
    record_api_usage(provider, endpoint, 0, remaining, settings, outcome=outcome)


def last_known_quota(provider: str, settings: Settings | None = None) -> dict[str, Any] | None:
    """Dernier etat de quota connu pour un fournisseur, ou None si jamais appele."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT remaining, called_at FROM api_usage "
            "WHERE provider = ? AND remaining IS NOT NULL "
            "ORDER BY called_at DESC, id DESC LIMIT 1",
            (provider,),
        ).fetchone()
    if row is None:
        return None
    return {"remaining": row["remaining"], "called_at": row["called_at"]}


def _safe_params(params: dict[str, Any]) -> dict[str, Any]:
    """Copie des parametres sans les secrets, pour les logs et la cle de cache."""
    return {k: v for k, v in params.items() if k not in SECRET_PARAMS}


class BaseHTTPClient:
    """Client HTTP partage : retry, timeout, cache de dev, journalisation."""

    provider_name: str = "base"
    base_url: str = ""

    #: Delai plancher avant de retenter une erreur transitoire cachee dans un
    #: corps HTTP 200. Le backoff ordinaire (1s, 2s) convient a une coupure
    #: reseau, pas a un quota par minute, qui ne se libere pas en deux secondes.
    payload_retry_delay: float = 0.0

    def _quota_reading(self, headers: dict[str, str]) -> int | None:
        """Compteur de quota restant lu dans les en-tetes, si ce fournisseur en a un.

        Le nom de l'en-tete differe d'un fournisseur a l'autre — et certains
        n'en ont aucun, l'Elo tennis ou la meteo n'ayant pas de quota du tout.
        Par defaut, rien a lire : seul un client qui connait son en-tete le
        redefinit.
        """
        return None

    def _observe_attempt(self, path: str, headers: dict[str, str], outcome: str) -> None:
        """Note le compteur d'une tentative **qui ne sera pas facturee**.

        Appelee depuis la boucle de retry, donc sur chaque reponse HTTP qui
        n'est pas celle qu'on rend. Sans elle, un enrichissement en rafale sur
        un quota sature journalise **zero** appel la ou il en consomme trois par
        endpoint : la saturation de debit arrive en HTTP 200 chez API-Football,
        et elle est donc retentee.
        """
        remaining = self._quota_reading(headers)
        if remaining is not None:
            record_quota_reading(self.provider_name, path, remaining, outcome, self._settings)

    def _transient_payload_error(self, data: Any) -> str | None:
        """Erreur transitoire portee par le corps d'une reponse HTTP 200.

        Certains fournisseurs repondent 200 en placant l'erreur dans
        l'enveloppe : le code de statut ne dit alors rien, `RETRY_STATUSES` ne
        voit jamais rien, et une saturation passagere devient un echec
        definitif. Un client qui connait la forme de son enveloppe redefinit ce
        crochet ; par defaut aucune reponse 200 n'est suspecte.
        """
        return None

    def __init__(
        self,
        client: httpx.AsyncClient,
        settings: Settings | None = None,
        *,
        backoff_base: float | None = None,
    ) -> None:
        self._client = client
        self._settings = settings or get_settings()
        self._backoff_base = (
            backoff_base if backoff_base is not None else self._settings.http_backoff_base
        )

    @property
    def settings(self) -> Settings:
        return self._settings

    # -- Cache disque de developpement -------------------------------------

    def _cache_path(self, path: str, params: dict[str, Any]) -> Path:
        payload = json.dumps(
            {"provider": self.provider_name, "path": path, "params": _safe_params(params)},
            sort_keys=True,
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:32]
        slug = path.strip("/").replace("/", "_") or "root"
        return self._settings.dev_cache_dir / self.provider_name / f"{slug}_{digest}.json"

    def _cache_read(self, path: str, params: dict[str, Any]) -> Any | None:
        if not self._settings.dev_cache:
            return None
        cache_file = self._cache_path(path, params)
        if not cache_file.is_file():
            return None
        logger.info("%s cache dev HIT %s", self.provider_name, path)
        return json.loads(cache_file.read_text(encoding="utf-8"))

    def _cache_write(self, path: str, params: dict[str, Any], data: Any) -> None:
        if not self._settings.dev_cache:
            return
        cache_file = self._cache_path(path, params)
        cache_file.parent.mkdir(parents=True, exist_ok=True)
        cache_file.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")

    # -- Appel ---------------------------------------------------------------

    async def _get(
        self,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        headers: dict[str, str] | None = None,
        as_text: bool = False,
        as_bytes: bool = False,
    ) -> ProviderResponse:
        """GET avec retry sur 429/5xx et sur les erreurs reseau.

        Leve `ProviderError` apres `MAX_ATTEMPTS` tentatives infructueuses.

        `as_text` rend le corps brut au lieu de le decoder en JSON : toutes les
        sources ne sont pas des APIs, et une page publiee en HTML se traverse
        avec le meme retry, le meme timeout et le meme cache que le reste.

        `as_bytes` va un cran plus loin, pour un corps qui n'est pas du texte —
        un classeur publie en telechargement. Il n'est jamais mis en cache
        disque : le cache de developpement est un cache **JSON**, et y ecrire des
        octets bruts le corromprait.
        """
        params = params or {}
        url = f"{self.base_url}{path}"

        if not as_bytes:
            cached = self._cache_read(path, params)
            if cached is not None:
                return ProviderResponse(data=cached, from_cache=True)

        last_error: str = "aucune tentative"
        last_status: int | None = None
        floor_delay = 0.0

        for attempt in range(1, MAX_ATTEMPTS + 1):
            floor_delay = 0.0
            try:
                response = await self._client.get(
                    url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
            else:
                if response.status_code < 400:
                    if as_bytes:
                        data: Any = response.content
                    else:
                        data = response.text if as_text else response.json()
                    transient = self._transient_payload_error(data)
                    if transient is None:
                        if not as_bytes:
                            self._cache_write(path, params, data)
                        return ProviderResponse(
                            data=data,
                            headers=dict(response.headers),
                            duration_ms=int(response.elapsed.total_seconds() * 1000),
                        )
                    # Rien n'est mis en cache : une saturation n'est pas une reponse.
                    last_error = transient
                    last_status = None
                    floor_delay = self.payload_retry_delay
                    # **Elle a pourtant consomme.** Une saturation de debit
                    # arrive en HTTP 200, donc elle passe le filtre de statut,
                    # elle est retentee, et rien ne la journalisait.
                    self._observe_attempt(path, dict(response.headers), "retry")
                else:
                    last_status = response.status_code
                    last_error = f"HTTP {response.status_code} — {response.text[:200]}"
                    self._observe_attempt(
                        path,
                        dict(response.headers),
                        "retry" if response.status_code in RETRY_STATUSES else "error",
                    )
                    if response.status_code not in RETRY_STATUSES:
                        break

            if attempt < MAX_ATTEMPTS:
                delay = max(self._backoff_base * (2 ** (attempt - 1)), floor_delay)
                logger.warning(
                    "%s %s echec (%s), nouvelle tentative %d/%d dans %.1fs",
                    self.provider_name,
                    path,
                    last_error,
                    attempt + 1,
                    MAX_ATTEMPTS,
                    delay,
                )
                if delay > 0:
                    await asyncio.sleep(delay)

        raise ProviderError(self.provider_name, path, last_error, last_status)
