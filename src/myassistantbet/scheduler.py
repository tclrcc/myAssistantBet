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

from .config import Settings
from .providers.apifootball import APIFootballClient
from .providers.oddsapi import OddsAPIClient
from .providers.tennisabstract import TennisAbstractClient
from .providers.tennisdata import TennisDataClient
from .services import competitions as competitions_service
from .services import elo as elo_service
from .services import tennis_history as tennis_history_service
from .services.context import refresh_due_lineups
from .services.scan import run_scan

logger = logging.getLogger(__name__)

SCAN_JOB_ID = "scan_quotidien"
FREE_JOB_ID = "sources_gratuites"
LINEUPS_JOB_ID = "compositions"

#: Minutes apres le scan pour les sources gratuites. Elles ne dependent pas du
#: scan, mais les enchainer groupe les appels sortants sur un seul moment de la
#: journee — et si le scan a decouvert une competition, la synchronisation qui
#: suit la voit tout de suite.
FREE_JOB_DELAY_MIN = 15

#: Cadence du balayage des compositions. Elles sortent environ une heure avant
#: le coup d'envoi, sans horaire fixe : dix minutes suffisent a les prendre
#: fraiches sans multiplier les passages a vide, qui ne coutent qu'une lecture
#: en base tant qu'aucun match n'est dans la fenetre.
LINEUPS_EVERY_MIN = 10


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
    logger.info(
        "Planifie : scan a %02d:%02d, sources gratuites a %02d:%02d, "
        "compositions toutes les %d min (%s)",
        settings.scan_hour,
        settings.scan_minute,
        (settings.scan_hour + free_hour) % 24,
        free_minute,
        LINEUPS_EVERY_MIN,
        settings.tz,
    )
    return scheduler
