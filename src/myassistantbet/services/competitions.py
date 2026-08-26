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
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from ..config import Settings, get_settings
from ..db import connect
from ..providers.oddsapi import OddsAPIClient
from ..providers.tennisapi import TOURS as _TENNISAPI_TOURS
from .labels import sort_key

logger = logging.getLogger(__name__)

#: Prefixes de cles The Odds API par sport interne. Le cyclisme n'est pas couvert.
SPORT_PREFIXES = {"soccer_": "football", "tennis_": "tennis"}

#: Surfaces de tennis. Elles decident quel Elo de surface est rendu dans le
#: bloc CONTEXTE ; laissee vide, seul l'Elo general apparait. Aucune deduction
#: automatique depuis le libelle du tournoi : ce serait une invention.
SURFACES = {"hard": "Dur", "clay": "Terre battue", "grass": "Gazon"}

#: Circuits acceptes par le fournisseur de rencontres. **Lue chez le client**
#: plutot que retapee : deux listes se seraient contredites au premier circuit
#: ajoute, et l'ecran aurait propose un choix que le service refuse.
TENNISAPI_TOURS = _TENNISAPI_TOURS

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
    # Un tableau de qualification n'est pas le tournoi qu'il precede : chez les
    # hommes il se joue au meilleur des trois manches quand le tableau principal
    # d'un Grand Chelem se joue en cinq, et son plateau est celui des 100e-250e
    # mondiaux. Les ranger ensemble produirait un taux qui ne decrit ni l'un ni
    # l'autre — l'argument meme de cette table. Sa place ici, entre le Challenger
    # et l'ITF, suit le plateau et non le prestige du tournoi hote.
    "qualifications": "Qualifications",
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
            "       c.fenetre_debut, c.fenetre_fin, c.tennisapi_tour, "
            "       c.tennisapi_tournament_id, c.phase_de, "
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


def check_fenetre(debut: str, fin: str, tour: str, tournament_id: str) -> tuple[str, str, str, int]:
    """Valide une fenetre du tournoi et son rattachement, ou leve.

    **Ecrite a part parce qu'elle est appelee avant une ecriture et pendant une
    autre.** `create_manual` doit pouvoir refuser une fenetre illisible *avant*
    d'inserer la competition : valider apres l'insertion laisserait une
    competition creee et a moitie reglee, c'est-a-dire exactement l'etat que
    cette fenetre existe pour eviter. Deux validations recopiees auraient
    diverge au premier circuit ajoute.
    """
    debut_value = (debut or "").strip()
    fin_value = (fin or "").strip()
    tour_value = (tour or "").strip().lower()
    id_raw = (tournament_id or "").strip()

    for value in (debut_value, fin_value):
        try:
            date.fromisoformat(value)
        except ValueError as exc:
            raise CompetitionError(
                f"Date illisible : « {value} ». Format attendu AAAA-MM-JJ."
            ) from exc
    if date.fromisoformat(debut_value) > date.fromisoformat(fin_value):
        raise CompetitionError("La fin de la fenêtre précède son début.")
    if tour_value not in TENNISAPI_TOURS:
        raise CompetitionError(
            f"Circuit inconnu : « {tour} ». Attendu : {', '.join(TENNISAPI_TOURS)}."
        )
    if not id_raw.isdigit() or int(id_raw) <= 0:
        raise CompetitionError(
            "L'identifiant de tournoi tennis-api est obligatoire : sans lui, la fenêtre "
            "ne désigne aucun flux."
        )
    return debut_value, fin_value, tour_value, int(id_raw)


def set_fenetre(
    competition_id: int,
    debut: str,
    fin: str,
    tour: str,
    tournament_id: str,
    settings: Settings | None = None,
) -> None:
    """Fenetre du tournoi et rattachement au fournisseur de rencontres.

    **Les quatre champs se posent ensemble parce qu'aucun ne sert seul** : sans
    la fenetre, rien ne distingue une qualification du tableau principal — les
    deux portent le meme identifiant de tournoi chez le fournisseur ; sans le
    rattachement, la fenetre ne designe aucun flux. Un formulaire par champ
    aurait laisse la competition dans un etat a moitie renseigne ou l'import
    echoue sans que l'ecran dise lequel des quatre manque.

    Rien n'est deduit d'un libelle, meme regle que la surface, le fuseau et la
    ligue API-Football : l'identifiant de tournoi se verifie a la main sur
    `/{tour}/tournament/info/{id}`, les dates se lisent sur le calendrier
    officiel.

    **Une saisie vide efface les quatre**, elle n'en garde pas trois : un
    rattachement a moitie retire est un piege pour le prochain import.
    """
    debut_value = (debut or "").strip()
    fin_value = (fin or "").strip()
    tour_value = (tour or "").strip().lower()
    id_raw = (tournament_id or "").strip()

    if not any((debut_value, fin_value, tour_value, id_raw)):
        with connect(settings) as conn:
            conn.execute(
                "UPDATE competitions SET fenetre_debut = NULL, fenetre_fin = NULL, "
                "       tennisapi_tour = NULL, tennisapi_tournament_id = NULL "
                " WHERE id = ?",
                (competition_id,),
            )
        logger.info("Qualification de la competition %d : effacee", competition_id)
        return

    debut_value, fin_value, tour_value, numero = check_fenetre(
        debut_value, fin_value, tour_value, id_raw
    )
    id_raw = str(numero)

    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET fenetre_debut = ?, fenetre_fin = ?, "
            "       tennisapi_tour = ?, tennisapi_tournament_id = ? WHERE id = ?",
            (debut_value, fin_value, tour_value, int(id_raw), competition_id),
        )
    logger.info(
        "Qualification de la competition %d : %s au %s, %s %s",
        competition_id,
        debut_value,
        fin_value,
        tour_value,
        id_raw,
    )


