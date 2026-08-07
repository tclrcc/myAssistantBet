from __future__ import annotations

from collections.abc import Iterator

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services.competitions import (
    APIFOOTBALL_LEAGUES,
    list_all,
    set_active,
    set_apifootball_league,
    set_category,
    sync_from_api,
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
    assert par_cle["soccer_epl"] is None, "le niveau ne concerne que le tennis pour l'instant"


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


def test_le_selecteur_de_niveau_ne_sert_qu_au_tennis(client: TestClient) -> None:
    """« ATP/WTA 500 » sur une Ligue 1 n'aurait aucun sens."""
    page = client.get("/competitions").text

    assert 'name="category"' in page
    assert "Masters 1000" in page
    assert (
        page.count('name="category"')
        == db.query_one(
            "SELECT COUNT(*) AS n FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "WHERE s.key = 'tennis'"
        )["n"]
    )


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
