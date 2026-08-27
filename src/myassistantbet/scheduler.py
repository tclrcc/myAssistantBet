"""Taches planifiees (APScheduler, en process).

Trois familles, et une regle qui les separe : **rien de ce qui coute des
credits The Odds API ne part sans decision humaine.** Le scan quotidien est la
seule exception, et il etait deja la — c'est lui qui remplit le board du matin,
sans quoi il n'y aurait rien a cocher.

Tout le reste est gratuit ou marginal :

- l'Elo de Tennis Abstract et l'historique de tennis-data.co.uk n'ont ni cle ni
  quota ; ils etaient rafraichis a l'enrichissement, donc jamais tant qu'aucune
  session n'etait montee ;
- la synchronisation des competitions passe par `GET /sports`, gratuit ;
- les compositions coutent un appel API-Football par match de la shortlist, et
  seulement dans les quatre-vingt-dix minutes qui precedent son coup d'envoi.

L'enrichissement (etage B) n'est **pas** planifie, et ce n'est pas un oubli :
il depense de vrais credits, une shortlist de trente matchs en vaut quelques
centaines, et le plancher `ODDS_API_CREDIT_FLOOR` protege le fond du quota mais
pas le gaspillage. Il reste un clic.
"""

from __future__ import annotations

import logging

import httpx
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger

from . import backup as backup_service
from . import timelines as timelines_service
from .config import Settings
from .providers.apifootball import APIFootballClient
from .providers.oddsapi import OddsAPIClient
from .providers.tennisabstract import TennisAbstractClient
from .providers.tennisapi import TennisAPIClient
from .providers.tennisdata import TennisDataClient
from .services import competitions as competitions_service
from .services import elo as elo_service
from .services import ingestion as ingestion_service
from .services import serve_stats as serve_stats_service
from .services import tennis_history as tennis_history_service
from .services.context import refresh_due_lineups
from .services.scan import run_scan

logger = logging.getLogger(__name__)

SCAN_JOB_ID = "scan_quotidien"
FREE_JOB_ID = "sources_gratuites"
LINEUPS_JOB_ID = "compositions"
COVERAGE_JOB_ID = "couverture_prix"
TIMELINES_JOB_ID = "timelines_service"

#: Minutes apres le scan pour les sources gratuites. Elles ne dependent pas du
#: scan, mais les enchainer groupe les appels sortants sur un seul moment de la
#: journee — et si le scan a decouvert une competition, la synchronisation qui
#: suit la voit tout de suite.
FREE_JOB_DELAY_MIN = 15

#: La reprise des timelines part apres les sources gratuites. **Elle n'est pas
#: gratuite**, et son garde-fou n'est donc pas la gratuite mais le plancher de
#: quota, verifie avant chaque joueur — meme regime que les statistiques de
#: service, dont elle prolonge la passe.
TIMELINES_JOB_DELAY_MIN = 30

#: Cadence du balayage des compositions. Elles sortent environ une heure avant
#: le coup d'envoi, sans horaire fixe : dix minutes suffisent a les prendre
#: fraiches sans multiplier les passages a vide, qui ne coutent qu'une lecture
#: en base tant qu'aucun match n'est dans la fenetre.
LINEUPS_EVERY_MIN = 10

#: Cadence du balayage de la couverture de prix. **Un job propre, et le choix se
#: prend sur une propriete mesurable de l'autre.**
#:
#: L'etat « sans prix » peut basculer par quatorze chemins d'ecriture recenses,
#: dont la moitie sont des aides appelees en boucle : leur faire porter l'appel
#: couterait 30 ms par evenement et serait oublie au quinzieme. Un balayage
#: unique n'a rien a se rappeler.
#:
#: Il ne s'accroche pas au balayage des compositions, qui a la meme cadence, et
#: la raison n'est pas esthetique : `_lineups` commence par
#: `if not settings.apifootball_key: return`. Une installation sans cle de
#: contexte cesserait de journaliser la couverture **en silence** — la dependance
#: cachee que ce projet retire partout. Celui-ci n'appelle aucune API, ne consomme
#: aucun quota et ne lit aucune cle.
#:
#: Dix minutes bornent l'imprecision de l'instant ; ce qui compte pour un point de
#: rupture est le **jour**, et un scan quotidien pouvait le manquer de vingt-quatre
#: heures — mesure du 27/08/2026.
COVERAGE_EVERY_MIN = 10

