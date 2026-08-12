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
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings, get_settings
from ..db import connect
from ..providers.oddsapi import OddsAPIClient
from .labels import sort_key

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
TENNIS_CATEGORIES = {
    "grand_slam": "Grand Chelem",
    "finals": "Masters de fin d'année",
    "masters_1000": "Masters 1000",
    "level_500": "ATP/WTA 500",
    "level_250": "ATP/WTA 250",
    "challenger": "Challenger / WTA 125",
    "itf": "ITF",
}

#: Meme echelle au football, et elle repare un angle mort mesure : le niveau
#: n'etait renseigne que sur le tennis, si bien que le regroupement « par
#: niveau » portait exactement l'effectif du tennis. Les 59 selections
#: football tranchees se repartissaient sur douze championnats, chacun sous le
#: seuil de lecture — donc invisibles a **tous** les etages d'agregation : trop
#: fines par competition, noyees par sport.
#:
#: Le decoupage suit ce qui change la lecture d'un match, pas la geographie :
#: un championnat du top 5 se joue devant des donnees abondantes et un marche
#: serre ; une premiere division scandinave, non. Une coupe continentale melange
#: deux niveaux economiques dans un format aller-retour.
#:
#: `d2` couvre la deuxieme division **et en dessous** — League 2 anglaise,
#: 3. Liga allemande. Les separer creerait des niveaux qu'aucune selection ne
#: peuple, et le libelle le dit plutot que de laisser croire a un pur echelon 2.
FOOTBALL_CATEGORIES = {
    "d1_top5": "1re division — top 5",
    "d1_europe": "1re division — Europe",
    "d1_hors_europe": "1re division — hors Europe",
    "d2": "2e division et moins",
    "coupe_nationale": "Coupe nationale",
    "coupe_continentale": "Coupe continentale",
    "selection": "Sélections",
}

#: Les niveaux proposes pour un sport donne. Ils ne se melangent pas : « ATP/WTA
#: 500 » sur une Ligue 1 n'a aucun sens, et l'ecran de gestion comme la
#: validation de la saisie lisent cette table plutot que la liste a plat.
#: Un sport absent n'a pas de taxonomie — le cyclisme n'en a jamais eu — et ses
#: competitions ne sont donc jamais reclamees dans la liste « a classer ».
CATEGORIES_BY_SPORT = {
    "tennis": TENNIS_CATEGORIES,
    "football": FOOTBALL_CATEGORIES,
}

#: Tous les niveaux a plat, pour les lectures qui n'ont que la cle sous la main
#: — un libelle a rendre, un rang de tri. Les cles sont uniques d'un sport a
#: l'autre, sans quoi cette fusion perdrait silencieusement une entree ; un test
#: le verifie.
CATEGORIES = {**TENNIS_CATEGORIES, **FOOTBALL_CATEGORIES}

#: Niveaux ou une **double confrontation** existe, donc ou le marche « Se
#: qualifie » a un sens. Il ne se demande que la : sur un championnat il ne
#: serait jamais servi, et le reclamer couterait un credit par match pour un
#: constat vide — que `coverage` memoriserait ensuite, mais apres l'avoir paye.
#:
#: Le niveau est deja saisi et maintenu ; en tirer cette liste evite une seconde
#: table qui aurait diverge de la premiere.
KNOCKOUT_CATEGORIES = frozenset({"coupe_continentale", "coupe_nationale"})


#: Rang d'affichage d'un niveau. Une competition sans niveau ferme la marche
#: plutot que de s'intercaler au hasard. L'ordre de `CATEGORIES` groupe les
#: niveaux par sport, ce qui suffit a ne pas intercaler un Grand Chelem entre
#: deux divisions.
CATEGORY_ORDER = {key: index for index, key in enumerate(CATEGORIES)}


def category_label(key: str | None) -> str:
    """Libelle d'un niveau, chaine vide s'il n'est pas renseigne."""
    return CATEGORIES.get(key or "", "")


def is_knockout(category: str | None) -> bool:
    """La competition se joue-t-elle a elimination directe ?

    Lue sur le **niveau**, deja saisi et maintenu : une seconde table aurait
    diverge de la premiere, et rien ne se deduit d'un libelle. Un niveau non
    renseigne rend faux — donc aucun credit depense sur une supposition.
    """
    return (category or "") in KNOCKOUT_CATEGORIES


