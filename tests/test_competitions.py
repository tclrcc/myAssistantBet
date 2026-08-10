from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services import competitions as competitions_module
from myassistantbet.services.competitions import (
    APIFOOTBALL_LEAGUES,
    CATEGORIES,
    CATEGORIES_BY_SPORT,
    COMPETITION_CATEGORIES,
    categories_for,
    list_all,
    set_active,
    set_apifootball_league,
    set_category,
    sync_from_api,
    unclassified,
)
from myassistantbet.services.labels import has_sport_icon
from myassistantbet.services.scan import active_competitions

from .helpers import QUOTA_HEADERS

SPORTS_PAYLOAD = [
    {"key": "soccer_epl", "group": "Soccer", "title": "EPL", "active": True},
    {"key": "soccer_france_ligue_one", "group": "Soccer", "title": "Ligue 1 - France"},
    {"key": "tennis_atp_us_open", "group": "Tennis", "title": "ATP US Open"},
    {"key": "tennis_wta_us_open", "group": "Tennis", "title": "WTA US Open"},
    {"key": "americanfootball_nfl", "group": "American Football", "title": "NFL"},
    {"key": "basketball_nba", "group": "Basketball", "title": "NBA"},
]


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


# -- Seed -------------------------------------------------------------------


def test_les_competitions_tennis_sont_seedees_inactives(migrated: Settings) -> None:
    rows = db.query(
        "SELECT c.label, c.active FROM competitions c JOIN sports s ON s.id = c.sport_id "
        "WHERE s.key = 'tennis'",
        settings=migrated,
    )

    assert len(rows) == 8
    assert all(row["active"] == 0 for row in rows), "aucun credit sans decision explicite"
    assert all(item["sport_key"] == "football" for item in active_competitions(migrated)), (
        "le scan ne voit que le football tant que rien n'est active"
    )


def test_activer_une_competition_la_rend_scannable(migrated: Settings) -> None:
    tennis = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'", settings=migrated
    )

    set_active(int(tennis["id"]), True, migrated)

    keys = {item["oddsapi_key"] for item in active_competitions(migrated)}
    assert "tennis_atp_us_open" in keys


def test_desactiver_une_competition(migrated: Settings) -> None:
    ligue1 = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'soccer_france_ligue_one'",
        settings=migrated,
    )

    set_active(int(ligue1["id"]), False, migrated)

    keys = {item["oddsapi_key"] for item in active_competitions(migrated)}
    assert "soccer_france_ligue_one" not in keys


# -- Synchronisation --------------------------------------------------------


@respx.mock
async def test_synchronisation_cree_les_competitions_manquantes(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(200, json=SPORTS_PAYLOAD, headers=QUOTA_HEADERS)
    )

    report = await sync_from_api(odds_client, migrated)

    keys = {row["oddsapi_key"] for row in list_all(migrated)}
    assert "tennis_wta_us_open" in keys
    assert report.ignored == 2, "NFL et NBA sont hors perimetre"
    assert "americanfootball_nfl" not in keys


@respx.mock
async def test_une_competition_decouverte_est_inactive(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "tennis_atp_shanghai_masters", "title": "ATP Shanghai"}],
            headers=QUOTA_HEADERS,
        )
    )

    await sync_from_api(odds_client, migrated)

    row = db.query_one(
        "SELECT active FROM competitions WHERE oddsapi_key = 'tennis_atp_shanghai_masters'",
        settings=migrated,
    )
    assert row["active"] == 0


@respx.mock
async def test_synchronisation_ne_desactive_jamais_l_existant(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(200, json=SPORTS_PAYLOAD, headers=QUOTA_HEADERS)
    )

    await sync_from_api(odds_client, migrated)

    row = db.query_one(
        "SELECT active FROM competitions WHERE oddsapi_key = 'soccer_epl'", settings=migrated
    )
    assert row["active"] == 1, "la Premier League etait active, elle le reste"


@respx.mock
async def test_le_libelle_du_fournisseur_fait_foi(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(200, json=SPORTS_PAYLOAD, headers=QUOTA_HEADERS)
    )

    report = await sync_from_api(odds_client, migrated)

    row = db.query_one(
        "SELECT label FROM competitions WHERE oddsapi_key = 'soccer_epl'", settings=migrated
    )
    assert row["label"] == "EPL"
    assert any("EPL" in item for item in report.updated)


