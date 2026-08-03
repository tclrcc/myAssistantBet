from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest

from myassistantbet.config import Settings, get_settings


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Settings]:
    """Isole chaque test : base temporaire, secrets factices, cache de config vide.

    Les variables d'environnement priment sur un eventuel `.env` local, ce qui
    garantit qu'aucun test ne touche la base de developpement.
    """
    monkeypatch.setenv("DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setenv("ODDS_API_KEY", "cle-odds-de-test")
    monkeypatch.setenv("APIFOOTBALL_KEY", "")
    monkeypatch.setenv("ODDS_API_CREDIT_FLOOR", "500")
    monkeypatch.setenv("DEV_CACHE", "0")
    monkeypatch.setenv("TZ", "Europe/Paris")

    get_settings.cache_clear()
    try:
        yield get_settings()
    finally:
        get_settings.cache_clear()
