"""Passe de reprise des timelines de service, relancable sans session.

**Elle ne finit pas en une fois, et c'est le dessin.** Une timeline coute quatre
a six appels la ou une table de service en coute un, et la couverture mesuree de
la source est de 6 % : couvrir le catalogue demande une quinzaine d'heures de
temps de mur. La passe est donc **reprenable** — l'archive `api_responses` sert
d'index, une rencontre deja demandee n'est jamais repayee — et elle s'interrompt
proprement sur le plancher de quota.

Elle se relance a la main (`myassistantbet-timelines`) ou toute seule, le
planificateur la rappelant chaque jour apres les sources gratuites.
"""

from __future__ import annotations

import argparse
import asyncio
import logging

import httpx

from .config import get_settings
from .providers.tennisapi import TennisAPIClient
from .services import ingestion as ingestion_service
from .services import serve_stats as serve_stats_service

logger = logging.getLogger(__name__)

#: Combien de joueurs au plus par passage. **Une borne de temps de mur, pas de
#: quota** : le plancher garde le second, et sans cette borne un passage
#: planifie tournerait quinze heures et chevaucherait le suivant.
BATCH = 12


def players(settings, limit: int = BATCH) -> list[tuple[str, str]]:
    """Les joueurs a traiter, **les lots a venir d'abord**.

    L'ordre est ce qui rend une interruption acceptable : ce sont les joueurs
    utiles aujourd'hui qui sont couverts, et le reste du catalogue attend le
    passage suivant.
    """
    a_venir = serve_stats_service.upcoming_players(settings)
    vus = set(a_venir)
    suite = [couple for couple in serve_stats_service.known_players(settings) if couple not in vus]
    return [*a_venir, *suite][: max(1, int(limit))]


async def run(limit: int = BATCH, settings=None) -> serve_stats_service.SyncReport:
    """Un passage. Rend son releve, et journalise les quatre taux."""
    settings = settings or get_settings()
    if not settings.rapidapi_key:
        logger.warning("Timelines : aucune cle RapidAPI, passe non lancee")
        return serve_stats_service.SyncReport()

    file = players(settings, limit)
    async with httpx.AsyncClient() as http:
        report = await serve_stats_service.sync(
            TennisAPIClient(http, settings), file, settings, with_games=True
        )

    tentees = sum(item[2].attempted for item in report.timelines)
    obtenues = sum(item[2].obtained for item in report.timelines)
    vides = sum(item[2].empty for item in report.timelines)
    alternance = sum(item[2].alternation for item in report.timelines)
    hors_fenetre = sum(item[2].too_old for item in report.timelines)
    au_seuil = sum(1 for item in report.timelines if item[2].reached)
    # **Les rencontres hors fenetre sont dites, jamais tues.** Un filtre qui
    # travaille en silence est indiscernable d'une source qui s'assechue : c'est
    # ce compte, rapporte aux tentees, qui permettra de rouvrir la question le
    # jour ou la retention de la source aura bouge.
    logger.info(
        "Timelines : %d joueur(s), %d rencontre(s) tentee(s), %d obtenue(s), "
        "%d vide(s), %d rupture(s) d'alternance, %d hors fenetre d'age, "
        "%d au seuil, %d appel(s)",
        len(report.timelines),
        tentees,
        obtenues,
        vides,
        alternance,
        hors_fenetre,
        au_seuil,
        report.calls,
    )
    if report.rejects:
        ingestion_service.record(None, report.rejects, settings)
    return report


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s — %(message)s")
    parser = argparse.ArgumentParser(description="Reprise des timelines de service (tennis).")
    parser.add_argument(
        "--joueurs", type=int, default=BATCH, help=f"joueurs a traiter (defaut {BATCH})"
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.joueurs))
    print(report.line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