@respx.mock
async def test_synchronisation_idempotente(odds_client: OddsAPIClient, migrated: Settings) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(200, json=SPORTS_PAYLOAD, headers=QUOTA_HEADERS)
    )

    await sync_from_api(odds_client, migrated)
    total = len(list_all(migrated))
    second = await sync_from_api(odds_client, migrated)

    assert len(list_all(migrated)) == total
    assert second.created == []


@respx.mock
async def test_synchronisation_gratuite(odds_client: OddsAPIClient, migrated: Settings) -> None:
    """`/sports` est gratuit : le cout facture doit rester nul."""
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=SPORTS_PAYLOAD,
            headers={"x-requests-remaining": "4821", "x-requests-last": "0"},
        )
    )

    await sync_from_api(odds_client, migrated)

    row = db.query_one("SELECT cost FROM api_usage", settings=migrated)
    assert row["cost"] == 0


# -- Routes -----------------------------------------------------------------


def test_page_competitions(client: TestClient) -> None:
    response = client.get("/competitions")

    assert response.status_code == 200
    assert "Ligue 1" in response.text
    assert "ATP — US Open" in response.text
    assert "Synchroniser depuis The Odds API" in response.text


def test_activation_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    tennis = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=isolated_settings,
    )

    response = client.post(f"/competitions/{tennis['id']}/active", data={"active": "1"})

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="competitions">')
    row = db.query_one(
        "SELECT active FROM competitions WHERE id = ?", (tennis["id"],), settings=isolated_settings
    )
    assert row["active"] == 1


def test_desactivation_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    ligue1 = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'soccer_france_ligue_one'",
        settings=isolated_settings,
    )

    client.post(f"/competitions/{ligue1['id']}/active", data={})

    row = db.query_one(
        "SELECT active FROM competitions WHERE id = ?", (ligue1["id"],), settings=isolated_settings
    )
    assert row["active"] == 0


@respx.mock
def test_synchronisation_via_htmx(client: TestClient) -> None:
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(200, json=SPORTS_PAYLOAD, headers=QUOTA_HEADERS)
    )

    response = client.post("/competitions/sync")

    assert response.status_code == 200
    assert "Aucun crédit consommé" in response.text


@respx.mock
def test_synchronisation_en_echec_ne_casse_pas_la_page(client: TestClient) -> None:
    respx.get(f"{BASE_URL}/sports").mock(return_value=httpx.Response(503, text="HS"))

    response = client.post("/competitions/sync")

    assert response.status_code == 200, "une API HS ne doit jamais empecher de servir la page"


# -- Catalogue complet ------------------------------------------------------


@respx.mock
async def test_le_catalogue_complet_est_demande(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Sans `all=true`, une competition hors saison reste introuvable."""
    respx.get(f"{BASE_URL}/sports").mock(return_value=httpx.Response(200, json=[]))

    await sync_from_api(odds_client, migrated)

    assert respx.calls.last.request.url.params.get("all") == "true"


@respx.mock
async def test_une_competition_hors_saison_est_creee_et_signalee(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """C'est tout l'interet : l'activer d'avance, avant que les cotes arrivent."""
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=[
                {
                    "key": "soccer_uefa_europa_league",
                    "title": "UEFA Europa League",
                    "active": False,
                },
                {"key": "soccer_epl", "title": "EPL", "active": True},
            ],
        )
    )

    report = await sync_from_api(odds_client, migrated)

    assert report.dormant == 1
    par_cle = {row["oddsapi_key"]: row for row in list_all(migrated)}
    assert par_cle["soccer_uefa_europa_league"]["api_active"] == 0
    assert par_cle["soccer_epl"]["api_active"] == 1
    assert par_cle["soccer_uefa_europa_league"]["active"] == 0, "creee inactive, comme toujours"


@respx.mock
async def test_la_disponibilite_suit_le_fournisseur_sans_toucher_a_l_activation(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Le jour ou les cotes arrivent, la competition deja activee doit scanner."""
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200, json=[{"key": "soccer_uefa_europa_league", "title": "UEFA EL", "active": False}]
        )
    )
    await sync_from_api(odds_client, migrated)
    dormante = next(
        row for row in list_all(migrated) if row["oddsapi_key"] == "soccer_uefa_europa_league"
    )
    set_active(int(dormante["id"]), True, migrated)

    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200, json=[{"key": "soccer_uefa_europa_league", "title": "UEFA EL", "active": True}]
        )
    )
    await sync_from_api(odds_client, migrated)

    reveillee = next(
        row for row in list_all(migrated) if row["oddsapi_key"] == "soccer_uefa_europa_league"
    )
    assert reveillee["api_active"] == 1
    assert reveillee["active"] == 1, "l'activation choisie par l'utilisateur n'est jamais touchee"


