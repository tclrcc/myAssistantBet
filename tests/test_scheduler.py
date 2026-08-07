"""Ce que l'application fait toute seule — et ce qu'elle ne fait pas."""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
from apscheduler.schedulers.asyncio import AsyncIOScheduler

from myassistantbet.config import Settings
from myassistantbet.scheduler import (
    FREE_JOB_ID,
    LINEUPS_EVERY_MIN,
    LINEUPS_JOB_ID,
    SCAN_JOB_ID,
    build_scheduler,
)


@pytest.fixture
def scheduler(http_client: httpx.AsyncClient, migrated: Settings) -> Iterator[AsyncIOScheduler]:
    """Un planificateur construit mais **jamais demarre** : aucun test ne doit
    declencher un scan en arriere-plan. Les declencheurs se lisent sans cela."""
    yield build_scheduler(http_client, migrated)


def _fields(job: Any) -> dict[str, str]:
    return {field.name: str(field) for field in job.trigger.fields}


def _job(scheduler: AsyncIOScheduler, job_id: str) -> Any:
    return next(job for job in scheduler.get_jobs() if job.id == job_id)


def test_les_trois_taches_sont_planifiees(scheduler: AsyncIOScheduler) -> None:
    assert {job.id for job in scheduler.get_jobs()} == {SCAN_JOB_ID, FREE_JOB_ID, LINEUPS_JOB_ID}


def test_aucune_tache_n_enrichit_la_shortlist(scheduler: AsyncIOScheduler) -> None:
    """L'etage B depense de vrais credits — une shortlist de trente matchs en
    vaut quelques centaines — et le plancher protege le fond du quota, pas le
    gaspillage. Le planifier serait la seule depense automatique que personne
    n'aurait decidee. Ce test existe pour qu'on ne l'ajoute pas par megarde."""
    noms = " ".join(job.func.__qualname__ for job in scheduler.get_jobs()).lower()

    assert "enrich" not in noms


def test_les_sources_gratuites_suivent_le_scan(
    scheduler: AsyncIOScheduler, migrated: Settings
) -> None:
    """Groupees sur un seul moment de la journee, et **apres** le scan : s'il a
    decouvert une competition, la synchronisation qui suit la voit tout de
    suite."""
    scan = _fields(_job(scheduler, SCAN_JOB_ID))
    gratuit = _fields(_job(scheduler, FREE_JOB_ID))

    assert scan["hour"] == str(migrated.scan_hour)
    assert scan["minute"] == str(migrated.scan_minute)
    assert (int(gratuit["hour"]), int(gratuit["minute"])) > (
        int(scan["hour"]),
        int(scan["minute"]),
    )


def test_les_compositions_sont_balayees_souvent(scheduler: AsyncIOScheduler) -> None:
    """Elles sortent environ une heure avant le coup d'envoi, sans horaire fixe :
    une passe quotidienne les manquerait toutes."""
    assert _fields(_job(scheduler, LINEUPS_JOB_ID))["minute"] == f"*/{LINEUPS_EVERY_MIN}"


def test_un_passage_manque_de_composition_ne_se_rattrape_pas(
    scheduler: AsyncIOScheduler,
) -> None:
    """La fenetre a bouge et le passage suivant arrive dans dix minutes :
    rejouer un balayage en retard appellerait pour des matchs deja commences."""
    assert _job(scheduler, LINEUPS_JOB_ID).misfire_grace_time <= 60