def categories_for(sport_key: str | None) -> dict[str, str]:
    """Niveaux proposes pour un sport. Vide s'il n'a pas de taxonomie."""
    return CATEGORIES_BY_SPORT.get(sport_key or "", {})


#: Niveau connu par cle The Odds API. Meme role que `APIFOOTBALL_LEAGUES`, et
#: pour la meme raison : les migrations 013 et 024 ne classent que les
#: competitions **deja en base au moment ou elles tournent**, et la
#: synchronisation en decouvre en permanence. Sans cette table, chaque
#: competition apparue apres le seed arriverait sans niveau — donc reclamee dans
#: la liste « a classer » alors que sa place ne fait aucun doute.
#:
#: Rien n'y est deduit d'un libelle, ici non plus : chaque ligne est une
#: decision humaine, verifiee cle par cle contre les calendriers. Une cle absente
#: se saisit depuis /competitions.
#:
#: Les deux migrations rejouent exactement cette table ; un test compare les
#: trois ecritures plutot que de faire confiance a une relecture.
COMPETITION_CATEGORIES: dict[str, str] = {
    # -- Tennis (seed de la migration 013) --------------------------------
    "tennis_atp_aus_open_singles": "grand_slam",
    "tennis_atp_french_open": "grand_slam",
    "tennis_atp_wimbledon": "grand_slam",
    "tennis_atp_us_open": "grand_slam",
    "tennis_wta_aus_open_singles": "grand_slam",
    "tennis_wta_french_open": "grand_slam",
    "tennis_wta_wimbledon": "grand_slam",
    "tennis_wta_us_open": "grand_slam",
    "tennis_atp_indian_wells": "masters_1000",
    "tennis_atp_miami_open": "masters_1000",
    "tennis_atp_monte_carlo_masters": "masters_1000",
    "tennis_atp_madrid_open": "masters_1000",
    "tennis_atp_italian_open": "masters_1000",
    "tennis_atp_canadian_open": "masters_1000",
    "tennis_atp_cincinnati_open": "masters_1000",
    "tennis_atp_shanghai_masters": "masters_1000",
    "tennis_atp_paris_masters": "masters_1000",
    "tennis_wta_qatar_open": "masters_1000",
    "tennis_wta_dubai": "masters_1000",
    "tennis_wta_indian_wells": "masters_1000",
    "tennis_wta_miami_open": "masters_1000",
    "tennis_wta_madrid_open": "masters_1000",
    "tennis_wta_italian_open": "masters_1000",
    "tennis_wta_canadian_open": "masters_1000",
    "tennis_wta_cincinnati_open": "masters_1000",
    "tennis_wta_china_open": "masters_1000",
    "tennis_wta_wuhan_open": "masters_1000",
    "tennis_atp_dubai": "level_500",
    "tennis_atp_qatar_open": "level_500",
    "tennis_atp_barcelona_open": "level_500",
    "tennis_atp_munich": "level_500",
    "tennis_atp_hamburg_open": "level_500",
    "tennis_atp_queens_club_champ": "level_500",
    "tennis_atp_halle_open": "level_500",
    "tennis_atp_washington_open": "level_500",
    "tennis_atp_china_open": "level_500",
    "tennis_wta_german_open": "level_500",
    "tennis_wta_charleston_open": "level_500",
    "tennis_wta_stuttgart_open": "level_500",
    "tennis_wta_strasbourg": "level_500",
    "tennis_wta_bad_homburg_open": "level_500",
    "tennis_wta_queens_club_champ": "level_500",
    "tennis_wta_washington_open": "level_500",
    # -- Football (seed de la migration 024) ------------------------------
    "soccer_epl": "d1_top5",
    "soccer_spain_la_liga": "d1_top5",
    "soccer_italy_serie_a": "d1_top5",
    "soccer_germany_bundesliga": "d1_top5",
    "soccer_france_ligue_one": "d1_top5",
    "soccer_austria_bundesliga": "d1_europe",
    "soccer_belgium_first_div": "d1_europe",
    "soccer_denmark_superliga": "d1_europe",
    "soccer_finland_veikkausliiga": "d1_europe",
    "soccer_germany_bundesliga_women": "d1_europe",
    "soccer_greece_super_league": "d1_europe",
    "soccer_league_of_ireland": "d1_europe",
    "soccer_netherlands_eredivisie": "d1_europe",
    "soccer_norway_eliteserien": "d1_europe",
    "soccer_poland_ekstraklasa": "d1_europe",
    "soccer_portugal_primeira_liga": "d1_europe",
    "soccer_russia_premier_league": "d1_europe",
    "soccer_spl": "d1_europe",
    "soccer_sweden_allsvenskan": "d1_europe",
    "soccer_switzerland_superleague": "d1_europe",
    "soccer_turkey_super_league": "d1_europe",
    "soccer_argentina_primera_division": "d1_hors_europe",
    "soccer_australia_aleague": "d1_hors_europe",
    "soccer_brazil_campeonato": "d1_hors_europe",
    "soccer_chile_campeonato": "d1_hors_europe",
    "soccer_china_superleague": "d1_hors_europe",
    "soccer_japan_j_league": "d1_hors_europe",
    "soccer_korea_kleague1": "d1_hors_europe",
    "soccer_mexico_ligamx": "d1_hors_europe",
    "soccer_saudi_arabia_pro_league": "d1_hors_europe",
    "soccer_usa_mls": "d1_hors_europe",
    "soccer_brazil_serie_b": "d2",
    "soccer_efl_champ": "d2",
    "soccer_england_league1": "d2",
    "soccer_england_league2": "d2",
    "soccer_france_ligue_two": "d2",
    "soccer_germany_bundesliga2": "d2",
    "soccer_germany_liga3": "d2",
    "soccer_italy_serie_b": "d2",
    "soccer_spain_segunda_division": "d2",
    "soccer_sweden_superettan": "d2",
    "soccer_england_efl_cup": "coupe_nationale",
    "soccer_fa_cup": "coupe_nationale",
    "soccer_france_coupe_de_france": "coupe_nationale",
    "soccer_germany_dfb_pokal": "coupe_nationale",
    "soccer_italy_coppa_italia": "coupe_nationale",
    "soccer_spain_copa_del_rey": "coupe_nationale",
    "soccer_concacaf_leagues_cup": "coupe_continentale",
    "soccer_conmebol_copa_libertadores": "coupe_continentale",
    "soccer_conmebol_copa_sudamericana": "coupe_continentale",
    "soccer_fifa_club_world_cup": "coupe_continentale",
    "soccer_uefa_champs_league": "coupe_continentale",
    "soccer_uefa_champs_league_qualification": "coupe_continentale",
    "soccer_uefa_champs_league_women": "coupe_continentale",
    "soccer_uefa_europa_conference_league": "coupe_continentale",
    "soccer_uefa_europa_league": "coupe_continentale",
    "soccer_africa_cup_of_nations": "selection",
    "soccer_concacaf_gold_cup": "selection",
    "soccer_conmebol_copa_america": "selection",
    "soccer_fifa_world_cup": "selection",
    "soccer_fifa_world_cup_qualifiers_europe": "selection",
    "soccer_fifa_world_cup_qualifiers_south_america": "selection",
    "soccer_fifa_world_cup_winner": "selection",
    "soccer_fifa_world_cup_womens": "selection",
    "soccer_uefa_euro_qualification": "selection",
    "soccer_uefa_european_championship": "selection",
    "soccer_uefa_nations_league": "selection",
}


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
            "       c.tennisdata_tournaments, c.timezone, c.city, "
            "       s.id AS sport_order, s.key AS sport_key, s.label AS sport_label "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id"
        ).fetchall()
    competitions = [
        {
            **dict(row),
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


def set_timezone(competition_id: int, timezone: str, settings: Settings | None = None) -> None:
    """Fixe le fuseau du lieu, pour dater un fait la ou il se produit.

    Un nom IANA (`America/Toronto`, `Europe/Warsaw`). Rien ne se deduit d'un
    libelle, meme regle que la surface : « Cincinnati Open » ne dit pas
    `America/New_York`, et une table de villes se tromperait le jour ou le
    tournoi demenage — le Canadian Open change de ville chaque annee.

    **Un fuseau illisible est refuse**, la ou la surface se contente d'etre
    ignoree. Le contraste est juste : une surface inconnue ne coute qu'une ligne
    d'Elo en moins, tandis qu'un fuseau accepte sans etre reconnu ferait rendre
    des heures en UTC sous le mot « local » — l'affirmation exactement inverse.
    """
    value = (timezone or "").strip()
    if value:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"Fuseau inconnu : {value}") from exc
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET timezone = ? WHERE id = ?", (value or None, competition_id)
        )
    logger.info("Fuseau de la competition %d : %s", competition_id, value or "non renseigne")


