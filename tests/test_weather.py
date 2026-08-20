"""Meteo du lieu : l'alerte officielle d'abord, les chiffres ensuite.

Mesure qui fixe cet ordre, sur cinq sessions reelles : la temperature n'a jamais
rien change ; l'alerte a change une section entiere, deux fois.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.weather import (
    FORECAST_URL,
    GEOCODING_URL,
    METEOALARM_URL,
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


@respx.mock
async def test_un_releve_vieilli_donne_son_age_plutot_que_son_heure(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Trois heures d'ecart sont sans consequence ; le meme mecanisme sur un
    releve du matin pour un match du soir servirait une prevision perimee avec la
    meme autorite. Un age se lit sans soustraction — meme exigence que l'age du
    releve de cotes."""
    _seed(migrated)
    _routes(load_fixture)
    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    tard = NOW + timedelta(hours=8)
    ligne = dict(weather.lines(EVENT, migrated, now=tard))["Meteo"]

    assert "releve il y a 8 h" in ligne
    assert "releve 13/08" not in ligne, "l'heure seule ne dit pas l'ecart"


@respx.mock
async def test_un_releve_frais_garde_son_heure(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sous la fenetre de fraicheur, l'heure suffit : ecrire « il y a 1 h » sur
    chaque bloc ferait du bruit pour un ecart qui ne change rien."""
    _seed(migrated)
    _routes(load_fixture)
    await weather.refresh_event(client, EVENT, "Mason", "United States", COMMENCE, migrated, NOW)

    ligne = dict(weather.lines(EVENT, migrated, now=NOW + timedelta(hours=1)))["Meteo"]

    assert "releve 13/08 11:00 local" in ligne
    assert "il y a" not in ligne


# --- Lot 13 : les alertes MeteoAlarm, resolues par polygone ---------------


def test_ring_lit_le_couple_lat_lon_de_cap():
    """CAP ecrit « lat,lon » ; le module raisonne en (lon, lat).

    Inverser les deux fait tomber le point dans la mer, donc rendre « aucune
    alerte en vigueur » : le seul mode d'echec que ce chantier ferme.
    """
    assert weather._ring("59.91,10.75 60.0,11.0") == [(10.75, 59.91), (11.0, 60.0)]
    assert weather._ring("pas un couple") == []


def test_inside_tranche_dedans_et_dehors():
    carre = weather._ring("0,0 0,10 10,10 10,0 0,0")
    assert weather._inside(5.0, 5.0, carre) is True
    assert weather._inside(20.0, 5.0, carre) is False
    assert weather._inside(5.0, 5.0, weather._ring("0,0 1,1")) is False


def _cap(polygone, evenement="Mye lyn", langue="en"):
    aire = {"areaDesc": "Ostlandet"}
    if polygone is not None:
        aire["polygon"] = polygone
    return {
        "alert": {
            "info": [
                {
                    "event": evenement,
                    "senderName": "Meteorologisk Institutt",
                    "severity": "Moderate",
                    "language": langue,
                    "onset": "2026-08-13T18:00:00+00:00",
                    "expires": "2026-08-14T06:00:00+00:00",
                    "area": [aire],
                }
            ]
        }
    }


def test_meteoalarm_retient_le_point_couvert_et_ecarte_l_autre():
    warnings = [_cap(["0,0 0,10 10,10 10,0 0,0"])]
    retenues, orphelines = weather._meteoalarm(warnings, 5.0, 5.0)
    assert orphelines == 0
    assert [row["event"] for row in retenues] == ["Mye lyn"]
    assert retenues[0]["ends"] == "2026-08-14T06:00:00+00:00"
    assert weather._meteoalarm(warnings, 50.0, 50.0) == ([], 0)


def test_une_aire_sans_polygone_se_compte_au_lieu_de_se_taire():
    """Sans polygone on ne sait pas : ce n'est pas une absence d'alerte."""
    retenues, orphelines = weather._meteoalarm([_cap(None)], 5.0, 5.0)
    assert retenues == []
    assert orphelines == 1


def test_infos_ne_compte_pas_deux_fois_la_meme_alerte_en_deux_langues():
    alerte = {
        "info": [
            {"event": "Mye lyn", "language": "no"},
            {"event": "Much lightning", "language": "en-GB"},
        ]
    }
    assert [bloc["event"] for bloc in weather._infos(alerte)] == ["Much lightning"]


def _norvege(load_fixture: Any, warnings: list[dict[str, Any]]) -> dict[str, respx.Route]:
    """Un match a Oslo : geocodage norvegien, et le flux MeteoAlarm en face."""
    routes = _routes(load_fixture)
    geo = load_fixture("openmeteo_geocoding.json")
    geo["results"] = [
        {
            **geo["results"][0],
            "country": "Norway",
            "country_code": "NO",
            "latitude": 59.91,
            "longitude": 10.75,
            "timezone": "Europe/Oslo",
        }
    ]
    routes["geocoding"].mock(return_value=httpx.Response(200, json=geo))
    routes["meteoalarm"] = respx.get(f"{METEOALARM_URL}/feeds-norway").mock(
        return_value=httpx.Response(200, json={"warnings": warnings})
    )
    return routes


@respx.mock
async def test_une_alerte_meteoalarm_sort_avec_son_emetteur_reel(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le polygone couvre le stade : l'alerte est rendue, et l'emetteur recopie.

    C'est ce qui fait tenir le niveau 1 : la ligne cite « Meteorologisk
    Institutt », l'instance, jamais l'agregateur qui la relaie.
    """
    _seed(migrated)
    routes = _norvege(load_fixture, [_cap(["59.0,10.0 59.0,11.5 60.5,11.5 60.5,10.0 59.0,10.0"])])

    await weather.refresh_event(client, EVENT, "Oslo", "Norway", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert weather.ALERT_MARK in ligne
    assert "Meteorologisk Institutt" in ligne
    assert routes["meteoalarm"].called
    assert not routes["alerts"].called, "le NWS ne couvre pas la Norvege"


@respx.mock
async def test_un_polygone_qui_ne_couvre_pas_le_stade_rend_aucune_alerte(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le point n'y est pas : on a regarde, et il n'y a rien. C'est le seul
    cas ou ce libelle est vrai."""
    _seed(migrated)
    _norvege(load_fixture, [_cap(["0,0 0,1 1,1 1,0 0,0"])])

    await weather.refresh_event(client, EVENT, "Oslo", "Norway", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert "aucune alerte" in ligne
    assert weather.ALERT_MARK not in ligne


@respx.mock
async def test_une_aire_sans_polygone_rend_non_interrogees_jamais_aucune_alerte(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le mode d'echec que ce chantier ferme.** Une aire non resolue ne dit
    pas qu'il n'y a pas d'alerte : elle dit qu'on ne sait pas. Rendre « aucune
    alerte » serait affirmer qu'on a regarde."""
    _seed(migrated)
    _norvege(load_fixture, [_cap(None)])

    await weather.refresh_event(client, EVENT, "Oslo", "Norway", COMMENCE, migrated, NOW)

    ligne = _line(migrated)
    assert "non interrogees" in ligne
    assert "aucune alerte" not in ligne


@respx.mock
async def test_un_flux_meteoalarm_injoignable_ne_se_lit_pas_comme_une_absence(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed(migrated)
    routes = _norvege(load_fixture, [])
    routes["meteoalarm"].mock(return_value=httpx.Response(503))

    await weather.refresh_event(client, EVENT, "Oslo", "Norway", COMMENCE, migrated, NOW)

    assert "injoignables" in _line(migrated)


@respx.mock
async def test_un_pays_servi_sans_polygone_reste_non_interroge(
    client: WeatherClient, migrated: Settings, load_fixture: Any
) -> None:
    """L'Espagne a bien un flux, et il n'expose qu'un `EMMA_ID`. Aucune table
    n'est saisie, donc aucun appel n'est emis et la ligne le dit."""
    _seed(migrated)
    routes = _routes(load_fixture)
    geo = load_fixture("openmeteo_geocoding.json")
    geo["results"] = [{**geo["results"][0], "country": "Spain", "country_code": "ES"}]
    routes["geocoding"].mock(return_value=httpx.Response(200, json=geo))

    await weather.refresh_event(client, EVENT, "Sevilla", "Spain", COMMENCE, migrated, NOW)

    assert "alertes officielles non interrogees (Spain)" in _line(migrated)
