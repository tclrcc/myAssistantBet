"""Configuration de l'application, chargee depuis l'environnement et `.env`."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

PACKAGE_DIR = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_DIR.parent.parent


class Settings(BaseSettings):
    """Parametres de l'application.

    Les secrets ne proviennent que de l'environnement (ou du `.env` non versionne).
    Aucune valeur par defaut n'est fournie pour les cles d'API : leur absence est
    un etat normal (phases 0 a 1) qui doit rester visible.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Secrets -----------------------------------------------------------
    odds_api_key: str = ""
    apifootball_key: str = ""

    # --- Base de donnees ---------------------------------------------------
    db_path: Path = Path("./data/myassistantbet.db")

    # --- Affichage ---------------------------------------------------------
    tz: str = "Europe/Paris"

    # --- Garde-fous quota --------------------------------------------------
    odds_api_credit_floor: int = 500

    # --- Etage B -----------------------------------------------------------
    #: Competitions (cles The Odds API) pour lesquelles demander les props buteurs.
    #: Ailleurs, ces marches ne sont servis par aucun bookmaker : ne pas depenser
    #: de credit. Liste separee par des virgules.
    player_props_leagues: str = (
        "soccer_epl,soccer_france_ligue_one,soccer_spain_la_liga,"
        "soccer_germany_bundesliga,soccer_italy_serie_a,soccer_usa_mls"
    )

    # --- Appels externes ---------------------------------------------------
    #: Base du backoff exponentiel entre deux tentatives, en secondes.
    #: Mise a 0 dans les tests pour ne jamais attendre.
    http_backoff_base: float = Field(default=1.0, ge=0)

    # --- Developpement -----------------------------------------------------
    dev_cache: bool = False
    dev_cache_dir: Path = Path("./data/dev_cache")

    # --- Fenetre de scan (jours glissants a partir d'aujourd'hui) ----------
    scan_window_days: int = Field(default=2, ge=1, le=7)

    # --- Scan quotidien (heure locale `tz`) --------------------------------
    scheduler_enabled: bool = True
    scan_hour: int = Field(default=7, ge=0, le=23)
    scan_minute: int = Field(default=0, ge=0, le=59)

    @property
    def player_props_whitelist(self) -> frozenset[str]:
        return frozenset(key.strip() for key in self.player_props_leagues.split(",") if key.strip())

    @property
    def db_path_absolute(self) -> Path:
        """Chemin absolu de la base, resolu depuis la racine du projet."""
        if self.db_path.is_absolute():
            return self.db_path
        return (PROJECT_ROOT / self.db_path).resolve()

    @property
    def migrations_dir(self) -> Path:
        return PACKAGE_DIR / "migrations"

    def public_dict(self) -> dict[str, object]:
        """Vue de la config sans aucun secret, exposable dans `/health`."""
        return {
            "db_path": str(self.db_path_absolute),
            "tz": self.tz,
            "odds_api_credit_floor": self.odds_api_credit_floor,
            "scan_window_days": self.scan_window_days,
            "scheduler_enabled": self.scheduler_enabled,
            "scan_at": f"{self.scan_hour:02d}:{self.scan_minute:02d}",
            "dev_cache": self.dev_cache,
            "odds_api_key_present": bool(self.odds_api_key),
            "apifootball_key_present": bool(self.apifootball_key),
        }


@lru_cache
def get_settings() -> Settings:
    """Instance unique des parametres (cache pour eviter les relectures disque)."""
    return Settings()