def test_chaque_competition_porte_son_pictogramme(migrated: Settings) -> None:
    """Le pictogramme est un SVG du sprite, designe par la cle du sport — plus
    un emoji rendu par la police de l'appareil, donc different d'une machine a
    l'autre et absent de certaines. C'est la cle qui doit arriver au gabarit."""
    sports = {row["sport_key"] for row in list_all(migrated)}

    assert {"football", "tennis"} <= sports
    assert all(has_sport_icon(key) for key in sports), "le sprite couvre tous les sports servis"


# -- Niveau de tournoi ------------------------------------------------------


def test_les_grands_chelems_sont_seedes_avec_leur_niveau(migrated: Settings) -> None:
    """Les cles The Odds API designent un tournoi identifie : le seed est une
    decision humaine, verifiee tournoi par tournoi — pas une deduction."""
    par_cle = {
        row["oddsapi_key"]: row["category"]
        for row in db.query("SELECT oddsapi_key, category FROM competitions", settings=migrated)
    }

    assert par_cle["tennis_atp_wimbledon"] == "grand_slam"
    assert par_cle["tennis_wta_us_open"] == "grand_slam"


def test_les_competitions_football_sont_seedees_avec_leur_niveau(migrated: Settings) -> None:
    """Sans niveau, cinquante-neuf selections football etaient invisibles.

    Elles se repartissaient sur douze championnats de une a six lignes, donc
    sous le seuil de lecture par competition et noyees ensemble sous
    « Football » : le seul regroupement intermediaire manquait.

    Seules les competitions livrees par la migration 002 sont en base ici ; le
    reste du catalogue arrive par la synchronisation, et c'est
    `COMPETITION_CATEGORIES` qui le classe.
    """
    par_cle = {
        row["oddsapi_key"]: row["category"]
        for row in db.query("SELECT oddsapi_key, category FROM competitions", settings=migrated)
    }

    assert par_cle["soccer_epl"] == "d1_top5"
    assert par_cle["soccer_france_ligue_one"] == "d1_top5"
    assert par_cle["soccer_sweden_allsvenskan"] == "d1_europe"
    assert par_cle["soccer_norway_eliteserien"] == "d1_europe"
    assert par_cle["soccer_china_superleague"] == "d1_hors_europe"


def test_les_migrations_rejouent_la_table_des_niveaux() -> None:
    """Trois ecritures de la meme decision : elles doivent dire la meme chose.

    Les migrations classent ce qui est **deja en base** quand elles tournent, la
    table Python ce que la synchronisation decouvre ensuite. Les laisser diverger
    donnerait deux niveaux differents a la meme competition selon la date
    d'installation — et personne ne s'en apercevrait, un niveau ne se voyant
    nulle part sur le board.

    Le test relit les fichiers de migration plutot que d'en recopier la regle,
    comme celui de la migration 021.
    """
    seeds: dict[str, str] = {}
    racine = Path(competitions_module.__file__).parent.parent / "migrations"
    for nom in ("013_competition_category.sql", "024_niveaux_football.sql"):
        sql = (racine / nom).read_text(encoding="utf-8")
        for bloc in re.finditer(
            r"SET category = '(\w+)'.*?IN \((.*?)\);", sql, flags=re.DOTALL | re.IGNORECASE
        ):
            for cle in re.findall(r"'([a-z0-9_]+)'", bloc.group(2)):
                seeds[cle] = bloc.group(1)

    assert seeds, "les migrations classent bien des competitions"
    assert seeds == COMPETITION_CATEGORIES


def test_les_qualifications_europeennes_suivent_leur_competition() -> None:
    """Pas un arbitrage, une contrainte de la source.

    The Odds API sert les tours preliminaires et la phase de ligue **sous la
    meme cle** pour l'Europa League comme pour la Conference League : un niveau
    se pose sur une cle, donc les separer est hors de portee. La qualification
    de Ligue des champions, elle, a bien sa cle, et rien ne justifierait de la
    ranger ailleurs que sa competition.
    """
    assert COMPETITION_CATEGORIES["soccer_uefa_champs_league"] == "coupe_continentale"
    assert COMPETITION_CATEGORIES["soccer_uefa_champs_league_qualification"] == "coupe_continentale"


