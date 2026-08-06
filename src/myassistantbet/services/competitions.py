"""Gestion des competitions scannees.

Le tennis n'est couvert par The Odds API que pendant les tournois, et les cles de
competition changent d'une saison a l'autre. Plutot que de figer une liste dans
une migration, on la synchronise depuis `GET /sports` — **endpoint gratuit**
(SPEC.md section 4), donc sans consequence sur le quota.

Une competition decouverte est creee **inactive** : rien ne se met a couter des
credits sans une decision explicite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from ..providers.oddsapi import OddsAPIClient
from .labels import sort_key, sport_emoji

logger = logging.getLogger(__name__)

#: Prefixes de cles The Odds API par sport interne. Le cyclisme n'est pas couvert.
SPORT_PREFIXES = {"soccer_": "football", "tennis_": "tennis"}

#: Surfaces de tennis. Elles decident quel Elo de surface est rendu dans le
#: bloc CONTEXTE ; laissee vide, seul l'Elo general apparait. Aucune deduction
#: automatique depuis le libelle du tournoi : ce serait une invention.
SURFACES = {"hard": "Dur", "clay": "Terre battue", "grass": "Gazon"}

#: Niveaux de tournoi, du plus releve au plus modeste. Un Grand Chelem se joue
#: au meilleur des cinq manches chez les hommes sur un tableau de 128 ; un 250
#: se joue en deux manches gagnantes sur un tableau de 28. Melanger leurs taux
#: de reussite produit un chiffre qui ne decrit ni l'un ni l'autre.
#:
#: `masters_1000` couvre les Masters 1000 de l'ATP **et** les WTA 1000 : c'est
#: le meme etage de la hierarchie, et le circuit se lit deja dans le libelle.
#: Les separer diviserait par deux des echantillons deja courts.
CATEGORIES = {
    "grand_slam": "Grand Chelem",
    "finals": "Masters de fin d'année",
    "masters_1000": "Masters 1000",
    "level_500": "ATP/WTA 500",
    "level_250": "ATP/WTA 250",
    "challenger": "Challenger / WTA 125",
    "itf": "ITF",
}

#: Rang d'affichage d'un niveau. Une competition sans niveau ferme la marche
#: plutot que de s'intercaler au hasard.
CATEGORY_ORDER = {key: index for index, key in enumerate(CATEGORIES)}


def category_label(key: str | None) -> str:
    """Libelle d'un niveau, chaine vide s'il n'est pas renseigne."""
    return CATEGORIES.get(key or "", "")


def category_rank(key: str | None) -> int:
    """Rang de tri d'un niveau. Les non renseignes passent en dernier."""
    return CATEGORY_ORDER.get(key or "", len(CATEGORIES))