#: Le reglement automatique. **Trois passages par jour, et ils ne coutent
#: rien** : la passe ne fait aucun appel reseau, elle relit ce que
#: l'enrichissement a deja archive — `api_responses` au tennis, les resumes de
#: saison au football.
#:
#: Elle ne peut donc regler que ce que la collecte a rapporte, et c'est une
#: limite a connaitre : sur les 293 selections tranchees, **169 evenements
#: seulement** portaient un resultat lisible au 20/08/2026. Les autres
#: arriveront a mesure que l'enrichissement repasse sur leurs equipes.
#:
#: Trois heures plutot qu'une seule parce que les resultats n'arrivent pas a
#: heure fixe, et gratuit veut dire qu'un passage de plus ne se discute pas.
SETTLEMENT_JOB_ID = "reglement_automatique"
SETTLEMENT_HOURS = "9,15,22"


def build_scheduler(client: httpx.AsyncClient, settings: Settings) -> AsyncIOScheduler:
    """Planifie le scan quotidien et les rafraichissements gratuits."""
    scheduler = AsyncIOScheduler(timezone=settings.tz)

    async def _scan() -> None:
        logger.info("Scan planifie : demarrage")
        try:
            report = await run_scan(OddsAPIClient(client, settings), settings)
        except Exception:
            # Un scan qui echoue ne doit jamais tuer le process de l'application.
            logger.exception("Scan planifie : echec")
            return
        logger.info(
            "Scan planifie : %d evenements, cout %d credits",
            report.total_events,
            report.total_cost,
        )

    async def _free_sources() -> None:
        """Elo, historique tennis et catalogue des competitions.

        Chaque source est isolee : celle qui echoue ne prive pas les autres.
        Elles se declenchaient jusqu'ici a l'enrichissement seulement, ce qui
        laissait une installation sans session avec des classements figes.
        """
        try:
            report = await elo_service.refresh(TennisAbstractClient(client, settings), settings)
            logger.info(
                "Elo planifie : %d joueur(s) sur %s", report.total, ", ".join(report.counts) or "—"
            )
        except Exception:
            logger.exception("Elo planifie : echec")
        try:
            history = await tennis_history_service.refresh(
                TennisDataClient(client, settings), settings
            )
            logger.info(
                "Historique tennis planifie : %d match(s), %d saison(s)",
                history.matches,
                len(history.seasons),
            )
            # **Une source figee se journalise la ou tout ce qui se perd se
            # journalise.** Le planificateur est le seul endroit qui voie la
            # collecte tourner sans qu'un humain regarde : sans cette ligne, une
            # source qui repond 200 et ne bouge plus resterait invisible jusqu'a
            # ce qu'un bloc paraisse etrange, des semaines plus tard.
            #
            # `session_id` vaut NULL : ce n'est pas une session qui a perdu
            # quelque chose, c'est la collecte. Le rattacher a la derniere
            # session en date en ferait un defaut de cette session-la, ce qu'il
            # n'est pas.
            if history.frozen:
                ingestion_service.record(None, history.frozen, settings)
        except Exception:
            logger.exception("Historique tennis planifie : echec")
        try:
            sync = await competitions_service.sync_from_api(
                OddsAPIClient(client, settings), settings
            )
            logger.info(
                "Competitions planifiees : %d creee(s), %d mise(s) a jour",
                len(sync.created),
                len(sync.updated),
            )
        except Exception:
            logger.exception("Synchronisation des competitions : echec")
        try:
            # **L'entretien, et non la reprise.** Seuls les joueurs des matchs a
            # venir : un lot tennis en porte trente-cinq en moyenne, donc la
            # passe reste a quelques dizaines d'appels. Passer tout le catalogue
            # tous les jours en couterait cent-quatre-vingts pour rafraichir des
            # joueurs qui ne jouent pas.
            #
            # **Ce n'est pas une source gratuite**, contrairement aux trois
            # au-dessus, et c'est assume : elle est ici parce que la cadence est
            # la bonne — une fois par jour, apres le scan qui vient de decouvrir
            # les matchs du lendemain. Son garde-fou n'est pas la gratuite mais
            # le plancher de quota, verifie **avant chaque joueur**.
            serve = await serve_stats_service.sync(
                TennisAPIClient(client, settings),
                serve_stats_service.upcoming_players(settings),
                settings,
            )
            logger.info("Statistiques de service planifiees : %s", serve.line)
            if serve.rejects:
                ingestion_service.record(None, serve.rejects, settings)
        except Exception:
            logger.exception("Statistiques de service : echec")
        try:
            # **Le disque plein est la seule panne d'exploitation que rien ne
            # surveille**, et elle arreterait le service sans qu'aucun des
            # dispositifs construits ne dise pourquoi : `/tmp` est de la memoire
            # vive, SQLite y pose ses fichiers temporaires, et un `ENOSPC` ne
            # ressemble pas a ce qu'il est.
            #
            # Elle est **ici** et non au demarrage : un service reste allume des
            # jours, et une purge qui ne tourne qu'au redemarrage ne tourne pas.
            # Elle ne coute rien et ne sort pas de la machine — sa place parmi
            # les sources gratuites est la cadence, une fois par jour.
            purge = backup_service.purge_temp()
            if purge.removed:
                logger.info("Artefacts temporaires : %s", purge.line)
        except Exception:
            logger.exception("Purge des artefacts temporaires : echec")

    async def _timelines() -> None:
        """Reprise des timelines de service, par lots bornes.

        **Elle ne finit pas en une fois et n'a pas a le faire.** La couverture
        mesuree de `event/get` est de 6 %, donc couvrir le catalogue demande une
        quinzaine d'heures : un passage quotidien borne a quelques joueurs
        avance sans jamais chevaucher le suivant, et l'archive fait que rien
        n'est repaye. Les joueurs des lots a venir passent en premier.
        """
        if not settings.rapidapi_key:
            return
        try:
            await timelines_service.run(settings=settings)
        except Exception:
            logger.exception("Timelines de service : echec")

    async def _settlement() -> None:
        """Propose les reglements calculables. **N'ecrit jamais dans `picks`.**

        Le cron propose, un humain promeut : 293 selections tranchees portent
        tout ce que ce projet sait produire, et un reglement errone les
        corromprait en silence. Une divergence avec un reglement deja pose se
        journalise et n'ecrase rien — la lecon de `set_open_dossiers` au lot 14.

        **Aucun appel reseau** : la passe relit ce que l'enrichissement a deja
        archive, donc elle ne coute rien, et elle ne peut rien manquer d'autre
        que ce que la collecte n'a pas encore rapporte.
        """
        from .services import settlement

        try:
            passe = settlement.run(settings)
        except Exception:
            logger.exception("Reglement automatique : passe interrompue")
            return
        logger.info(
            "Reglement automatique : %d proposition(s), %d divergence(s), "
            "%d hors regle, %d sans resultat, %d inacheve(s)",
            len(passe.nouveaux),
            len(passe.divergents),
            passe.hors_regle,
            passe.sans_resultat,
            passe.inacheves,
        )

    async def _lineups() -> None:
        """Compositions des matchs de la shortlist dont le coup d'envoi approche.

        Sans cle API-Football, l'appel echouerait a chaque passage : le
        balayage ne part pas. Un passage a vide ne coute qu'une lecture en base.
        """
        if not settings.apifootball_key:
            return
        try:
            sweep = await refresh_due_lineups(APIFootballClient(client, settings), settings)
        except Exception:
            logger.exception("Compositions planifiees : echec")
            return
        if sweep.fetched:
            logger.info("Compositions recuperees : %s", ", ".join(sweep.fetched))
        if sweep.errors:
            logger.warning("Compositions partielles : %s", " ; ".join(sweep.errors))

    async def _coverage() -> None:
        """Date les bascules de l'etat « sans prix ». Aucun appel externe.

        **Le seul site d'appel periodique**, et c'est la propriete recherchee :
        un chemin d'ecriture ajoute demain ne peut pas oublier de journaliser,
        puisque rien n'a a s'en souvenir. Le scan garde son appel — il est le
        chemin le plus frequent et il date a la seconde ce qu'il vient d'ecrire.

        Un passage manque ne se rattrape pas : l'etat se recalcule a chaque
        passage depuis la base, donc celui d'apres voit exactement la meme chose.
        """
        try:
            for libelle, detail in competitions_service.note_price_coverage(settings):
                logger.info("%s : %s", libelle, detail)
        except Exception:
            logger.exception("Couverture de prix : echec du balayage")

    scheduler.add_job(
        _scan,
        trigger=CronTrigger(
            hour=settings.scan_hour, minute=settings.scan_minute, timezone=settings.tz
        ),
        id=SCAN_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    free_hour, free_minute = divmod(settings.scan_minute + FREE_JOB_DELAY_MIN, 60)
    scheduler.add_job(
        _free_sources,
        trigger=CronTrigger(
            hour=(settings.scan_hour + free_hour) % 24, minute=free_minute, timezone=settings.tz
        ),
        id=FREE_JOB_ID,
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
    )
    tl_hour, tl_minute = divmod(settings.scan_minute + TIMELINES_JOB_DELAY_MIN, 60)
    scheduler.add_job(
        _timelines,
        trigger=CronTrigger(
            hour=(settings.scan_hour + tl_hour) % 24, minute=tl_minute, timezone=settings.tz
        ),
        id=TIMELINES_JOB_ID,
        replace_existing=True,
        # Un passage manque ne se rattrape pas : la passe est reprenable, donc
        # celui de demain reprendra exactement ou celui-ci s'est arrete.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        _settlement,
        trigger=CronTrigger(hour=SETTLEMENT_HOURS, minute=5, timezone=settings.tz),
        id=SETTLEMENT_JOB_ID,
        replace_existing=True,
        # Un passage manque se rattrape tout seul : la passe est idempotente et
        # recalcule tout, donc celui d'apres reprend exactement le meme etat.
        misfire_grace_time=3600,
        coalesce=True,
    )
    scheduler.add_job(
        _lineups,
        trigger=CronTrigger(minute=f"*/{LINEUPS_EVERY_MIN}", timezone=settings.tz),
        id=LINEUPS_JOB_ID,
        replace_existing=True,
        # Un passage manque ne se rattrape pas : la fenetre a bouge, et le
        # passage suivant arrive dans dix minutes.
        misfire_grace_time=60,
        coalesce=True,
    )
    scheduler.add_job(
        _coverage,
        trigger=CronTrigger(minute=f"*/{COVERAGE_EVERY_MIN}", timezone=settings.tz),
        id=COVERAGE_JOB_ID,
        replace_existing=True,
        # Meme raison que les compositions : l'etat se recalcule, donc rien a
        # rattraper.
        misfire_grace_time=60,
        coalesce=True,
    )
    # **La ligne se derive des taches posees, elle ne les enumere pas a la main.**
    # Ecrite en dur, elle a omis le balayage de couverture le jour meme ou il est
    # arrive : un message de demarrage qui decrit un etat incomplet est une
    # seconde copie de la liste des taches, et rien ne l'obligeait a concorder.
    logger.info(
        "Planifie (%s) : %s",
        settings.tz,
        ", ".join(
            f"{job.id} {job.trigger}" for job in sorted(scheduler.get_jobs(), key=lambda j: j.id)
        ),
    )
    return scheduler
