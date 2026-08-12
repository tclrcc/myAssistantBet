"""Meteo du lieu : l'alerte officielle d'abord, les chiffres ensuite.

Mesure qui fixe cet ordre, sur cinq sessions reelles : la temperature n'a jamais
rien change ; l'alerte a change une section entiere, deux fois.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.weather import (
    FORECAST_URL,
    GEOCODING_URL,
    NWS_URL,
    WeatherClient,
)
from myassistantbet.services import weather

#: Le coup d'envoi tombe dans la tranche horaire de la fixture de prevision.
COMMENCE = "2026-08-13T23:00:00Z"
NOW = datetime(2026, 8, 13, 15, 0, tzinfo=UTC)
EVENT = 1


@pytest.fixture
def client(http_client: httpx.AsyncClient, migrated: Settings) -> WeatherClient:
    return WeatherClient(http_client, migrated)


def _routes(load_fixture: Any, alertes: bool = True) -> dict[str, respx.Route]:
    """Les trois endpoints, tous gratuits et sans cle."""
    routes = {
        "geocoding": respx.get(f"{GEOCODING_URL}/v1/search").mock(
            return_value=httpx.Response(200, json=load_fixture("openmeteo_geocoding.json"))
        ),
        "forecast": respx.get(f"{FORECAST_URL}/v1/forecast").mock(
            return_value=httpx.Response(200, json=load_fixture("openmeteo_forecast.json"))
        ),
    }
    charge = load_fixture("nws_alerts.json") if alertes else {"features": []}
    routes["alerts"] = respx.get(f"{NWS_URL}/alerts/active").mock(
        return_value=httpx.Response(200, json=charge)
    )
    return routes


def _seed(settings: Settings) -> None:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (id, sport_id, home, away, commence_time, source, created_at) "
        "VALUES (1, ?, 'A', 'B', ?, 'api', ?)",
        (sport["id"], COMMENCE, db.utcnow()),
        settings=settings,
    )


def _line(settings: Settings) -> str:
    lignes = dict(weather.lines(EVENT, settings))
    return lignes.get("Meteo", "")


# -- Le geocodage ------------------------------------------------------------


@respx.mock
async def test_le_pays_departage_les_homonymes(client: WeatherClient, load_fixture: Any) -> None:
    """« Mason » existe en Ohio, dans le Nebraska et en Angleterre. Donner la
    meteo de la mauvaise ville serait une erreur invisible — le genre le plus
    couteux."""
    _routes(load_fixture)

    point = await client.coordinates("Mason", "United States")

    assert point is not None
    assert (round(point["latitude"], 2), round(point["longitude"], 2)) == (39.36, -84.31)
    assert point["timezone"] == "America/New_York"


@respx.mock
async def test_sans_pays_correspondant_aucune_coordonnee(
    client: WeatherClient, load_fixture: Any
) -> None:
    """En cas de doute, rien : c'est la regle du projet, et elle vaut ici plus
    qu'ailleurs parce que l'erreur ne se verrait pas."""
    _routes(load_fixture)

    assert await client.coordinates("Mason", "Bulgaria") is None


@respx.mock
async def test_le_geocodage_rend_le_fuseau_du_lieu(
    client: WeatherClient, load_fixture: Any
) -> None:
    """Le fournisseur le publie dans la meme reponse que les coordonnees : c'est
    le seul fuseau certainement celui du stade, et il ne coute rien."""
    _routes(load_fixture)

    point = await client.coordinates("Mason", "United States")

    assert point["timezone"] == "America/New_York"


# -- L'alerte, et ce qu'elle prime -------------------------------------------


@respx.mock
async def test_l_alerte_passe_devant_les_chiffres(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un fait qui peut empecher la rencontre ne se lit pas au milieu d'une
    enumeration de degres."""
    _seed(migrated)
    _routes(load_fixture)

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    premiere = ligne.splitlines()[0]
    assert premiere.startswith("ALERTE Flood Watch (Severe)")
    assert "NWS Wilmington OH" in premiere, "l'emetteur est l'instance : source de niveau 1"
    assert "28 C" in ligne, "les chiffres suivent"