def set_city(competition_id: int, city: str, settings: Settings | None = None) -> None:
    """Fixe la ville d'une competition, pour la meteo du lieu.

    Rien ne se deduit d'un libelle, meme regle que la surface et le fuseau :
    « ATP Cincinnati Open » se joue a **Mason**, et le Canadian Open change de
    ville chaque annee. Vide efface la saisie — sans ville, aucune ligne de
    meteo, ce qui vaut mieux que la meteo d'ailleurs.

    Aucune validation possible ici : c'est le geocodage qui dira si la ville
    existe, et il refuse les homonymes que le pays ne departage pas.
    """
    value = (city or "").strip()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET city = ? WHERE id = ?", (value or None, competition_id)
        )
    logger.info("Ville de la competition %d : %s", competition_id, value or "non renseignee")


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

    La valeur est verifiee **contre la taxonomie du sport** et non contre la
    liste a plat : depuis que le football a la sienne, « ATP/WTA 500 » est une
    cle connue, et l'accepter sur une Ligue 1 produirait un regroupement que
    plus rien ne pourrait distinguer d'un vrai tournoi.
    """
    value = (category or "").strip().lower()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT s.key AS sport_key FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "WHERE c.id = ?",
            (competition_id,),
        ).fetchone()
        allowed = categories_for(row["sport_key"] if row else None)
        conn.execute(
            "UPDATE competitions SET category = ? WHERE id = ?",
            (value if value in allowed else None, competition_id),
        )
    logger.info("Niveau de la competition %d : %s", competition_id, value or "non renseigne")


@dataclass
class Unclassified:
    """Une competition sans niveau, et ce qu'elle porte deja d'historique.

    Une cle non classee ne doit jamais disparaitre en silence : sans niveau,
    ses selections sortent du regroupement « par niveau » sans qu'aucune ligne
    ne le dise, et c'est exactement ce qui a rendu 59 selections football
    invisibles pendant cent paris.
    """

    competition_id: int
    label: str
    sport_key: str
    sport_label: str
    #: Selections deja rattachees a cette competition, tranchees ou non. C'est
    #: ce qui range la liste : classer une competition qui porte onze paris
    #: repare onze lignes de statistiques, classer une competition vierge n'en
    #: repare aucune.
    picks: int = 0
    settled: int = 0
    active: bool = False


#: Fiche de depart d'une competition, par cle The Odds API.
#:
#: Un lot de cinq matchs portait trois fiches — Allsvenskan, Superliga danoise,
#: Primeira Liga — et **aucune pour l'EFL Cup**, qui etait le match le plus
#: atypique du lot : un tour de coupe anglaise est le format ou la rotation
#: d'effectif est la regle et non l'exception, exactement le fait de format et de
#: calendrier que ces fiches ont pour role de porter.
#:
#: Ce qui est ecrit ici est **structurel et durable** : nombre de manches, tour
#: d'entree des grands clubs, terrain du match, ecart de niveau attendu. Rien qui
#: change d'une saison a l'autre — un fait perime dans le prompt coute plus
#: qu'une fiche absente, et la phase en cours se lit deja sur le match.
#:
#: Les coupes n'en ont pas le monopole. Un **championnat** a lui aussi un format
#: a dire — ce que sa fin de saison met en jeu, a quel moment de l'annee il se
#: joue, si ses clubs se valent — et un **tournoi de tennis** en a deux de plus,
#: qui n'appartiennent qu'a lui : ce qui se dispute la semaine d'avant et la
#: semaine d'apres. Aucun de ces faits n'est dans le bloc CONTEXTE, qui decrit
#: un match et jamais la competition qui l'accueille.
#:
#: **Aucune migration ne les rejoue**, contrairement aux niveaux : c'est de la
#: prose de plusieurs lignes, et la tenir a jour des deux cotes la ferait
#: diverger au premier ajustement. La synchronisation comble le manque sur les
#: competitions existantes — elle tourne tous les jours avec le lot gratuit —
#: et n'ecrase jamais une fiche ecrite a la main.
COMPETITION_NOTES: dict[str, str] = {
    "soccer_england_efl_cup": (
        "Élimination directe, un match sec (demi-finales en aller-retour). Les clubs "
        "engagés en Europe entrent au 3e tour. Rotation d'effectif systématique sur les "
        "tours précoces : l'équipe alignée n'est pas celle du championnat, et les "
        "compositions sortent souvent tard."
    ),
    "soccer_fa_cup": (
        "Élimination directe, un match sec. Les clubs de Premier League et de Championship "
        "entrent au 3e tour. Écarts de division fréquents et rotation d'effectif sur les "
        "tours précoces."
    ),
    "soccer_france_coupe_de_france": (
        "Élimination directe, un match sec chez le club le moins bien classé : l'avantage "
        "du terrain va au petit. Les clubs de Ligue 1 entrent en 32es. Écarts de division "
        "extrêmes, pelouses et conditions très variables."
    ),
    "soccer_spain_copa_del_rey": (
        "Élimination directe, un match sec chez le club le moins bien classé "
        "(demi-finales en aller-retour). Écarts de division extrêmes sur les tours "
        "précoces, et rotation d'effectif marquée."
    ),
    "soccer_germany_dfb_pokal": (
        "Élimination directe, un match sec chez le club le moins bien classé, prolongation "
        "puis tirs au but. Les clubs de Bundesliga entrent au 1er tour et se déplacent chez "
        "des amateurs : écarts de niveau extrêmes."
    ),
    "soccer_italy_coppa_italia": (
        "Élimination directe, un match sec chez le mieux classé, prolongation puis tirs au "
        "but. Les têtes de série de Serie A entrent tard : les tours précoces opposent des "
        "clubs de niveaux proches."
    ),
    "soccer_concacaf_leagues_cup": (
        "Tournoi estival opposant clubs de MLS et de Liga MX. Décalage de préparation : la "
        "MLS est en pleine saison, le championnat mexicain en début de tournoi. Format "
        "court, pas de prolongation — tirs au but directs."
    ),
    # Les trois competitions UEFA couvrent leurs tours preliminaires sous la meme
    # cle : la fiche vaut donc pour la qualification comme pour la phase de ligue,
    # et c'est le tour du match qui dit ou l'on en est.
    "soccer_uefa_champs_league_qualification": (
        "Tours préliminaires en aller-retour, puis phase de ligue. En qualification, les "
        "écarts de niveau et de calendrier sont importants : un club déjà lancé en "
        "championnat affronte souvent un club en reprise ou en fin de saison. Le score de "
        "l'aller commande le scénario du retour."
    ),
    "soccer_uefa_europa_league": (
        "Tours préliminaires en aller-retour, puis phase de ligue. En qualification, les "
        "écarts de niveau et de calendrier sont importants : un club déjà lancé en "
        "championnat affronte souvent un club en reprise ou en fin de saison. Le score de "
        "l'aller commande le scénario du retour."
    ),
    "soccer_uefa_europa_conference_league": (
        "Tours préliminaires en aller-retour, puis phase de ligue. En qualification, les "
        "écarts de niveau et de calendrier sont importants : un club déjà lancé en "
        "championnat affronte souvent un club en reprise ou en fin de saison. Le score de "
        "l'aller commande le scénario du retour."
    ),
    # -- Championnats : le format, le calendrier et l'ecart de niveau attendu --
    #
    # Un championnat n'a pas de « tour d'entree » a dire, mais il a tout le
    # reste : ce que la fin de saison met en jeu, a quel moment de l'annee il se
    # joue, et si ses clubs se valent. Trois faits que le bloc CONTEXTE ne porte
    # pas — il decrit un match, jamais la competition qui l'accueille.
    "soccer_italy_serie_b": (
        "Championnat à matchs aller-retour. Les deux premiers montent directement, le "
        "troisième promu sort de play-offs à élimination directe ; le bas de tableau descend "
        "en Serie C, la dernière place de relégation se jouant en play-out. Écarts de niveau "
        "faibles et classement resserré, avec des journées en semaine qui imposent la "
        "rotation."
    ),
    "soccer_australia_aleague": (
        "Championnat à contre-saison de l'Europe : il se joue d'octobre à mai, l'été austral "
        "au milieu. Ligue fermée, sans montée ni descente — la fin de saison ne met en jeu "
        "que la qualification aux finales, disputées à élimination directe : le premier de la "
        "saison régulière n'est pas le champion. Plafond salarial et quota de joueurs "
        "étrangers resserrent les écarts entre clubs, et les déplacements sont énormes "
        "(Perth, Nouvelle-Zélande)."
    ),
    "soccer_saudi_arabia_pro_league": (
        "Championnat à matchs aller-retour, d'août à mai, avec montées et descentes vers la "
        "première division saoudienne. Depuis 2023, le recrutement international est "
        "concentré sur quelques clubs de tête : les écarts de niveau avec le bas de tableau "
        "sont parmi les plus marqués d'un championnat de première division. Chaleur et "
        "coups d'envoi tardifs en début de saison ; en mars, le calendrier se resserre autour "
        "du ramadan."
    ),
    # -- Tennis : le format d'un tournoi, sa place dans le calendrier ----------
    #
    # Meme role qu'au football, et deux faits de plus qui n'appartiennent qu'au
    # tennis : ce qui se joue la semaine d'avant, et ce qui se joue la semaine
    # d'apres. Un tournoi colle a un Grand Chelem ne se dispute pas comme un
    # tournoi isole.
    "tennis_atp_cincinnati_open": (
        "Masters 1000 sur dur extérieur, en deux manches gagnantes, joué à Mason (Ohio) en "
        "août. Tableau élargi étalé sur douze jours : les têtes de série sont exemptées du "
        "premier tour et les tours s'enchaînent moins vite qu'en une semaine. Il suit "
        "immédiatement le Masters canadien et précède l'US Open : forfaits de précaution et "
        "abandons y sont fréquents, et la fraîcheur pèse autant que la hiérarchie. Chaleur et "
        "humidité de l'Ohio, sensibles sur les sessions de journée."
    ),
    "tennis_wta_cincinnati_open": (
        "WTA 1000 sur dur extérieur, joué à Mason (Ohio) en août. Tableau élargi étalé sur "
        "douze jours : les têtes de série sont exemptées du premier tour et les tours "
        "s'enchaînent moins vite qu'en une semaine. Il suit immédiatement le tournoi canadien "
        "et précède l'US Open : forfaits de précaution et abandons y sont fréquents, et la "
        "fraîcheur pèse autant que la hiérarchie. Chaleur et humidité de l'Ohio, sensibles "
        "sur les sessions de journée."
    ),
}


@dataclass
class MissingNote:
    """Une competition passee dans un prompt sans fiche.

    Un lot de cinq matchs portait trois fiches et **aucune pour l'EFL Cup**,
    qui etait le match le plus atypique du lot : un tour de coupe anglaise est
    le format ou la rotation d'effectif est la regle et non l'exception.

    Le compte vient de `prompt_events` : ce sont des matchs **reellement partis
    a l'analyse** sans que le format de leur competition soit dit. Une
    competition active mais jamais analysee est signalee sans compte — il n'y a
    rien a rattraper, seulement quelque chose a preparer.
    """

    competition_id: int
    label: str
    sport_label: str
    #: Matchs de cette competition deja entres dans un prompt.
    analysed: int = 0
    active: bool = False


def without_notes(settings: Settings | None = None) -> list[MissingNote]:
    """Competitions sans fiche, et ce qu'elles ont deja coute d'analyses muettes.

    Meme logique que les cles non classees : ce qui manque doit se voir dans
    l'interface, pas se decouvrir dans le prompt.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.active, s.label AS sport_label, "
            "  (SELECT COUNT(DISTINCT pe.event_id) FROM prompt_events pe "
            "     JOIN events e ON e.id = pe.event_id "
            "    WHERE e.competition_id = c.id) AS analysed "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "WHERE TRIM(COALESCE(c.notes, '')) = ''"
        ).fetchall()

    found = [
        MissingNote(
            competition_id=row["id"],
            label=row["label"],
            sport_label=row["sport_label"],
            analysed=int(row["analysed"] or 0),
            active=bool(row["active"]),
        )
        for row in rows
        if row["analysed"] or row["active"]
    ]
    # Les plus couteuses d'abord : une competition deja passee douze fois dans un
    # prompt sans fiche a douze analyses muettes derriere elle.
    found.sort(key=lambda item: (-item.analysed, sort_key(item.label)))
    return found