def test_tout_niveau_seede_existe_dans_la_taxonomie_de_son_sport() -> None:
    """Une faute de frappe dans une cle ne casserait rien, et c'est le danger.

    La competition sortirait avec un niveau qu'aucun libelle ne nomme : ligne
    sans nom dans les statistiques, et jamais reclamee dans « a classer »
    puisqu'elle porte bien une valeur.
    """
    for cle, niveau in COMPETITION_CATEGORIES.items():
        sport = "tennis" if cle.startswith("tennis_") else "football"
        assert niveau in categories_for(sport), f"{cle} porte un niveau inconnu de {sport}"


@respx.mock
async def test_une_competition_decouverte_arrive_avec_son_niveau(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Les migrations ne classent que l'existant ; la synchronisation decouvre.

    Sans cette table, chaque competition apparue apres le seed arriverait sans
    niveau — donc reclamee dans « a classer » alors que sa place ne fait aucun
    doute.
    """
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "soccer_uefa_europa_league", "title": "UEFA Europa League"}],
            headers=QUOTA_HEADERS,
        )
    )

    await sync_from_api(odds_client, migrated)

    assert (
        db.query_one(
            "SELECT category FROM competitions WHERE oddsapi_key = 'soccer_uefa_europa_league'",
            settings=migrated,
        )["category"]
        == "coupe_continentale"
    )


def test_les_cles_de_niveau_ne_se_chevauchent_pas_entre_sports() -> None:
    """`CATEGORIES` fusionne les deux tables : une cle commune en perdrait une.

    Le degat serait muet — un niveau de football rendu sous un libelle de
    tennis — et c'est exactement la sorte d'erreur que ce projet refuse de
    laisser passer sans un mot.
    """
    plat = sum(len(niveaux) for niveaux in CATEGORIES_BY_SPORT.values())

    assert len(CATEGORIES) == plat
    assert categories_for("cycling") == {}, "un sport sans taxonomie ne propose rien"


def test_le_niveau_se_saisit_et_se_retire(migrated: Settings) -> None:
    competition = next(row for row in list_all(migrated) if row["sport_key"] == "tennis")

    set_category(competition["id"], "masters_1000", migrated)
    assert _category(migrated, competition["id"]) == "masters_1000"

    set_category(competition["id"], "", migrated)
    assert _category(migrated, competition["id"]) is None


def test_un_niveau_inconnu_vaut_non_renseigne(migrated: Settings) -> None:
    """Comme la surface : le seul effet est une ligne de moins en statistiques."""
    competition = next(row for row in list_all(migrated) if row["sport_key"] == "tennis")
    set_category(competition["id"], "masters_1000", migrated)

    set_category(competition["id"], "super_masters", migrated)

    assert _category(migrated, competition["id"]) is None


def test_les_competitions_sont_rangees_par_niveau(migrated: Settings) -> None:
    """Sur quarante tournois, l'alphabet melange un Grand Chelem et un 500."""
    tennis = [row["id"] for row in list_all(migrated) if row["sport_key"] == "tennis"]
    set_category(tennis[0], "level_250", migrated)

    ordre = [row["category"] for row in list_all(migrated) if row["sport_key"] == "tennis"]

    assert ordre.index("grand_slam") < ordre.index("level_250")


def test_le_selecteur_de_niveau_sert_les_sports_qui_ont_une_taxonomie(client: TestClient) -> None:
    """Un menu par sport, et aucun menu la ou il n'y a rien a proposer.

    Le cyclisme n'a pas de niveaux : lui en afficher un vide reclamerait une
    saisie impossible a faire, et la liste « a classer » le reclamerait ensuite
    tous les jours.
    """
    page = client.get("/competitions").text

    assert "Masters 1000" in page
    assert "1re division — top 5" in page
    assert (
        page.count('name="category"')
        == db.query_one(
            "SELECT COUNT(*) AS n FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "WHERE s.key IN ('tennis', 'football')"
        )["n"]
    )


def test_un_niveau_de_tennis_est_refuse_sur_une_competition_football(migrated: Settings) -> None:
    """La validation lit la taxonomie **du sport**, pas la liste a plat.

    Depuis que le football a la sienne, « grand_slam » est une cle connue :
    l'accepter sur une Ligue 1 produirait un regroupement que plus rien ne
    distinguerait d'un vrai tournoi.
    """
    football = next(row for row in list_all(migrated) if row["sport_key"] == "football")

    set_category(football["id"], "d1_top5", migrated)
    assert _category(migrated, football["id"]) == "d1_top5"

    set_category(football["id"], "grand_slam", migrated)
    assert _category(migrated, football["id"]) is None


# -- La liste « a classer » -------------------------------------------------


def _pick_sur(settings: Settings, oddsapi_key: str, result: str = "win") -> None:
    """Une selection rattachee a un match de cette competition."""
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('t', '2026-08-04T10:00:00Z')",
        settings=settings,
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, created_at) "
        "SELECT c.sport_id, c.id, 'A', 'B', '2026-08-04T18:00:00Z', '2026-08-04T10:00:00Z' "
        "FROM competitions c WHERE c.oddsapi_key = ?",
        (oddsapi_key,),
        settings=settings,
    )
    db.execute(
        "INSERT INTO picks (session_id, event_id, tier, market, selection, result, created_at) "
        "SELECT (SELECT MAX(id) FROM sessions), (SELECT MAX(id) FROM events), "
        "       'safe', 'O/U', 'Over', ?, '2026-08-04T10:00:00Z'",
        (result,),
        settings=settings,
    )