def phase_scope(competition_id: int | None, settings: Settings | None = None) -> tuple[int, ...]:
    """Les competitions a lire pour le parcours d'un joueur dans celle-ci.

    Rend la competition demandee, suivie de celles qui se declarent une **phase**
    d'elle. Un tableau principal ramene donc ses qualifications ; une
    qualification lue pour elle-meme ne ramene qu'elle — le tableau principal ne
    s'est pas encore joue, et un joueur n'y a pas de parcours.

    **Une seule ecriture, et c'est le point.** Deux lecteurs filtrent par
    competition et le second n'est pas `tennis_load.load_for` :
    `serve_stats._scanned_here` lit `events` directement, pour eviter une
    recursion avec `_tournament_id`. Recopier la resolution des deux cotes
    serait la septieme occurrence du motif du dossier — deux copies qu'aucun
    mecanisme n'oblige a concorder.

    **Et tous les lecteurs ne l'appellent pas.** `tennis_round._edition_in_base`
    doit s'en tenir a la competition seule : le compte des joueurs y decide du
    tour, et 128 + 256 n'est la taille d'aucun tableau. Suivre le lien y ferait
    taire la ligne. Le lien est une relation entre competitions, pas une etendue
    a appliquer partout.

    **Profondeur un, garantie a l'ecriture.** `set_phase` refuse une chaine, donc
    une seule requete suffit et rien ne se tronque en silence.
    """
    if not competition_id:
        return ()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id FROM competitions WHERE phase_de = ? ORDER BY id", (competition_id,)
        ).fetchall()
    return (int(competition_id), *(int(row["id"]) for row in rows))