def unclassified(settings: Settings | None = None) -> list[Unclassified]:
    """Competitions a classer : sans niveau, mais qui en attendent un.

    Deux populations, et il faut les deux : celles qui **portent deja des
    selections** — leur historique est muet tant qu'elles ne sont pas classees —
    et celles qui sont **actives**, donc sur le point d'en porter.

    Un sport sans taxonomie n'y figure jamais : le cyclisme n'a pas de niveaux,
    et l'y reclamer chaque jour serait une tache qu'on ne peut pas accomplir.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.active, s.key AS sport_key, s.label AS sport_label, "
            "       COUNT(k.id) AS picks, "
            "       SUM(CASE WHEN k.result IN ('win', 'loss') THEN 1 ELSE 0 END) AS settled "
            "FROM competitions c "
            "JOIN sports s ON s.id = c.sport_id "
            "LEFT JOIN events e ON e.competition_id = c.id "
            "LEFT JOIN picks k ON k.event_id = e.id "
            "WHERE c.category IS NULL OR c.category = '' "
            "GROUP BY c.id"
        ).fetchall()
    found = [
        Unclassified(
            competition_id=row["id"],
            label=row["label"],
            sport_key=row["sport_key"],
            sport_label=row["sport_label"],
            picks=row["picks"] or 0,
            settled=row["settled"] or 0,
            active=bool(row["active"]),
        )
        for row in rows
        if categories_for(row["sport_key"]) and (row["picks"] or row["active"])
    ]
    # Les plus lourdes d'historique d'abord : c'est l'ordre dans lequel les
    # classer rend le plus de lignes lisibles.
    found.sort(key=lambda item: (-item.picks, item.sport_key, sort_key(item.label)))
    return found


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


# -- Competitions absentes du catalogue The Odds API --------------------------


class CompetitionError(ValueError):
    """Saisie refusee, avec un message lisible pour l'utilisateur."""