def test_une_competition_non_classee_qui_porte_des_selections_est_reclamee(
    migrated: Settings,
) -> None:
    """Une cle non classee ne doit jamais disparaitre en silence.

    Sans niveau, ses selections sortent du regroupement « par niveau » sans
    qu'aucune ligne ne le dise — c'est ainsi que cinquante-neuf selections
    football sont restees invisibles cent paris durant.
    """
    db.execute(
        "UPDATE competitions SET category = NULL, active = 0 WHERE oddsapi_key = 'soccer_epl'",
        settings=migrated,
    )
    _pick_sur(migrated, "soccer_epl")
    _pick_sur(migrated, "soccer_epl", result="pending")

    a_classer = unclassified(migrated)

    ligne = next(row for row in a_classer if row.label == "Premier League")
    assert (ligne.picks, ligne.settled) == (2, 1)
    # Rangee en tete : classer une competition a deux paris repare deux lignes,
    # classer une competition vierge n'en repare aucune.
    assert a_classer[0].competition_id == ligne.competition_id


def test_une_competition_classee_ne_figure_pas_a_classer(migrated: Settings) -> None:
    _pick_sur(migrated, "soccer_epl")

    assert all(row.label != "Premier League" for row in unclassified(migrated))


def test_un_sport_sans_taxonomie_n_est_jamais_reclame(migrated: Settings) -> None:
    """Le cyclisme n'a pas de niveaux : le reclamer serait une tache impossible."""
    db.execute(
        "UPDATE competitions SET active = 1 "
        "WHERE sport_id = (SELECT id FROM sports WHERE key = 'cycling')",
        settings=migrated,
    )

    assert all(row.sport_key != "cycling" for row in unclassified(migrated))


def test_la_liste_a_classer_est_affichee(client: TestClient, isolated_settings: Settings) -> None:
    db.execute(
        "UPDATE competitions SET category = NULL WHERE oddsapi_key = 'soccer_epl'",
        settings=isolated_settings,
    )
    _pick_sur(isolated_settings, "soccer_epl")

    page = " ".join(client.get("/competitions").text.split())

    assert "à classer" in page
    assert "Premier League</b> — 1 sélection(s)" in page


def test_niveau_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    competition = next(row for row in list_all(isolated_settings) if row["sport_key"] == "tennis")

    response = client.post(
        f"/competitions/{competition['id']}/category", data={"category": "level_500"}
    )

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="competitions">')
    assert _category(isolated_settings, competition["id"]) == "level_500"


def _category(settings: Settings, competition_id: int) -> str | None:
    row = db.query_one(
        "SELECT category FROM competitions WHERE id = ?", (competition_id,), settings=settings
    )
    return row["category"] if row else None


# -- Rattachement a une ligue API-Football ------------------------------------


def _league(settings: Settings, competition_id: int) -> int | None:
    row = db.query_one(
        "SELECT apifootball_league_id FROM competitions WHERE id = ?",
        (competition_id,),
        settings=settings,
    )
    return row["apifootball_league_id"] if row else None


