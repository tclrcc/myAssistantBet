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

    # --- Sauvegardes -------------------------------------------------------
    backup_dir: Path = Path("./data/backups")
    backup_keep_days: int = Field(default=7, ge=1, le=365)

    # --- Captures de coupons ------------------------------------------------
    #: Sous `data/`, donc deja couvert par le `ReadWritePaths` de l'unite
    #: systemd et par le `.gitignore`.
    upload_dir: Path = Path("./data/uploads")
    #: Une capture d'ecran de coupon depasse rarement 1 Mo. Au-dela, c'est une
    #: erreur de manipulation, et le disque du VPS n'est pas extensible.
    upload_max_bytes: int = Field(default=5_000_000, ge=10_000)

    # --- Developpement -----------------------------------------------------
    dev_cache: bool = False
    dev_cache_dir: Path = Path("./data/dev_cache")

    # --- Fenetre de scan (jours glissants a partir d'aujourd'hui) ----------
    scan_window_days: int = Field(default=2, ge=1, le=7)

    # --- Scan quotidien (heure locale `tz`) --------------------------------
    scheduler_enabled: bool = True
    scan_hour: int = Field(default=7, ge=0, le=23)
    scan_minute: int = Field(default=0, ge=0, le=59)

    #: Bookmakers de repli pour les marches que le principal ne sert pas.
    #: Interroger jusqu'a dix books coute le meme prix qu'un seul : le cout vaut
    #: `marches x ceil(books / 10)`. Vide = Betclic seul, comme avant.
    reference_bookmakers: str = ""

    @property
    def reference_books(self) -> tuple[str, ...]:
        """Books de reference, dans l'ordre de priorite."""
        return tuple(key.strip() for key in self.reference_bookmakers.split(",") if key.strip())

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
    def backup_dir_absolute(self) -> Path:
        """Chemin absolu du dossier de sauvegardes, resolu depuis la racine."""
        if self.backup_dir.is_absolute():
            return self.backup_dir
        return (PROJECT_ROOT / self.backup_dir).resolve()

    @property
    def upload_dir_absolute(self) -> Path:
        """Chemin absolu du dossier des captures, resolu depuis la racine."""
        if self.upload_dir.is_absolute():
            return self.upload_dir
        return (PROJECT_ROOT / self.upload_dir).resolve()

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
            "backup_dir": str(self.backup_dir_absolute),
            "backup_keep_days": self.backup_keep_days,
            "dev_cache": self.dev_cache,
            "odds_api_key_present": bool(self.odds_api_key),
            "apifootball_key_present": bool(self.apifootball_key),
        }


@lru_cache
def get_settings() -> Settings:
    """Instance unique des parametres (cache pour eviter les relectures disque)."""
    return Settings()