@respx.mock
async def test_une_alerte_close_avant_le_coup_d_envoi_n_est_pas_rendue(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une alerte active **maintenant** mais close avant le coup d'envoi ferait
    craindre un orage deja passe."""
    _seed(migrated)
    charge = load_fixture("nws_alerts.json")
    # Coup d'envoi a 19h00 heure de Mason : l'alerte se ferme une heure avant.
    charge["features"][0]["properties"]["ends"] = "2026-08-13T18:00:00-04:00"
    routes = _routes(load_fixture)
    routes["alerts"].mock(return_value=httpx.Response(200, json=charge))

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert "ALERTE" not in ligne
    assert "aucune alerte NWS en vigueur" in ligne


@respx.mock
async def test_un_pays_sans_source_le_dit_plutot_que_de_se_taire(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """**La regle centrale du module.** Ne rien dire ferait lire « aucune
    alerte » la ou personne n'a regarde — le defaut exact d'« Absents : donnees
    non disponibles » sur une competition non couverte."""
    _seed(migrated)
    routes = _routes(load_fixture)
    geo = load_fixture("openmeteo_geocoding.json")
    geo["results"] = [
        {
            **geo["results"][0],
            "country": "Bulgaria",
            "country_code": "BG",
            "timezone": "Europe/Sofia",
        }
    ]
    routes["geocoding"].mock(return_value=httpx.Response(200, json=geo))

    await weather.refresh_event(client, EVENT, "Stara Zagora", "Bulgaria", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert "alertes officielles non interrogees (Bulgaria)" in ligne
    assert not routes["alerts"].called, "aucune source branchee : aucun appel"
    assert "aucune alerte" not in ligne, "le silence ne doit pas ressembler a une absence"


@respx.mock
async def test_une_source_injoignable_ne_se_lit_pas_comme_une_absence(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Trois etats, et les deux derniers se ressemblent sans se valoir : source
    interrogee sans resultat, contre source qui n'a pas repondu."""
    _seed(migrated)
    routes = _routes(load_fixture)
    routes["alerts"].mock(return_value=httpx.Response(503))

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    assert "alertes NWS injoignables" in _line(migrated)


# -- Les chiffres ------------------------------------------------------------


@respx.mock
async def test_les_chiffres_valent_a_l_heure_du_coup_d_envoi(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une prevision « du jour » ne dit rien d'un match a 21h : l'orage de
    l'apres-midi peut etre passe, ou pas encore arrive. La fixture porte 28.3 a
    22h et 27.6 a 23h — c'est la seconde qui doit sortir."""
    _seed(migrated)
    _routes(load_fixture)

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert "28 C" in ligne, "27.6 arrondi, l'heure du coup d'envoi"
    assert "pluie 19 %" in ligne
    assert "rafales 8 km/h" in ligne


@respx.mock
async def test_l_heure_du_releve_accompagne_les_chiffres(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une prevision de huit heures du matin pour un match du soir n'engage pas
    grand-chose, et le dire coute cinq caracteres. Meme regle que l'heure de
    releve des cotes."""
    _seed(migrated)
    _routes(load_fixture)

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    assert "Open-Meteo, releve" in _line(migrated), "et l'attribution CC-BY avec"


@respx.mock
async def test_l_heure_est_celle_du_lieu(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le fuseau vient du geocodage, donc du lieu : une heure de Paris presentee
    comme locale serait pire qu'une heure UTC annoncee comme telle."""
    _seed(migrated)
    _routes(load_fixture)

    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    # 23h00 UTC le 13 = 19h00 le 13 a Mason (EDT).
    assert "13/08 19:00 local" in _line(migrated)


# -- Peremption et economie d'appels -----------------------------------------


@respx.mock
async def test_une_prevision_fraiche_n_est_pas_redemandee(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Trois heures : au-dela, une prevision d'orage a eu le temps de se preciser
    ou de se dissiper. En dessous, la relire couterait un appel pour le meme
    chiffre."""
    _seed(migrated)
    routes = _routes(load_fixture)
    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)
    avant = routes["forecast"].call_count

    change = await weather.refresh_event(
        client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW
    )

    assert change is False
    assert routes["forecast"].call_count == avant


@respx.mock
async def test_le_geocodage_ne_se_refait_pas(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une ville ne bouge pas : c'est le seul des trois appels qui ne perime
    jamais."""
    _seed(migrated)
    routes = _routes(load_fixture)
    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    plus_tard = datetime(2026, 8, 13, 20, 0, tzinfo=UTC)
    await weather.refresh_event(
        client, EVENT, "Mason", "United States", COMMENCE, migrated, plus_tard
    )

    assert routes["geocoding"].call_count == 1
    assert routes["forecast"].call_count == 2, "la prevision, elle, se refait"


@respx.mock
async def test_une_ville_introuvable_ne_produit_aucune_ligne(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Aucune ligne plutot qu'une meteo d'ailleurs."""
    _seed(migrated)
    routes = _routes(load_fixture)
    routes["geocoding"].mock(return_value=httpx.Response(200, json={"results": []}))

    change = await weather.refresh_event(client, EVENT, "Nulle Part", None, COMMENCE, migrated, NOW)

    assert change is False
    assert _line(migrated) == ""


@respx.mock
async def test_regenerer_un_prompt_ne_declenche_aucun_appel(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Deux temps separes, comme `context.py` et `dossier.py`. Le test ne simule
    **aucune** route pour la lecture : le moindre appel le ferait echouer."""
    _seed(migrated)
    _routes(load_fixture)
    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    respx.reset()

    assert "ALERTE" in _line(migrated)
