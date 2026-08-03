from __future__ import annotations

import json
from collections.abc import AsyncIterator, Callable, Iterator
from pathlib import Path
from typing import Any

import httpx
import pytest

from myassistantbet import db
from myassistantbet.config import Settings, get_settings
from myassistantbet.main import ENRICH_PROGRESS
from myassistantbet.providers.oddsapi import OddsAPIClient

FIXTURES_DIR = Path(__file__).parent / "fixtures"


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Isole chaque test : base temporaire, secrets factices, cache de config vide.

    Les variables d'environnement priment sur un eventuel `.env` local, ce qui
    garantit qu'aucun test ne touche la base de developpement. Le planificateur
    est desactive : aucun test ne doit declencher de scan en arriere-plan.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ODDS_API_KEY", "cle-odds-de-test")
    monkeypatch.setenv("APIFOOTBALL_KEY", "")
    monkeypatch.setenv("ODDS_API_CREDIT_FLOOR", "500")
    monkeypatch.setenv("DEV_CACHE", "0")
    monkeypatch.setenv("DEV_CACHE_DIR", str(tmp_path / "dev_cache"))
    monkeypatch.setenv("TZ", "Europe/Paris")
    monkeypatch.setenv("SCHEDULER_ENABLED", "0")
    monkeypatch.setenv("HTTP_BACKOFF_BASE", "0")

    get_settings.cache_clear()
    ENRICH_PROGRESS.clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()
        ENRICH_PROGRESS.clear()


@pytest.fixture
def migrated(isolated_settings: Settings) -> Settings:
    """Base temporaire avec toutes les migrations appliquees."""
    db.run_migrations(isolated_settings)
    return isolated_settings


@pytest.fixture
def load_fixture() -> Callable[[str], Any]:
    """Charge une reponse d'API capturee dans `tests/fixtures/`."""

    def _load(name: str) -> Any:
        return json.loads((FIXTURES_DIR / name).read_text(encoding="utf-8"))

    return _load


@pytest.fixture
async def http_client() -> AsyncIterator[httpx.AsyncClient]:
    async with httpx.AsyncClient() as client:
        yield client


@pytest.fixture
def odds_client(http_client: httpx.AsyncClient, migrated: Settings) -> OddsAPIClient:
    """Client Odds API pret a l'emploi (temporisation nulle via la config de test)."""
    return OddsAPIClient(http_client, migrated)