def set_phase(
    competition_id: int, phase_de: str | int | None, settings: Settings | None = None
) -> None:
    """Declare que cette competition est une **phase** d'une autre, ou l'efface.

    **Un formulaire a part, sur la meme ligne que la fenetre.** Les quatre champs
    de `set_fenetre` se posent ensemble parce qu'aucun ne sert seul ; celui-ci
    sert seul — Winston-Salem a une fenetre et aucune phase — et les fondre
    ferait effacer le rattachement en effacant la fenetre.

    Rien n'est deduit d'un libelle, meme regle que la ligue API-Football, la
    surface et le fuseau. Le piege est plus tentant ici qu'ailleurs, le prefixe
    d'une qualification etant exactement celui de son tableau principal.

    Quatre refus, et chacun evite un lien qui se lirait comme un fait :

      * une competition **inconnue** — un identifiant qui ne designe rien ;
      * **elle-meme** — le scope se lirait deux fois et le compte doublerait ;
      * un **autre sport** — une qualification de tennis n'est pas une phase d'un
        championnat de football, et rien d'autre n'attraperait la faute de frappe ;
      * une **chaine**, dans les deux sens : la cible est deja une phase, ou la
        source en porte deja. `phase_scope` lit **un** niveau ; une chaine s'y
        tronquerait sans un mot, et un silence vaut moins qu'un refus.
    """
    brut = str(phase_de or "").strip()
    if not brut:
        with connect(settings) as conn:
            conn.execute("UPDATE competitions SET phase_de = NULL WHERE id = ?", (competition_id,))
        logger.info("Phase de la competition %d : effacee", competition_id)
        return

    try:
        cible = int(brut)
    except ValueError as exc:
        raise CompetitionError(f"Identifiant de competition illisible : {brut!r}") from exc

    if cible == competition_id:
        raise CompetitionError("Une competition ne peut pas etre une phase d'elle-meme.")

    with connect(settings) as conn:
        source = conn.execute(
            "SELECT sport_id, label FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
        parent = conn.execute(
            "SELECT sport_id, label, phase_de FROM competitions WHERE id = ?", (cible,)
        ).fetchone()
        if source is None:
            raise CompetitionError(f"Competition inconnue : {competition_id}")
        if parent is None:
            raise CompetitionError(f"Competition inconnue : {cible}")
        if int(source["sport_id"]) != int(parent["sport_id"]):
            raise CompetitionError(
                f"« {parent['label']} » n'est pas du meme sport que « {source['label']} »."
            )
        if parent["phase_de"] is not None:
            raise CompetitionError(
                f"« {parent['label']} » est deja une phase d'une autre competition : "
                "une phase de phase ne se lit pas."
            )
        enfants = conn.execute(
            "SELECT COUNT(*) AS n FROM competitions WHERE phase_de = ?", (competition_id,)
        ).fetchone()
        if int(enfants["n"]):
            raise CompetitionError(
                f"« {source['label']} » porte deja des phases : elle ne peut pas en etre une."
            )
        conn.execute("UPDATE competitions SET phase_de = ? WHERE id = ?", (cible, competition_id))
    logger.info(
        "Phase de la competition %d : rattachee a %d (%s)", competition_id, cible, parent["label"]
    )


def phase_options(settings: Settings | None = None) -> dict[int, list[dict[str, Any]]]:
    """Pour chaque competition, celles dont elle peut se declarer une phase.

    **Le menu reprend les gardes de `set_phase`**, et un menu qui propose ce que
    le service refuse est pire qu'absent — meme regle que le bouton d'import de
    `/competitions`. Les deux ecritures ne peuvent pas etre fondues : l'une leve
    avec un message, l'autre filtre une liste. C'est donc la seconde branche de
    la regle du dossier — **un test lit les deux sources** et verifie que tout ce
    qui est propose est accepte, et que rien d'accepte ne manque au menu.

    Une seule lecture pour tout l'ecran : la version par competition faisait une
    requete par ligne sur une page qui en porte plus de cent.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, sport_id, label, phase_de FROM competitions ORDER BY label"
        ).fetchall()
    toutes = [
        {
            "id": int(row["id"]),
            "sport_id": int(row["sport_id"]),
            "label": str(row["label"]),
            "phase_de": row["phase_de"],
        }
        for row in rows
    ]
    parents = {int(row["phase_de"]) for row in toutes if row["phase_de"] is not None}
    options: dict[int, list[dict[str, Any]]] = {}
    for competition in toutes:
        if competition["id"] in parents:
            options[competition["id"]] = []
            continue
        options[competition["id"]] = [
            {"id": autre["id"], "label": autre["label"]}
            for autre in toutes
            if autre["id"] != competition["id"]
            and autre["sport_id"] == competition["sport_id"]
            and autre["phase_de"] is None
        ]
    return options


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
    # Verifie le 12/08/2026 : `/leagues?search=Leagues Cup` rend **une** ligne,
    # « Leagues Cup », type Cup, pays « World », saison 2026 en cours. Le
    # fournisseur y annonce `standings: false` et `injuries: false` — le bloc
    # portera donc la forme, les confrontations et le lieu, et dira lui-meme ce
    # qu'il n'a pas.
    "soccer_concacaf_leagues_cup": 772,
    # Verifie le 14/08/2026 : `/leagues?country=Saudi-Arabia` rend sept lignes,
    # dont « Pro League » (307), type League, saison courante 2026 du 13/08/2026
    # au 28/05/2027. Couverture annoncee : `standings`, `lineups` et
    # `statistics_fixtures` vrais, `injuries` et `top_scorers` faux.
    #
    # Son absence d'ici est ce qui a rendu **trois matchs du 14/08 muets a 0/26**
    # sans qu'aucune ligne ne le signale : sans identifiant de ligue,
    # `context_possible` est faux et aucun appel n'est jamais emis. La reprise du
    # championnat, le 13/08, etait sa premiere apparition depuis la treve.
    "soccer_saudi_arabia_pro_league": 307,
    # Verifie le 14/08/2026 : `/leagues?country=Germany&type=cup` rend « DFB
    # Pokal » (81), saison 2026. Le fournisseur y annonce **tout a faux** —
    # `standings`, `injuries`, `lineups`, `statistics_fixtures` — et les
    # 32 fixtures du tour relevees ce jour-la portent toutes un arbitre nul.
    # C'est ce qui a decide `DOMESTIC_AGGREGATES` : rattachee seule, la
    # competition ne ramenerait que l'affiche, le lieu et les confrontations.
    "soccer_germany_dfb_pokal": 81,
    # Verifie le 14/08/2026 : `/leagues?country=England&type=cup` rend « League
    # Cup » (48) — le fournisseur ne la nomme pas « EFL Cup » —, saison 2026,
    # `lineups` et `statistics_fixtures` vrais, `standings` et `injuries` faux.
    # Elle portait deja 34 evenements en base, dont un parti a l'analyse et une
    # selection prise dessus, tous sans contexte et sans que rien ne le dise.
    "soccer_england_efl_cup": 48,
}


#: Competitions dont les **agregats de saison** se lisent dans le championnat
#: domestique de chaque equipe, et non dans la competition du match.
#:
#: `/teams/statistics` et `/standings` sont scopes a une competition. Sur une
#: coupe, cela ne decrit rien : les participants y ont joue un ou deux matchs,
#: donc sous `SEASON_MIN_MATCHES`, et une coupe n'a pas de classement. Le bloc
#: perdait ses dix lignes les plus decisives exactement la ou l'ecart de niveau
#: **est** le fait de la rencontre.
#:
#: C'est le meme angle mort que celui repare par l'historique de saison du
#: dossier d'equipe — Motherwell compte 2 matchs de Conference League quand sa
#: saison domestique en porte 47 — pousse jusqu'aux agregats du bloc.
#:
#: **Declaree par competition, jamais deduite d'un drapeau.** Piloter la source
#: des agregats sur `coverage.standings` reviendrait a laisser la couverture du
#: fournisseur decider de la methode, et embarquerait au passage la Conference
#: League, dont les blocs fonctionnent. L'extension reste une decision d'une
#: ligne, auditable, qui ne surprend jamais un bloc qui marche.
DOMESTIC_AGGREGATES: frozenset[str] = frozenset(
    {
        "soccer_germany_dfb_pokal",
        "soccer_england_efl_cup",
    }
)


def reads_domestic_aggregates(oddsapi_key: str | None) -> bool:
    """Les agregats de saison de cette competition viennent-ils d'ailleurs ?"""
    return bool(oddsapi_key) and oddsapi_key in DOMESTIC_AGGREGATES


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


#: Le nom d'un tournoi chez le fournisseur de **profils** (`matches-played`),
#: qui n'est pas celui du jeu de donnees de resultats.
#:
#: **Double de la migration 069, exactement comme `TENNISDATA_TOURNAMENTS`**, et
#: pour la meme raison : une migration ne classe que ce qui est deja en base
#: quand elle tourne, et la synchronisation decouvre en permanence. Un test relit
#: le fichier de migration et le compare a cette table plutot que d'en recopier
#: la regle.
#:
#: **Plusieurs noms par tournoi, et c'est la regle et non l'exception.** Mesure
#: du 20/08/2026 sur les 798 reponses archivees : le fournisseur renomme au
#: sponsor sans retro-corriger. Cincinnati porte trois graphies — dont une
#: « - New York », l'edition 2020 deplacee — le Canadian Open quatre sur deux
#: villes et deux langues, Queen's cinq. La ville est **dans** le nom, elle bouge
#: par calendrier : ce n'est pas une cle de rapprochement.
MATCHESPLAYED_TOURNAMENTS: dict[str, str] = {
    "tennis_atp_aus_open_singles": "Australian Open - Melbourne",
    "tennis_atp_barcelona_open": "Barcelona Open Banc Sabadell - Barcelona",
    "tennis_atp_canadian_open": (
        "National Bank Open - Toronto|National Bank Open - Montreal|Rogers Cup - Toronto|"
        "Rogers Cup - Montreal|Coupe Rogers - Montreal"
    ),
    "tennis_atp_china_open": "China Open - Beijing",
    "tennis_atp_cincinnati_open": (
        "Cincinnati Open - Cincinnati|Western & Southern Open - Cincinnati|"
        "Western & Southern Open - New York"
    ),
    "tennis_atp_dubai": "Dubai Duty Free Tennis Championships - Dubai",
    "tennis_atp_french_open": "French Open - Paris",
    "tennis_atp_halle_open": "Terra Wortmann Open - Halle",
    "tennis_atp_hamburg_open": "Hamburg Open - Hamburg|Hamburg European Open - Hamburg",
    "tennis_atp_indian_wells": "BNP Paribas Open - Indian Wells",
    "tennis_atp_italian_open": "Internazionali BNL d'Italia - Rome",
    "tennis_atp_madrid_open": "Mutua Madrid Open - Madrid",
    "tennis_atp_miami_open": "Miami Open - Miami",
    "tennis_atp_monte_carlo_masters": "Monte-Carlo Rolex Masters - Monte-Carlo",
    "tennis_atp_munich": "BMW Open - Munich",
    "tennis_atp_paris_masters": "Rolex Paris Masters - Paris",
    "tennis_atp_qatar_open": "Qatar ExxonMobil Open - Doha",
    "tennis_atp_queens_club_champ": (
        "HSBC Championships - London|cinch Championships - London|Fever-Tree Championships - London"
    ),
    "tennis_atp_shanghai_masters": "Shanghai Rolex Masters - Shanghai",
    "tennis_atp_us_open": "U.S. Open - New York",
    "tennis_atp_washington_open": (
        "Citi Open - Washington|Mubadala Citi DC Open - Washington|Mubadala DC Open - Washington"
    ),
    "tennis_atp_wimbledon": "Wimbledon - London",
    "tennis_wta_aus_open_singles": "Australian Open - Melbourne",
    "tennis_wta_bad_homburg_open": "Bad Homburg Open - Bad Homburg",
    "tennis_wta_canadian_open": (
        "National Bank Open - Toronto|Omnium Banque Nationale - Montreal|"
        "Rogers Cup - Toronto|Rogers Cup - Montreal"
    ),
    "tennis_wta_charleston_open": (
        "Credit One Charleston Open - Charleston|Volvo Car Open - Charleston|"
        "Family Circle Cup - Charleston"
    ),
    "tennis_wta_china_open": "China Open - Beijing",
    "tennis_wta_cincinnati_open": (
        "Cincinnati Open - Cincinnati|Western & Southern Open - Cincinnati|"
        "Western & Southern Open - New York"
    ),
    "tennis_wta_dubai": "Dubai Duty Free Championships - Dubai",
    "tennis_wta_french_open": "French Open - Paris",
    "tennis_wta_german_open": (
        "Berlin Tennis Open - Berlin|Berlin Ladies Open - Berlin|bett1open - Berlin|"
        "Betti Open - Berlin"
    ),
    "tennis_wta_indian_wells": "BNP Paribas Open - Indian Wells",
    "tennis_wta_italian_open": "Internazionali BNL d'Italia - Rome",
    "tennis_wta_madrid_open": "Mutua Madrid Open - Madrid",
    "tennis_wta_miami_open": "Miami Open - Miami",
    "tennis_wta_qatar_open": "Qatar TotalEnergies Open - Doha|Qatar Total Open - Doha",
    "tennis_wta_queens_club_champ": (
        "The HSBC Championships - London|LTA London Championships - London"
    ),
    "tennis_wta_strasbourg": "Internationaux de Strasbourg - Strasbourg",
    "tennis_wta_stuttgart_open": "Porsche Tennis Grand Prix - Stuttgart",
    "tennis_wta_us_open": "U.S. Open - New York",
    "tennis_wta_washington_open": (
        "Mubadala Citi DC Open - Washington|Mubadala DC Open - Washington|Citi Open - Washington"
    ),
    "tennis_wta_wimbledon": "Wimbledon - London",
    "tennis_wta_wuhan_open": "Wuhan Open - Wuhan|Wuhan Tennis Open - Wuhan",
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


def manual_sports(settings: Settings | None = None) -> list[dict[str, str]]:
    """Sports ou une competition peut se creer sans aucun fournisseur.

    Le football en est exclu : il a sa porte, et elle reclame l'identifiant de
    ligue qui rend la competition bavarde. La liste se lit dans la table plutot
    que d'etre ecrite ici — un sport ajoute demain apparaitrait sans qu'on y
    pense, et une seconde liste aurait diverge de la premiere.
    """
    with connect(settings) as conn:
        return [
            {"key": row["key"], "label": row["label"]}
            for row in conn.execute("SELECT key, label FROM sports ORDER BY id").fetchall()
            if row["key"] != "football"
        ]


def create_manual(
    label: str,
    sport_key: str,
    category: str = "",
    settings: Settings | None = None,
    *,
    fenetre: tuple[str, str, str, str] | None = None,
) -> int:
    """Cree une competition qu'**aucun fournisseur** ne sert, ni en cotes ni en matchs.

    Soeur de `create_apifootball`, et la difference tient a ce qui manque.
    La-bas The Odds API ne sert pas les cotes mais API-Football sert les matchs,
    donc l'identifiant de ligue est obligatoire : sans lui la competition ne
    recevrait jamais un match, c'est-a-dire tout ce pour quoi on la cree. Ici il
    n'existe **aucun** chemin automatique — `fixtures.py` est football seulement,
    et le tennis n'a pas d'equivalent — donc exiger un identifiant reviendrait a
    reclamer une preuve qui n'existe pas. Les matchs se saisissent un par un
    depuis `/manual`, et c'est la seule facon.

    **Mesure du 24/08/2026, faite avant d'ecrire une ligne**, le jour ou les
    qualifications de l'US Open commencent : `/sports?all=true` rend 176 cles,
    dont **44 au tennis**, et **aucune ne porte une qualification** — ni a l'US
    Open ni aux trois autres Grands Chelems. Les tableaux de qualification sont
    donc absents du catalogue au sens de `create_apifootball` : ils n'y figurent
    **a aucun moment**, et la synchronisation ne les decouvrira jamais. Verifie
    dans le meme geste et pour zero credit, `/events` rendant **zero evenement**
    sur `tennis_atp_us_open` comme sur `tennis_wta_us_open` — le fournisseur ne
    sert rien de l'US Open ce jour-la, pas meme le tableau principal.

    Le football est **refuse ici** : il a sa porte, et elle reclame la ligue. Le
    laisser passer rouvrirait exactement le trou que `create_apifootball` a
    bouche — une competition muette, ni classement, ni forme, ni absents.

    Deux choix repris tels quels de la porte football, pour les memes raisons :

    - **`api_active = 0` est ecrit explicitement.** La colonne vaut 1 par defaut
      et n'est mise a jour que par la synchronisation, qui s'indexe sur
      `oddsapi_key` : une competition sans cle garderait 1 pour toujours, et la
      colonne « Servie ? » annoncerait l'inverse de la verite.
    - **Elle est creee active.** La regle « rien ne se met a couter sans
      decision » protege le quota ; sa raison ne s'applique pas ici,
      `scan.active_competitions` filtrant sur `oddsapi_key IS NOT NULL` — cette
      competition ne coutera jamais un credit Odds API. Et la creer **est** la
      decision : elle se tape a la main, une par une.

    La surface, la ville et le fuseau ne sont pas demandes ici : ils se
    corrigent dans le tableau, champ par champ, et rien ne se deduit du libelle.

    `fenetre` — `(debut, fin, circuit, identifiant de tournoi)` — est en
    revanche proposee **a la creation**, et c'est le seul des reglages a
    l'etre. La raison est qu'elle decide de ce que la competition *est* : sans
    fenetre, un tableau de qualification ne se distingue pas du tournoi
    principal, les deux portant le meme identifiant chez le fournisseur. La
    laisser au tableau ferait exister la competition dans un etat ou son import
    ne peut pas tourner, ce qui est precisement l'etat qu'on vient de quitter.
    Vide, elle ne pose rien — une competition sans qualification est le cas
    ordinaire.
    """
    name = (label or "").strip()
    if not name:
        raise CompetitionError("Le nom de la compétition est obligatoire.")

    key = (sport_key or "").strip().lower()
    if key == "football":
        raise CompetitionError(
            "Une compétition de football se crée avec son identifiant de ligue "
            "API-Football : sans lui elle serait muette — ni classement, ni forme, "
            "ni absents."
        )

    value = (category or "").strip().lower()

    # Valide **avant** d'inserer : une fenetre illisible doit refuser la
    # creation entiere, pas laisser une competition a moitie reglee.
    fenetre_saisie = fenetre if fenetre and any(c.strip() for c in fenetre) else None
    if fenetre_saisie is not None:
        check_fenetre(*fenetre_saisie)

    with connect(settings) as conn:
        sport = conn.execute("SELECT id FROM sports WHERE key = ?", (key,)).fetchone()
        if sport is None:
            raise CompetitionError(f"Sport inconnu : {sport_key}")

        # Meme cle naturelle que `manual._competition_id` et que la porte
        # football — (sport, libelle), casse et accents ignores. C'est ce qui
        # evite le doublon le plus couteux : deux competitions au meme nom que
        # rien ne distingue a l'ecran, se partageant les matchs.
        for row in conn.execute(
            "SELECT id, label FROM competitions WHERE sport_id = ?", (sport["id"],)
        ).fetchall():
            if sort_key(row["label"]) == sort_key(name):
                raise CompetitionError(
                    f"« {row['label']} » existe déjà : son niveau, sa surface et son "
                    "fuseau se corrigent dans le tableau."
                )

        niveaux = CATEGORIES_BY_SPORT.get(key, {})
        cursor = conn.execute(
            "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
            "                          api_active, category) "
            "VALUES (?, NULL, ?, 0, 1, 0, ?)",
            (sport["id"], name, value if value in niveaux else None),
        )
        competition_id = int(cursor.lastrowid)

    if fenetre_saisie is not None:
        # Deja validee ci-dessus : cet appel ne peut plus lever.
        set_fenetre(competition_id, *fenetre_saisie, settings=settings)

    logger.info(
        "Competition sans fournisseur creee : %s (%s, niveau %s)",
        name,
        key,
        value if value in CATEGORIES_BY_SPORT.get(key, {}) else "non renseigne",
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
            profils = MATCHESPLAYED_TOURNAMENTS.get(oddsapi_key)
            category = COMPETITION_CATEGORIES.get(oddsapi_key)
            note = COMPETITION_NOTES.get(oddsapi_key)
            existing = conn.execute(
                "SELECT id, label, api_active, apifootball_league_id, tennisdata_tournaments, "
                "       matchesplayed_tournaments, category, notes "
                "  FROM competitions WHERE oddsapi_key = ?",
                (oddsapi_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
                    "                          api_active, apifootball_league_id, "
                    "                          tennisdata_tournaments, matchesplayed_tournaments, "
                    "                          category, notes) "
                    "VALUES (?, ?, ?, 0, 0, ?, ?, ?, ?, ?, ?)",
                    (
                        sport_ids[sport_key],
                        oddsapi_key,
                        title,
                        served,
                        league_id,
                        tournaments,
                        profils,
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

            if profils is not None and existing["matchesplayed_tournaments"] is None:
                # Le second rattachement, meme regle : les deux fournisseurs ne
                # nomment pas les tournois pareil, et aucun des deux noms ne se
                # deduit de l'autre.
                conn.execute(
                    "UPDATE competitions SET matchesplayed_tournaments = ? WHERE id = ?",
                    (profils, existing["id"]),
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


#: Fenetre glissante sur laquelle une competition est jugee servie, en jours.
#:
#: **Une borne basse mesuree, une borne haute que la base ne peut pas donner.**
#: Releve du 26/08/2026 : la Leagues Cup porte deux rencontres a venir et son
#: dernier prix date de **13,5 jours** — elle se joue par phases, et huit de ses
#: quarante evenements sont cotes. Une fenetre de sept jours l'aurait donc
#: masquee a tort. La borne mesuree est « plus de quatorze jours » ; au-dela, la
#: base entiere ne couvre que vingt-deux jours et **ne permet pas de departager
#: trois semaines de quatre**.
#:
#: Le choix se fait du cote sur. Masquer une competition servie coute une soiree
#: d'analyse ; en laisser paraitre une qui ne l'est plus coute quelques lignes de
#: board, et le badge « aucun prix » le dit deja.
PRICE_WINDOW_DAYS = 21


@dataclass
class UnpricedCompetition:
    """Une competition dont aucun prix ne remonte, et qui porte des matchs a venir."""

    competition_id: int
    label: str
    sport: str
    upcoming: int
    #: Dernier releve de prix connu, tous chemins confondus. `None` quand la
    #: competition n'en a **jamais** porte — le cas des trois tournois du 24/08.
    last_price_at: str | None = None

    @property
    def never_priced(self) -> bool:
        return self.last_price_at is None


def unpriced(
    settings: Settings | None = None, now: datetime | None = None
) -> list[UnpricedCompetition]:
    """Les competitions qu'aucun book ne cote, et qui ont des matchs a venir.

    **La regle porte sur la competition, jamais sur l'evenement**, et c'est la
    mesure qui l'impose. Au 26/08/2026, 295 evenements n'avaient jamais porte de
    prix : 154 dans trois tournois que The Odds API ne sert pas du tout, et 141
    **dans des competitions servies** — EFL Cup 43 sur 57, Leagues Cup 32 sur 40.
    Le book y cote certains matchs et pas d'autres.

    Le contre-exemple qui interdit la regle par evenement est dans la base : douze
    rencontres a venir sans aucun prix, dont **Lyon - Fenerbahce a dix heures du
    coup d'envoi**. A cette echelle, rien ne distingue « ne sera pas cote » de
    « pas encore cote » — et en cas de doute, rien.

    **Reversible dans les deux sens, et sur l'etat reel.** Un prix suffit a faire
    revenir une competition — Monterrey, le 24/08, etait au catalogue et
    simplement inactive ; et un releve plus vieux que `PRICE_WINDOW_DAYS` la fait
    partir, sans quoi un tournoi annuel reapparaitrait servi douze mois apres.
    Rien n'est fige dans une liste.

    **Une competition sans match a venir n'y figure pas** : il n'y a rien a
    cacher, elle ne parait deja plus au board. C'etait le cas des trois tournois
    vises le jour meme de la mesure — leurs tableaux etaient termines.
    """
    settings = settings or get_settings()
    moment = (now or datetime.now(UTC)).astimezone(UTC)
    horizon = (moment - timedelta(days=PRICE_WINDOW_DAYS)).strftime("%Y-%m-%dT%H:%M:%SZ")
    borne = moment.strftime("%Y-%m-%dT%H:%M:%SZ")
    with connect(settings) as conn:
        rows = conn.execute(
            # `odds` **et** `prompt_odds` : « aucune cote obtenable par aucun des
            # deux chemins ». Un releve de substitution ou une saisie manuelle
            # valent autant qu'un prix du fournisseur — sans quoi la regle
            # ecarterait les competitions creees a la main, qui portent quatre
            # selections tranchees reelles.
            "SELECT c.id, c.label, s.key AS sport,"
            "       SUM(e.commence_time >= :borne) AS upcoming,"
            # **`MAX` a deux arguments est scalaire en SQLite, pas agregat** : ecrit
            # ainsi il rendait le dernier prix d'une ligne arbitraire du groupe au
            # lieu du dernier de la competition, et une competition servie
            # ressortait non servie. Le maximum par evenement se calcule donc dans
            # une sous-requete, et `MAX` a un seul argument agrege le groupe.
            "       MAX(("
            "         SELECT MAX(f) FROM ("
            "           SELECT o.fetched_at AS f FROM odds o WHERE o.event_id = e.id"
            "           UNION ALL"
            "           SELECT q.fetched_at FROM prompt_odds q WHERE q.event_id = e.id"
            "         )"
            "       )) AS dernier_prix "
            "FROM competitions c "
            "JOIN sports s ON s.id = c.sport_id "
            "JOIN events e ON e.competition_id = c.id "
            # **L'absence de prix ne suffit pas : il faut que le fournisseur le
            # dise.** Une competition servie dont l'appel de cotes a echoue n'a
            # pas de prix non plus, et la masquer viderait le board sur une panne
            # — defaut que quatre tests de board ont fait apparaitre avant la
            # livraison. `api_active` est la declaration du fournisseur, ecrite
            # par `sync_from_api` ; une cle absente dit qu'il ne la connait pas du
            # tout. Meme regle que partout : on cherche ce que la source dit,
            # plutot que de le deduire d'un silence.
            #
            # Verifie sur la base servie au 25/08/2026 : les trois tournois vises
            # portent `oddsapi_key` nul **et** `api_active = 0` ; les six autres
            # competitions a matchs a venir portent les deux a 1.
            "WHERE c.oddsapi_key IS NULL OR c.api_active = 0 "
            "GROUP BY c.id "
            "HAVING upcoming > 0 AND (dernier_prix IS NULL OR dernier_prix < :horizon) "
            "ORDER BY s.key, c.label",
            {"borne": borne, "horizon": horizon},
        ).fetchall()
    return [
        UnpricedCompetition(
            competition_id=int(row["id"]),
            label=str(row["label"]),
            sport=str(row["sport"]),
            upcoming=int(row["upcoming"]),
            last_price_at=str(row["dernier_prix"]) if row["dernier_prix"] else None,
        )
        for row in rows
    ]


#: Libelles du journal des mesures. **Les deux sens sont dates**, sans quoi la
#: regle ne serait reversible que dans un.
UNPRICED_ENTERED = "compétition retirée du board — aucun prix"
UNPRICED_LEFT = "compétition revenue au board — prix servis"

#: Libelle de l'entree qui date la **mise en service** de la regle, au premier
#: scan qui l'evalue.
#:
#: **Sans elle, deux causes rendent la meme observation.** Un journal sans
#: transition dit « aucune competition n'a bascule » et « la regle n'a jamais
#: tourne » du meme silence — et les deux n'appellent pas le meme geste : la
#: premiere se lit, la seconde se repare. C'est le defaut caracteristique du
#: projet, pose cette fois sur le dispositif qui date les autres.
#:
#: C'est aussi elle qui rend vraie la formule du point de rupture : **date au
#: premier scan qui l'evalue**, et non a la premiere bascule. Le sujet n'est pas
#: la premiere competition retiree mais l'instant a partir duquel la composition
#: des lots est soumise a la regle — meme s'il ne retire rien ce jour-la.
#:
#: **Une fois et une seule**, et la garde se lit sur le journal lui-meme : idiome
#: de `changelog.note_feedback`. Un compteur en memoire ne survivrait pas au
#: redemarrage, un drapeau de plus en base serait une seconde ecriture de ce que
#: le journal dit deja.
UNPRICED_ARMED = "filtre des compétitions sans prix — en service"


def note_price_coverage(
    settings: Settings | None = None, now: datetime | None = None
) -> list[tuple[str, str]]:
    """Date la mise en service, puis les **transitions** de l'etat « sans prix ».

    Rend les entrees de journal ecrites, `(libelle, detail)`.

    **Les transitions, jamais un instantane periodique.** Un releve a chaque scan
    grossirait sans porter d'information et noierait la bascule au milieu du
    bruit : ce qui informe est le moment ou l'etat change. Monterrey, le
    24/08/2026, etait au catalogue et simplement inactive — c'est cette bascule-la
    qu'un journal doit rendre lisible, pas les vingt jours ou rien n'a bouge.

    **Les deux sens.** Une competition qui cesse d'etre servie entre dans l'etat,
    une competition qui recoit un prix en sort, et les deux s'ecrivent. Sans la
    seconde, la regle ne serait reversible que dans un sens et le journal
    laisserait croire qu'une exclusion est definitive.

    **Et une entree de mise en service, une fois et une seule.** Sans elle, un
    journal sans transition confond « aucune competition n'a bascule » avec « la
    regle n'a jamais tourne » : deux causes, une observation. Elle est ecrite
    avant les transitions du meme passage, pour que la chronologie se relise.

    Appelee au scan, seul moment ou l'etat peut avoir change sans qu'on regarde.
    """
    settings = settings or get_settings()
    moment = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    jour = moment[:10]
    courantes = {entree.competition_id: entree for entree in unpriced(settings, now)}

    from .changelog import INGESTION
    from .changelog import add as note

    entrees: list[tuple[str, str]] = []
    with connect(settings) as conn:
        marquees = {
            int(row["id"]): str(row["label"])
            for row in conn.execute(
                "SELECT id, label FROM competitions WHERE unpriced_since IS NOT NULL"
            )
        }
        armee = (
            conn.execute(
                "SELECT 1 FROM changelog_mesure WHERE label = ? LIMIT 1", (UNPRICED_ARMED,)
            ).fetchone()
            is not None
        )
        entrantes = [cid for cid in courantes if cid not in marquees]
        sortantes = [cid for cid in marquees if cid not in courantes]
        for cid in entrantes:
            conn.execute("UPDATE competitions SET unpriced_since = ? WHERE id = ?", (moment, cid))
        for cid in sortantes:
            conn.execute("UPDATE competitions SET unpriced_since = NULL WHERE id = ?", (cid,))

    if not armee:
        etat = (
            f"{len(courantes)} compétition(s) déjà dans l'état"
            if courantes
            else "aucune compétition dans l'état"
        )
        note(
            jour,
            UNPRICED_ARMED,
            f"Première évaluation de la règle au scan — {etat}, fenêtre de "
            f"{PRICE_WINDOW_DAYS} jours. À partir d'ici, un journal sans transition "
            "dit qu'aucune compétition n'a basculé ; avant cette date, il ne disait "
            "que l'absence d'évaluation.",
            scope=INGESTION,
            settings=settings,
        )
        entrees.append((UNPRICED_ARMED, etat))

    for cid in entrantes:
        entree = courantes[cid]
        note(
            jour,
            UNPRICED_ENTERED,
            f"{entree.label} — {entree.upcoming} match(s) à venir, "
            + (
                "aucun prix connu"
                if entree.never_priced
                else f"dernier prix {entree.last_price_at}"
            )
            + f", fenêtre de {PRICE_WINDOW_DAYS} jours",
            scope=INGESTION,
            settings=settings,
        )
        entrees.append((UNPRICED_ENTERED, entree.label))
    for cid in sortantes:
        note(jour, UNPRICED_LEFT, marquees[cid], scope=INGESTION, settings=settings)
        entrees.append((UNPRICED_LEFT, marquees[cid]))
    return entrees


@dataclass
class HiddenEvent:
    """Une rencontre retiree du board faute de prix.

    **Elle ne porte pas de drapeau « deja cotee », et c'est structurel** : la
    regle opere au niveau de la competition, donc une seule cote — manuelle,
    de substitution, peu importe — la ramene au board avec toutes ses rencontres.
    Aucun evenement listé ici ne peut donc etre cote.

    Le drapeau avait ete ecrit puis retire : un test l'a montre inatteignable, et
    un garde qui ne peut pas mordre donne l'apparence d'un garde. Meme sort que
    le plancher de confiance, pour la meme raison.
    """

    event_id: int
    home: str
    away: str
    commence_time: str

    @property
    def affiche(self) -> str:
        return f"{self.home} – {self.away}" if self.away else self.home


def hidden_events(
    settings: Settings | None = None,
    now: datetime | None = None,
    entries: Sequence[UnpricedCompetition] | None = None,
) -> dict[int, list[HiddenEvent]]:
    """Les rencontres a venir des competitions retirees du board, par competition.

    **L'entree propre par la competition, et pas une porte dans le filtre.** Une
    exception laissee visible au board finit par y rester, et le filtre cesse
    d'en etre un ; l'entree par la competition est le bon niveau puisque la regle
    opere la. Les rencontres restent atteignables pour la saisie manuelle de
    cotes sans encombrer le board.

    C'est aussi ce qui rend « les fixtures entrent » vrai en pratique et pas
    seulement en base : sans ce chemin, un tournoi importe deviendrait invisible
    et incotable, ce qui reviendrait a couper a l'ingestion par un detour.
    """
    settings = settings or get_settings()
    moment = (now or datetime.now(UTC)).strftime("%Y-%m-%dT%H:%M:%SZ")
    # **`entries` est la liste deja calculee par l'appelant.** L'ecran des
    # competitions la demandait pour son bandeau puis la faisait recalculer ici :
    # deux fois la meme requete dans le meme rendu. Absente, elle se calcule — un
    # parametre optionnel plutot qu'une seconde source, et la fonction reste
    # appelable seule.
    if entries is None:
        entries = unpriced(settings, now)
    cibles = [entree.competition_id for entree in entries]
    if not cibles:
        return {}
    marques = ", ".join("?" * len(cibles))
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.competition_id, e.home, e.away, e.commence_time "
            f"FROM events e WHERE e.competition_id IN ({marques}) AND e.commence_time >= ? "
            "ORDER BY e.commence_time, e.id",
            (*cibles, moment),
        ).fetchall()
    groupes: dict[int, list[HiddenEvent]] = {}
    for row in rows:
        groupes.setdefault(int(row["competition_id"]), []).append(
            HiddenEvent(
                event_id=int(row["id"]),
                home=str(row["home"]),
                away=str(row["away"] or ""),
                commence_time=str(row["commence_time"]),
            )
        )
    return groupes
