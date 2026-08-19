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
    #: Cle RapidAPI, pour l'**evaluation** de `tennis-api.com`. Aucun chemin
    #: servi ne la lit : elle existe pour qu'un banc de mesure puisse tourner
    #: sans exporter une variable a la main.
    #:
    #: **Elle etait deja dans `.env` et n'arrivait nulle part**, et la cause est
    #: `extra="ignore"` : une cle non declaree y est indiscernable d'une cle
    #: absente. Septieme forme du defaut caracteristique du projet — une sortie
    #: identique pour l'oubli et pour le cas ordinaire — et la premiere sur la
    #: configuration.
    rapidapi_key: str = ""

    # --- Base de donnees ---------------------------------------------------
    db_path: Path = Path("./data/myassistantbet.db")

    # --- Affichage ---------------------------------------------------------
    tz: str = "Europe/Paris"

    # --- Garde-fous quota --------------------------------------------------
    odds_api_credit_floor: int = 500

    #: Plancher d'appels API-Football sous lequel le dossier d'equipe ne part
    #: plus. Il ne bloque **que** le dossier : le contexte d'un match reste la
    #: fonction premiere, et l'arreter faute de quota pour un bonus serait le
    #: mauvais arbitrage. Le quota est journalier, donc un plancher franchi se
    #: reouvre de lui-meme le lendemain.
    apifootball_call_floor: int = 500

    #: Plancher d'appels `tennis-api.com` sous lequel **toute** collecte de
    #: statistiques de service s'arrete.
    #:
    #: Le quota est **mensuel** (150 000 sur le plan PRO, remis a zero tous les
    #: 31 jours), et c'est ce qui le rend different des deux autres : un plancher
    #: franchi ne se rouvre pas le lendemain, il se rouvre le mois prochain. Une
    #: reprise d'historique qui epuiserait le quota le 8 du mois laisserait
    #: l'application sans donnees courantes pendant trois semaines.
    #:
    #: 20 000, soit **13 % du quota mensuel** : de quoi tenir l'entretien
    #: quotidien pendant plus d'un an au regime mesure (~180 appels/jour), donc
    #: largement de quoi voir venir et corriger. Il se regle sans toucher au code.
    rapidapi_call_floor: int = 20_000

    #: Les lignes `Service`, `Retour`, `Jeux` et `Ecart` des blocs tennis.
    #:
    #: **Defaut a faux, et c'est le point le plus important de la partie
    #: gabarit.** Le passage du budget a dix dossiers et l'ajout de ces lignes
    #: modifient tous deux ce que le modele produit. Livres le meme jour, leurs
    #: effets seraient indissociables et le `changelog_mesure` ne servirait a
    #: rien — il existe precisement pour que deux changements de cadre se
    #: decoupent.
    #:
    #: Il se bascule apres quelques sessions, et sa **vraie date d'activation**
    #: entre au journal des mesures — pas la date du commit.
    serve_lines_enabled: bool = False

    #: Age maximum, en jours, d'une rencontre dont on tente encore la timeline.
    #:
    #: **C'est une fenetre de retention de la source, mesuree et non supposee.**
    #: Releve du 18/08/2026 sur les 564 rencontres tentees et archivees : age
    #: maximum d'une rencontre servie, **80 jours** ; 387 tentatives au-dela de
    #: 90 jours, **zero timeline**. Le taux passe de 57 % sur la tranche
    #: 31-90 jours a 0 % sur les deux tranches suivantes — ce n'est pas une
    #: decroissance, c'est une falaise.
    #:
    #: 90 est donc la premiere dizaine au-dessus du dernier succes observe : la
    #: marge se prend du cote qui ne perd pas de donnee. Le filtre supprime 69 %
    #: des tentatives en gardant **113 timelines sur 113**.
    #:
    #: Zero ou negatif desactive le filtre — c'est le seul moyen de rejouer la
    #: mesure le jour ou la retention de la source changerait.
    timeline_max_age_days: int = 90

    #: Delai entre deux appels a `tennis-api.com`, en secondes.
    #:
    #: **Aucun en-tete de debit n'est servi par ce fournisseur** — ni
    #: `x-ratelimit-rate-limit`, ni `retry-after` — et dix appels consecutifs
    #: passent a 4,4 req/s sans un seul 429. Ce delai est donc une politesse de
    #: notre cote sur une reprise longue, et **pas une limite relevee** : ne pas
    #: le documenter comme si le fournisseur l'imposait.
    rapidapi_interval: float = Field(default=0.2, ge=0)

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

    #: Books retenus chez API-Football quand The Odds API ne sert aucune cote,
    #: dans l'ordre de preference. Betclic n'est pas au catalogue de ce
    #: fournisseur : il faut donc un substitut, et « proche de Betclic » se
    #: mesure. Sur un echantillon de matchs servis par les deux, l'ecart moyen
    #: en valeur absolue etait de 3.0 % pour BetVictor, 3.4 % pour William Hill
    #: et 888Sport, contre 5.4 % pour Unibet, 6.0 % pour Pinnacle et 6.8 % pour
    #: 1xBet. L'intuition « un book francais sera le plus proche » etait fausse.
    #: L'echantillon reste court : cet ordre se corrige sans toucher au code.
    apifootball_bookmakers: str = "888Sport,William Hill,BetVictor,10Bet,Bet365,Superbet"

    # --- Meteo -------------------------------------------------------------
    #: Contact envoye dans le `User-Agent` des appels meteo. Le National Weather
    #: Service **l'exige** — « a User Agent is required to identify your
    #: application », contact souhaite — et le coder en dur ferait porter a un
    #: autre les appels d'une installation qui n'est pas la sienne. Vide, les
    #: appels partent en s'annoncant sans contact plutot que de ne pas partir.
    weather_contact: str = ""

    @property
    def apifootball_books(self) -> tuple[str, ...]:
        """Substituts de Betclic, dans l'ordre de preference."""
        return tuple(key.strip() for key in self.apifootball_bookmakers.split(",") if key.strip())

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
            "apifootball_call_floor": self.apifootball_call_floor,
            "rapidapi_call_floor": self.rapidapi_call_floor,
            "timeline_max_age_days": self.timeline_max_age_days,
            "scan_window_days": self.scan_window_days,
            "scheduler_enabled": self.scheduler_enabled,
            "scan_at": f"{self.scan_hour:02d}:{self.scan_minute:02d}",
            "backup_dir": str(self.backup_dir_absolute),
            "backup_keep_days": self.backup_keep_days,
            "dev_cache": self.dev_cache,
            "odds_api_key_present": bool(self.odds_api_key),
            "apifootball_key_present": bool(self.apifootball_key),
            # **Le nom, jamais la valeur.** `/health` est servi derriere nginx et
            # relu dans des captures : un booleen dit qu'une cle est la sans
            # jamais la montrer, meme regle que les deux autres.
            "rapidapi_key_present": bool(self.rapidapi_key),
        }


@lru_cache
def get_settings() -> Settings:
    """Instance unique des parametres (cache pour eviter les relectures disque)."""
    return Settings()