def test_la_correspondance_evite_les_pieges_du_rapprochement_par_libelle() -> None:
    """Sans identifiant de ligue, `enrich.context_possible` est faux et aucun
    contexte n'est jamais demande. Ces trois-la, un rapprochement automatique
    les donne faux avec un score maximal — d'ou une table verifiee a la main."""
    assert APIFOOTBALL_LEAGUES["soccer_efl_champ"] == 40, "l'anglaise, pas l'ecossaise (180)"
    assert APIFOOTBALL_LEAGUES["soccer_germany_bundesliga2"] == 79, "pas la Bundesliga (78)"
    assert APIFOOTBALL_LEAGUES["soccer_usa_mls"] == 253, "pas la Coupe de Malaisie (499)"


def test_les_qualifications_europeennes_pointent_sur_leur_competition() -> None:
    """API-Football sert les tours preliminaires sous la competition elle-meme
    (`round = "3rd Qualifying Round"`) : il n'existe pas d'identifiant distinct
    pour la qualification, contrairement a The Odds API qui en a une cle."""
    assert APIFOOTBALL_LEAGUES["soccer_uefa_champs_league_qualification"] == 2
    assert APIFOOTBALL_LEAGUES["soccer_uefa_europa_league"] == 3
    assert APIFOOTBALL_LEAGUES["soccer_uefa_europa_conference_league"] == 848


@respx.mock
async def test_une_competition_decouverte_arrive_deja_rattachee(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Le defaut repare : la synchronisation creait des competitions sans ligue,
    donc muettes, et il fallait une migration pour chaque nouvelle."""
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "soccer_italy_serie_a", "group": "Soccer", "title": "Serie A - Italy"}],
            headers=QUOTA_HEADERS,
        )
    )

    await sync_from_api(odds_client, migrated)

    row = db.query_one(
        "SELECT apifootball_league_id FROM competitions WHERE oddsapi_key = 'soccer_italy_serie_a'",
        settings=migrated,
    )
    assert row["apifootball_league_id"] == 135


@respx.mock
async def test_la_synchronisation_comble_un_manque_sans_ecraser_une_saisie(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Un rattachement corrige a la main prime pour toujours, comme un alias."""
    respx.get(f"{BASE_URL}/sports").mock(
        return_value=httpx.Response(
            200,
            json=[{"key": "soccer_italy_serie_a", "group": "Soccer", "title": "Serie A - Italy"}],
            headers=QUOTA_HEADERS,
        )
    )
    await sync_from_api(odds_client, migrated)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'soccer_italy_serie_a'", settings=migrated
    )
    set_apifootball_league(competition["id"], "999", migrated)

    await sync_from_api(odds_client, migrated)

    assert _league(migrated, competition["id"]) == 999


def test_le_rattachement_se_saisit_et_se_retire(migrated: Settings) -> None:
    competition = next(row for row in list_all(migrated) if row["sport_key"] == "football")

    set_apifootball_league(competition["id"], "140", migrated)
    assert _league(migrated, competition["id"]) == 140

    set_apifootball_league(competition["id"], "", migrated)
    assert _league(migrated, competition["id"]) is None


def test_un_rattachement_illisible_vaut_non_rattache(migrated: Settings) -> None:
    """L'effet est une ligne de contexte absente, jamais une donnee fausse."""
    competition = next(row for row in list_all(migrated) if row["sport_key"] == "football")
    set_apifootball_league(competition["id"], "140", migrated)

    set_apifootball_league(competition["id"], "la liga", migrated)

    assert _league(migrated, competition["id"]) is None


def test_rattachement_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    competition = next(row for row in list_all(isolated_settings) if row["sport_key"] == "football")

    response = client.post(
        f"/competitions/{competition['id']}/apifootball",
        data={"apifootball_league_id": "61"},
    )

    assert response.status_code == 200
    assert "<html" not in response.text, "une route ciblee par HTMX rend le fragment, pas la page"
    assert _league(isolated_settings, competition["id"]) == 61


def test_le_champ_de_ligue_ne_sert_qu_au_football(client: TestClient) -> None:
    """Le tennis recoit son contexte de Tennis Abstract, pas d'API-Football."""
    page = client.get("/competitions").text

    assert 'name="apifootball_league_id"' in page
    tennis = [ligne for ligne in page.splitlines() if "Wimbledon" in ligne]
    assert tennis and all("apifootball_league_id" not in ligne for ligne in tennis)