@dataclass
class SyncReport:
    """Bilan d'une synchronisation depuis `/sports`."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    ignored: int = 0
    #: Competitions que l'API ne sert pas en ce moment — hors saison, ou pas
    #: encore ouvertes aux paris. Elles existent et peuvent etre activees
    #: d'avance : le jour ou les cotes arrivent, le scan les prend.
    dormant: int = 0

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)


def _sport_key_for(oddsapi_key: str) -> str | None:
    for prefix, sport in SPORT_PREFIXES.items():
        if oddsapi_key.startswith(prefix):
            return sport
    return None


def list_all(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Toutes les competitions, actives d'abord, pour l'ecran de gestion.

    Rangees ensuite par sport puis **par niveau** : les Grands Chelems avant les
    Masters 1000, avant les 500. Sur quarante tournois de tennis, l'ordre
    alphabetique melangeait un Grand Chelem et un 500 sans que rien ne le dise.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.oddsapi_key, c.apifootball_league_id, c.priority, "
            "       c.active, c.api_active, c.notes, c.surface, c.category, "
            "       c.tennisdata_tournaments, "
            "       s.id AS sport_order, s.key AS sport_key, s.label AS sport_label "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id"
        ).fetchall()
    competitions = [
        {
            **dict(row),
            "sport_emoji": sport_emoji(row["sport_key"]),
            "category_label": category_label(row["category"]),
        }
        for row in rows
    ]
    competitions.sort(
        key=lambda row: (
            not row["active"],
            row["sport_order"],
            category_rank(row["category"]),
            -row["priority"],
            sort_key(row["label"]),
        )
    )
    return competitions


def set_active(competition_id: int, active: bool, settings: Settings | None = None) -> None:
    """Active ou desactive une competition. Seules les actives sont scannees."""
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET active = ? WHERE id = ?", (1 if active else 0, competition_id)
        )


def set_notes(competition_id: int, notes: str, settings: Settings | None = None) -> None:
    """Enregistre la fiche d'une competition : format, phase, enjeu, particularites.

    Ce texte entre tel quel dans le prompt, une fois par lot. Il tient lieu de
    ce qu'aucune API ne donne — qu'une coupe se joue en aller-retour, qu'un
    championnat vient de reprendre, qu'une competition se dispute a huis clos.
    Vide, la fiche disparait plutot que d'occuper une ligne pour rien.
    """
    cleaned = (notes or "").strip()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET notes = ? WHERE id = ?",
            (cleaned or None, competition_id),
        )
    logger.info(
        "Fiche de competition %d : %s",
        competition_id,
        f"{len(cleaned)} caracteres" if cleaned else "effacee",
    )


def set_surface(competition_id: int, surface: str, settings: Settings | None = None) -> None:
    """Fixe la surface d'une competition de tennis.

    Une valeur inconnue est traitee comme « non renseignee » plutot que refusee :
    le seul effet est de ne rendre que l'Elo general, ce qui n'a rien de grave.
    """
    value = (surface or "").strip().lower()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET surface = ? WHERE id = ?",
            (value if value in SURFACES else None, competition_id),
        )
    logger.info("Surface de la competition %d : %s", competition_id, value or "non renseignee")


def set_tennisdata_tournaments(
    competition_id: int, tournaments: str, settings: Settings | None = None
) -> None:
    """Rattache une competition de tennis a son ou ses noms dans le jeu de donnees.

    La source nomme les tournois par leur sponsor, nous par leur ville ou leur nom
    usuel : rien ne se deduit d'un libelle, la saisie est manuelle. Plusieurs noms
    se separent par `|` — un sponsor qui change renomme le tournoi sans que ce
    soit un autre tournoi.

    Vide efface la correspondance : les lignes « ici » disparaissent alors, ce qui
    vaut mieux qu'un palmares emprunte a un autre tournoi.
    """
    names = [name.strip() for name in (tournaments or "").split("|") if name.strip()]
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET tennisdata_tournaments = ? WHERE id = ?",
            ("|".join(names) or None, competition_id),
        )
    logger.info(
        "Tournois du jeu de donnees pour la competition %d : %s",
        competition_id,
        ", ".join(names) or "aucun",
    )


def set_category(competition_id: int, category: str, settings: Settings | None = None) -> None:
    """Fixe le niveau d'une competition.

    Comme la surface, elle se saisit a la main : deduire « Masters 1000 » du mot
    « Masters » dans un libelle marcherait pour Monte-Carlo et se tromperait sur
    le Masters de fin d'annee. Une valeur inconnue vaut « non renseigne » plutot
    qu'une erreur : le seul effet est une ligne de moins dans les statistiques.
    """
    value = (category or "").strip().lower()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET category = ? WHERE id = ?",
            (value if value in CATEGORIES else None, competition_id),
        )
    logger.info("Niveau de la competition %d : %s", competition_id, value or "non renseigne")


#: Correspondance entre une cle The Odds API et une ligue API-Football.
#:
#: Sans elle, une competition decouverte par la synchronisation arrive sans
#: identifiant de ligue, `enrich.context_possible` est faux, et elle reste
#: muette sans que rien ne le signale — c'est exactement ce qui s'est produit
#: pour les qualifications europeennes.
#:
#: Chaque valeur a ete relevee dans le catalogue `/leagues` du fournisseur,
#: filtre par pays, et verifiee ligne a ligne. Le rapprochement automatique par
#: libelle a ete essaye et rejete : il proposait la Championship ecossaise (180)
#: pour l'anglaise (40), la Bundesliga (78) pour la 2. Bundesliga (79) et la
#: Coupe de Malaisie (499) pour la MLS (253), le tout avec un score maximal.
#: Une cle absente d'ici n'est pas devinee : elle se saisit depuis /competitions.
#:
#: Les competitions UEFA couvrent leurs tours preliminaires (`round =
#: "3rd Qualifying Round"`) : il n'existe pas d'identifiant distinct pour la
#: qualification, la ou The Odds API en a une cle separee.
APIFOOTBALL_LEAGUES: dict[str, int] = {
    "soccer_austria_bundesliga": 218,
    "soccer_belgium_first_div": 144,
    "soccer_brazil_campeonato": 71,
    "soccer_china_superleague": 169,
    "soccer_denmark_superliga": 119,
    "soccer_efl_champ": 40,
    "soccer_epl": 39,
    "soccer_finland_veikkausliiga": 244,
    "soccer_france_ligue_one": 61,
    "soccer_france_ligue_two": 62,
    "soccer_germany_bundesliga": 78,
    "soccer_germany_bundesliga2": 79,
    "soccer_germany_liga3": 80,
    "soccer_greece_super_league": 197,
    "soccer_italy_serie_a": 135,
    "soccer_italy_serie_b": 136,
    "soccer_netherlands_eredivisie": 88,
    "soccer_norway_eliteserien": 103,
    "soccer_poland_ekstraklasa": 106,
    "soccer_portugal_primeira_liga": 94,
    "soccer_spain_la_liga": 140,
    "soccer_spain_segunda_division": 141,
    "soccer_spl": 179,
    "soccer_sweden_allsvenskan": 113,
    "soccer_sweden_superettan": 114,
    "soccer_switzerland_superleague": 207,
    "soccer_turkey_super_league": 203,
    "soccer_uefa_champs_league": 2,
    "soccer_uefa_champs_league_qualification": 2,
    "soccer_uefa_europa_conference_league": 848,
    "soccer_uefa_europa_league": 3,
    "soccer_uefa_nations_league": 5,
    "soccer_usa_mls": 253,
}


#: Nom de chaque tournoi de tennis dans le jeu de donnees de resultats
#: (tennis-data.co.uk). Plusieurs noms se separent par `|` : un sponsor qui change
#: renomme le tournoi sans que ce soit un autre tournoi.
#:
#: Meme regle que `APIFOOTBALL_LEAGUES` : **rien ne se deduit d'un libelle**, et la
#: table est verifiee a la main tournoi par tournoi, contre la ville et le niveau
#: publies par la source. La mesure justifie cette severite — la **ville** ne
#: suffit pas (Paris heberge le BNP Paribas Masters *et* Roland-Garros), le **nom**
#: non plus (le Canadian Open change de ville chaque annee), et onze villes portent
#: plusieurs noms de tournoi. Le circuit, lui, se lit dans la cle et departage
#: Cincinnati et Stuttgart, ou les epreuves masculine et feminine ont des noms
#: differents dans la meme ville.
#:
#: Le seed de la migration 020 applique cette table a l'existant ; la
#: synchronisation la reapplique a ce qui se cree ensuite, sans jamais ecraser une
#: saisie manuelle.
TENNISDATA_TOURNAMENTS: dict[str, str] = {
    "tennis_atp_aus_open_singles": "Australian Open",
    "tennis_atp_barcelona_open": "Barcelona Open",
    "tennis_atp_canadian_open": "Canadian Open",
    "tennis_atp_china_open": "China Open",
    "tennis_atp_cincinnati_open": "Western & Southern Financial Group Masters",
    "tennis_atp_dubai": "Dubai Tennis Championships",
    "tennis_atp_french_open": "French Open",
    "tennis_atp_halle_open": "Halle Open",
    "tennis_atp_hamburg_open": "Hamburg Open",
    "tennis_atp_indian_wells": "BNP Paribas Open",
    "tennis_atp_italian_open": "Internazionali BNL d'Italia",
    "tennis_atp_madrid_open": "Mutua Madrid Open",
    "tennis_atp_miami_open": "Miami Open",
    "tennis_atp_monte_carlo_masters": "Monte Carlo Masters",
    "tennis_atp_munich": "BMW Open",
    "tennis_atp_paris_masters": "BNP Paribas Masters",
    "tennis_atp_qatar_open": "Qatar Exxon Mobil Open",
    "tennis_atp_queens_club_champ": "Queen's Club Championships",
    "tennis_atp_shanghai_masters": "Shanghai Masters",
    "tennis_atp_us_open": "US Open",
    "tennis_atp_washington_open": "Citi Open",
    "tennis_atp_wimbledon": "Wimbledon",
    "tennis_wta_aus_open_singles": "Australian Open",
    "tennis_wta_bad_homburg_open": "Bad Homburg Open",
    "tennis_wta_canadian_open": "Canadian Open",
    "tennis_wta_charleston_open": "Charleston Open",
    "tennis_wta_china_open": "China Open",
    "tennis_wta_cincinnati_open": "Western & Southern Financial Group Women's Open",
    "tennis_wta_dubai": "Dubai Duty Free Tennis Championships",
    "tennis_wta_french_open": "French Open",
    "tennis_wta_german_open": "German Open",
    "tennis_wta_indian_wells": "BNP Paribas Open",
    "tennis_wta_italian_open": "Internazionali BNL d'Italia",
    "tennis_wta_madrid_open": "Mutua Madrid Open",
    "tennis_wta_miami_open": "Miami Open",
    "tennis_wta_qatar_open": "Qatar Open",
    "tennis_wta_queens_club_champ": "Queen's Club Championships",
    "tennis_wta_strasbourg": "Internationaux de Strasbourg",
    "tennis_wta_stuttgart_open": "Porsche Tennis Grand Prix",
    "tennis_wta_us_open": "US Open",
    "tennis_wta_washington_open": "Citi Open",
    "tennis_wta_wimbledon": "Wimbledon",
    "tennis_wta_wuhan_open": "Wuhan Open",
}


def set_apifootball_league(
    competition_id: int, league_id: str, settings: Settings | None = None
) -> None:
    """Rattache une competition de football a une ligue API-Football.

    Sans ce rattachement aucun contexte n'est demande, et la competition reste
    muette sans que rien ne le signale. La synchronisation depuis /sports cree
    les competitions sans identifiant : il faut donc pouvoir le saisir ici,
    sinon toute competition decouverte apres coup exige une migration.

    Une saisie illisible vaut « non rattachee » plutot qu'une erreur : l'effet
    est une ligne de contexte absente, jamais une donnee fausse. En revanche un
    identifiant n'est **jamais devine** a partir du libelle — un rapprochement
    automatique donne la Championship ecossaise pour l'anglaise avec le meme
    aplomb que la bonne reponse.
    """
    raw = (league_id or "").strip()
    value: int | None = None
    if raw.isdigit() and int(raw) > 0:
        value = int(raw)
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET apifootball_league_id = ? WHERE id = ?",
            (value, competition_id),
        )
    logger.info(
        "Ligue API-Football de la competition %d : %s", competition_id, value or "non rattachee"
    )


async def sync_from_api(client: OddsAPIClient, settings: Settings | None = None) -> SyncReport:
    """Aligne la table `competitions` sur le catalogue **complet** de The Odds API.

    Gratuit : `/sports` ne consomme aucun credit. N'active jamais rien de
    lui-meme et ne desactive jamais une competition existante.

    `include_inactive` est indispensable : sans lui, seules les competitions
    que le fournisseur sert a l'instant sont decouvertes. Une phase de
    qualification europeenne ou un tournoi qui ouvre dans trois jours reste
    alors introuvable jusqu'a ce que les cotes arrivent — donc trop tard pour
    l'activer avant les premiers matchs.
    """
    settings = settings or get_settings()
    report = SyncReport()

    sports = await client.get_sports(include_inactive=True)
    with connect(settings) as conn:
        sport_ids = {
            row["key"]: int(row["id"]) for row in conn.execute("SELECT id, key FROM sports")
        }

        for entry in sports:
            oddsapi_key = entry.get("key")
            title = entry.get("title") or oddsapi_key
            if not oddsapi_key:
                continue
            sport_key = _sport_key_for(oddsapi_key)
            if sport_key is None or sport_key not in sport_ids:
                report.ignored += 1
                continue

            served = 1 if entry.get("active") else 0
            if not served:
                report.dormant += 1

            league_id = APIFOOTBALL_LEAGUES.get(oddsapi_key)
            tournaments = TENNISDATA_TOURNAMENTS.get(oddsapi_key)
            existing = conn.execute(
                "SELECT id, label, api_active, apifootball_league_id, tennisdata_tournaments "
                "FROM competitions WHERE oddsapi_key = ?",
                (oddsapi_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
                    "                          api_active, apifootball_league_id, "
                    "                          tennisdata_tournaments) "
                    "VALUES (?, ?, ?, 0, 0, ?, ?, ?)",
                    (sport_ids[sport_key], oddsapi_key, title, served, league_id, tournaments),
                )
                report.created.append(f"{title} ({oddsapi_key})")
                continue

            if league_id is not None and existing["apifootball_league_id"] is None:
                # Comble un manque, n'ecrase jamais une saisie : un rattachement
                # corrige a la main prime pour toujours, comme un alias d'equipe.
                conn.execute(
                    "UPDATE competitions SET apifootball_league_id = ? WHERE id = ?",
                    (league_id, existing["id"]),
                )

            if tournaments is not None and existing["tennisdata_tournaments"] is None:
                # Meme regle que le rattachement de ligue : comble un manque,
                # n'ecrase jamais une saisie.
                conn.execute(
                    "UPDATE competitions SET tennisdata_tournaments = ? WHERE id = ?",
                    (tournaments, existing["id"]),
                )

            if existing["label"] != title or existing["api_active"] != served:
                # Le libelle et la disponibilite du fournisseur font foi ;
                # l'activation choisie par l'utilisateur ne bouge jamais.
                conn.execute(
                    "UPDATE competitions SET label = ?, api_active = ? WHERE id = ?",
                    (title, served, existing["id"]),
                )
                if existing["label"] != title:
                    report.updated.append(f"{title} ({oddsapi_key})")

    logger.info(
        "Synchronisation des competitions : %d creees, %d mises a jour, %d ignorees, "
        "%d non servies actuellement",
        len(report.created),
        len(report.updated),
        report.ignored,
        report.dormant,
    )
    return report
