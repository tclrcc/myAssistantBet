from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from myassistantbet.config import PROJECT_ROOT, Settings

ENV_VARS = (
    "ODDS_API_KEY",
    "APIFOOTBALL_KEY",
    "DB_PATH",
    "TZ",
    "ODDS_API_CREDIT_FLOOR",
    "DEV_CACHE",
    "DEV_CACHE_DIR",
    "SCAN_WINDOW_DAYS",
    "SCHEDULER_ENABLED",
    "SCAN_HOUR",
    "SCAN_MINUTE",
    "HTTP_BACKOFF_BASE",
)


@pytest.fixture
def env_vierge(monkeypatch: pytest.MonkeyPatch) -> None:
    """Retire toute variable d'environnement pour observer les vraies valeurs par defaut."""
    for name in ENV_VARS:
        monkeypatch.delenv(name, raising=False)


def test_valeurs_par_defaut(env_vierge: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.tz == "Europe/Paris"
    assert settings.odds_api_credit_floor == 500
    assert settings.dev_cache is False
    assert settings.scan_window_days == 2
    assert settings.odds_api_key == ""


def test_db_path_relatif_resolu_depuis_la_racine() -> None:
    settings = Settings(_env_file=None, db_path=Path("./data/x.db"))

    assert settings.db_path_absolute == PROJECT_ROOT / "data" / "x.db"


def test_db_path_absolu_conserve(tmp_path: Path) -> None:
    settings = Settings(_env_file=None, db_path=tmp_path / "x.db")

    assert settings.db_path_absolute == tmp_path / "x.db"


def test_public_dict_ne_contient_aucun_secret() -> None:
    settings = Settings(_env_file=None, odds_api_key="secret-a", apifootball_key="secret-b")
    public = settings.public_dict()

    assert "secret-a" not in str(public)
    assert "secret-b" not in str(public)
    assert public["odds_api_key_present"] is True
    assert public["apifootball_key_present"] is True


def test_fenetre_de_scan_bornee() -> None:
    with pytest.raises(ValidationError):
        Settings(_env_file=None, scan_window_days=0)


def test_migrations_dir_existe() -> None:
    settings = Settings(_env_file=None)

    assert settings.migrations_dir.is_dir()
    assert (settings.migrations_dir / "001_init.sql").is_file()


def test_temporisation_par_defaut_en_production(env_vierge: None) -> None:
    # Les tests forcent 0 ; hors tests, le backoff doit rester reel.
    assert Settings(_env_file=None).http_backoff_base == 1.0


def test_planificateur_actif_par_defaut(env_vierge: None) -> None:
    settings = Settings(_env_file=None)

    assert settings.scheduler_enabled is True
    assert (settings.scan_hour, settings.scan_minute) == (7, 0)