def create_apifootball(
    label: str, league_id: str, category: str = "", settings: Settings | None = None
) -> int:
    """Cree une competition de football que The Odds API ne connait **pas du tout**.

    A ne pas confondre avec « hors saison » : une competition que le fournisseur
    sert parfois figure au catalogue et arrive par la synchronisation, avec
    `api_active = 0` en attendant ses cotes. Celles dont il est question ici n'y
    figurent a aucun moment. Mesure du 12/08/2026 sur `/sports?all=true` : 175
    cles dont 67 au football, et **aucune Supercoupe d'Europe** — quand
    API-Football la sert sous la ligue 531. La synchronisation ne peut donc pas
    la decouvrir, aujourd'hui ni jamais.

    Sans cette porte, le seul chemin d'entree etait `manual.py`, qui cree une
    competition comme **effet de bord** d'un match saisi a la main : sans
    identifiant de ligue, donc muette — ni classement, ni forme, ni absents — et
    sans le bouton d'import qui aurait ramene les matchs tout seuls.

    Trois choix qui ne vont pas de soi :

    - **`api_active = 0` est ecrit explicitement.** La colonne vaut 1 par
      defaut et n'est jamais mise a jour que par la synchronisation, qui
      s'indexe sur `oddsapi_key` : une competition sans cle garderait 1 pour
      toujours, et `fixtures.import_competition` la refuserait comme « deja
      servie par The Odds API » — l'affirmation exactement inverse de la verite.
    - **Elle est creee active**, contrairement a ce que la synchronisation
      decouvre. La regle « rien ne se met a couter sans decision » protege le
      quota ; sa raison ne s'applique pas ici, `scan.active_competitions`
      filtrant sur `oddsapi_key IS NOT NULL` — cette competition ne coutera
      jamais un credit Odds API. Et la creer **est** la decision : elle se tape
      a la main, une par une.
    - **L'identifiant de ligue est obligatoire**, la ou `set_apifootball_league`
      traite une saisie illisible comme « non rattachee ». Le contraste est
      juste : la-bas l'effet est une ligne de contexte absente, ici c'est une
      competition qui ne pourra jamais recevoir un seul match, c'est-a-dire tout
      ce pour quoi on la cree. Il n'est pour autant **jamais devine** a partir
      du libelle, meme regle que partout ailleurs.

    Football seulement : `fixtures.py` est le seul fournisseur de matchs sans
    cotes, et il refuse deja tout autre sport.
    """
    name = (label or "").strip()
    if not name:
        raise CompetitionError("Le nom de la compétition est obligatoire.")

    raw = (league_id or "").strip()
    if not raw.isdigit() or int(raw) <= 0:
        raise CompetitionError(
            "L'identifiant de ligue API-Football est obligatoire : sans lui, "
            "aucun match ne peut être importé."
        )

    value = (category or "").strip().lower()
    with connect(settings) as conn:
        sport = conn.execute("SELECT id FROM sports WHERE key = 'football'").fetchone()
        if sport is None:
            raise CompetitionError("Sport « football » introuvable.")

        # Meme cle naturelle que `manual._competition_id` — (sport, libelle) —
        # et c'est ce qui evite le doublon le plus couteux : deux competitions
        # au meme nom, l'une scannee et l'autre non, que rien ne distingue a
        # l'ecran. On refuse plutot que d'ecraser une saisie existante ; le
        # rattachement se corrige dans le tableau, champ par champ.
        existing = conn.execute(
            "SELECT id, label FROM competitions WHERE sport_id = ?", (sport["id"],)
        ).fetchall()
        for row in existing:
            if sort_key(row["label"]) == sort_key(name):
                raise CompetitionError(
                    f"« {row['label']} » existe déjà : son rattachement et son niveau "
                    "se corrigent dans le tableau."
                )

        cursor = conn.execute(
            "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
            "                          api_active, apifootball_league_id, category) "
            "VALUES (?, NULL, ?, 0, 1, 0, ?, ?)",
            (sport["id"], name, int(raw), value if value in FOOTBALL_CATEGORIES else None),
        )
        competition_id = int(cursor.lastrowid)

    logger.info(
        "Competition hors catalogue creee : %s (ligue API-Football %s, niveau %s)",
        name,
        raw,
        value if value in FOOTBALL_CATEGORIES else "non renseigne",
    )
    return competition_id


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
            category = COMPETITION_CATEGORIES.get(oddsapi_key)
            note = COMPETITION_NOTES.get(oddsapi_key)
            existing = conn.execute(
                "SELECT id, label, api_active, apifootball_league_id, tennisdata_tournaments, "
                "       category, notes FROM competitions WHERE oddsapi_key = ?",
                (oddsapi_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
                    "                          api_active, apifootball_league_id, "
                    "                          tennisdata_tournaments, category, notes) "
                    "VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?)",
                    (
                        sport_ids[sport_key],
                        oddsapi_key,
                        title,
                        served,
                        league_id,
                        tournaments,
                        category,
                        note,
                    ),
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

            if note is not None and not (existing["notes"] or "").strip():
                # Meme regle que le reste : comble un manque, n'ecrase jamais une
                # fiche ecrite a la main. C'est de la prose, et celle de
                # l'utilisateur vaut toujours mieux que la notre.
                conn.execute(
                    "UPDATE competitions SET notes = ? WHERE id = ?", (note, existing["id"])
                )

            if category is not None and not existing["category"]:
                # Meme regle encore. Elle compte doublement ici : un niveau
                # manquant ne se voit nulle part sur le board, seulement dans un
                # regroupement de statistiques qui s'appauvrit en silence.
                conn.execute(
                    "UPDATE competitions SET category = ? WHERE id = ?",
                    (category, existing["id"]),
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
