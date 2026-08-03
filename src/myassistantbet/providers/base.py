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
) -> None:
    """Trace la consommation de quota d'un appel dans `api_usage`."""
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (provider, endpoint, cost, remaining, utcnow()),
        )


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
    ) -> ProviderResponse:
        """GET avec retry sur 429/5xx et sur les erreurs reseau.

        Leve `ProviderError` apres `MAX_ATTEMPTS` tentatives infructueuses.
        """
        params = params or {}
        url = f"{self.base_url}{path}"

        cached = self._cache_read(path, params)
        if cached is not None:
            return ProviderResponse(data=cached, from_cache=True)

        last_error: str = "aucune tentative"
        last_status: int | None = None

        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.get(
                    url, params=params, headers=headers, timeout=DEFAULT_TIMEOUT
                )
            except httpx.HTTPError as exc:
                last_error = f"{type(exc).__name__}: {exc}"
                last_status = None
            else:
                if response.status_code < 400:
                    data = response.json()
                    self._cache_write(path, params, data)
                    return ProviderResponse(
                        data=data,
                        headers=dict(response.headers),
                        duration_ms=int(response.elapsed.total_seconds() * 1000),
                    )
                last_status = response.status_code
                last_error = f"HTTP {response.status_code} — {response.text[:200]}"
                if response.status_code not in RETRY_STATUSES:
                    break

            if attempt < MAX_ATTEMPTS:
                delay = self._backoff_base * (2 ** (attempt - 1))
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
