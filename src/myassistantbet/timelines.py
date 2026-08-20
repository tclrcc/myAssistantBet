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
from .services import palmares as palmares_service
from .services import serve_stats as serve_stats_service

logger = logging.getLogger(__name__)

#: Combien de joueurs au plus par passage. **Une borne de temps de mur, pas de
#: quota** : le plancher garde le second, et sans cette borne un passage
#: planifie tournerait quinze heures et chevaucherait le suivant.
BATCH = 12


#: Combien de lots analyses recents forment l'etage intermediaire de la file.
RECENT_LOTS = 5


def players(settings, limit: int = BATCH) -> list[tuple[str, str]]:
    """Les joueurs a traiter, **en trois etages et dans cet ordre**.

    L'ordre est ce qui rend une interruption acceptable — une passe longue
    s'interrompt, et ce qu'elle laisse derriere doit etre ce qui sert le moins :

    1. les joueurs des matchs **a venir** : ceux dont un bloc se rendra demain ;
    2. ceux des `RECENT_LOTS` derniers **lots analyses** : ils reviennent, un
       tournoi durant une semaine et un joueur y jouant plusieurs tours ;
    3. le reste du catalogue.

    `limit` a zero ou moins ne borne rien. **C'est une borne de temps de mur, pas
    de quota** — le plancher garde le second, et le confondre ferait chercher un
    garde-fou de credit dans un nombre de joueurs.
    """
    a_venir = serve_stats_service.upcoming_players(settings)
    vus = set(a_venir)
    recents = [
        couple
        for couple in serve_stats_service.recent_players(RECENT_LOTS, settings)
        if couple not in vus
    ]
    vus |= set(recents)
    suite = [couple for couple in serve_stats_service.known_players(settings) if couple not in vus]
    file = [*a_venir, *recents, *suite]
    return file if int(limit) <= 0 else file[: int(limit)]


async def run(
    limit: int = BATCH, settings=None, force: bool = False
) -> serve_stats_service.SyncReport:
    """Un passage. Rend son releve, et journalise les cinq taux."""
    settings = settings or get_settings()
    if not settings.rapidapi_key:
        logger.warning("Timelines : aucune cle RapidAPI, passe non lancee")
        return serve_stats_service.SyncReport()

    file = players(settings, limit)
    logger.info(
        "Timelines : file de %d joueur(s) — %d a venir, %d des %d derniers lots, "
        "%d de fond de catalogue",
        len(file),
        len(serve_stats_service.upcoming_players(settings)),
        len(serve_stats_service.recent_players(RECENT_LOTS, settings)),
        RECENT_LOTS,
        len(serve_stats_service.known_players(settings)),
    )
    async with httpx.AsyncClient() as http:
        client = TennisAPIClient(http, settings)
        report = await serve_stats_service.sync(
            client, file, settings, with_games=True, force=force
        )
        # **Le palmares profond passe par la meme file, et pas par la meme
        # borne.** Il pagine l'historique entier d'un joueur, ce qui coute une
        # mediane de 3 appels et se perime a la semaine ; une timeline en coute
        # quatre a six **par rencontre**. `BATCH` borne le temps de mur de la
        # seconde, et l'appliquer au premier le rendait invisible sur la
        # majorite des lots.
        #
        # Mesure du 20/08/2026 sur les 18 journees de board archivees :
        # **mediane 32 joueurs de tennis par journee, maximum 99**, et `BATCH`
        # (12) ne couvre la journee que **4 fois sur 18**. Le palmares ne sortait
        # donc que sur les douze premiers joueurs a venir. Cout de la levee, aux
        # memes chiffres : ~96 appels par jour, ~3 000 par mois, contre 139 411
        # restants sur 150 000 — 2 % du quota.
        #
        # **Les joueurs a venir, jamais tout le catalogue** : 256 joueurs y
        # feraient 768 appels pour rafraichir des profils qui ne jouent pas.
        #
        # **Sans ce branchement, `player_palmares` serait ecrite par personne** —
        # la faute de `/players/squads`, collecte des mois sans lecteur.
        repris = await palmares_service.refresh(
            client, serve_stats_service.upcoming_players(settings), settings
        )
        logger.info("Palmares : %d joueur(s) repris", repris)

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
        "--joueurs",
        type=int,
        default=BATCH,
        help=f"joueurs a traiter, 0 pour ne pas borner (defaut {BATCH})",
    )
    parser.add_argument(
        "--reprise",
        action="store_true",
        help=(
            "passe longue : leve la peremption de l'agregat, qui ne dit rien "
            "des timelines. Le plancher de quota borne toujours."
        ),
    )
    args = parser.parse_args()
    report = asyncio.run(run(args.joueurs, force=args.reprise))
    print(report.line)
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
