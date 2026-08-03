from __future__ import annotations

from typing import Any

import httpx
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services.scan import active_competitions, run_scan, scan_window

from .helpers import NOW, QUOTA_HEADERS


def _mock_all_competitions(payload_by_key: dict[str, Any]) -> dict[str, respx.Route]:
    """Repond a chaque competition active : la fixture si connue, sinon une liste vide.

    Renvoie les routes par cle de competition, pour pouvoir en ajuster une ensuite.
    """
    routes: dict[str, respx.Route] = {}
    for competition in active_competitions():
        key = competition["oddsapi_key"]
        routes[key] = respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200, json=payload_by_key.get(key, []), headers=QUOTA_HEADERS
            )
        )
    return routes


def test_seed_des_competitions(migrated: Settings) -> None:
    competitions = active_competitions(migrated)

    labels = {item["label"] for item in competitions}
    assert labels == {
        "Ligue 1",
        "Premier League",
        "Allsvenskan",
        "Eliteserien",
        "Chinese Super League",
        "Liga Portugal",
        "Super Lig",
    }
    assert competitions[0]["label"] == "Ligue 1", "tri par priorite decroissante"
    assert all(item["sport_key"] == "football" for item in competitions)


def test_fenetre_de_scan_couvre_j0_et_j1(migrated: Settings) -> None:
    start, end = scan_window(migrated, NOW)

    assert start == NOW
    # Fin de journee du 4 aout a Paris (UTC+2) = 21:59:59Z.
    assert end.isoformat() == "2026-08-04T21:59:59+00:00"


@respx.mock
async def test_scan_persiste_evenements_et_cotes(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.total_events == 2, "le match du 11 aout est hors fenetre"
    assert report.failures == []
    assert report.total_cost == 2 * len(active_competitions(migrated))

    events = db.query("SELECT * FROM events ORDER BY commence_time", settings=migrated)
    assert [row["home"] for row in events] == ["BK Hacken", "IFK Norrkoping"]
    assert events[0]["source"] == "api"

    odds = db.query(
        "SELECT market_key, outcome_name, point, price FROM odds "
        "WHERE event_id = ? ORDER BY market_key, point, outcome_name",
        (events[0]["id"],),
        settings=migrated,
    )
    assert len([row for row in odds if row["market_key"] == "h2h"]) == 3
    assert len([row for row in odds if row["market_key"] == "totals"]) == 6
    assert odds[0]["price"] == 2.55


@respx.mock
async def test_scan_est_idempotent(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    payload = {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    _mock_all_competitions(payload)

    await run_scan(odds_client, migrated, now=NOW)
    counts_after_first = (
        len(db.query("SELECT id FROM events", settings=migrated)),
        len(db.query("SELECT id FROM odds", settings=migrated)),
    )

    await run_scan(odds_client, migrated, now=NOW)
    counts_after_second = (
        len(db.query("SELECT id FROM events", settings=migrated)),
        len(db.query("SELECT id FROM odds", settings=migrated)),
    )

    assert counts_after_first == counts_after_second == (2, 12)


@respx.mock
async def test_les_cotes_sont_remplacees_pas_accumulees(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    routes = _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)

    # Nouveau releve : la cote domicile bouge et le marche totals disparait.
    payload[0]["bookmakers"][0]["markets"] = [
        {
            "key": "h2h",
            "outcomes": [
                {"name": "BK Hacken", "price": 2.9},
                {"name": "Djurgardens IF", "price": 2.4},
                {"name": "Draw", "price": 3.5},
            ],
        }
    ]
    routes["soccer_sweden_allsvenskan"].mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await run_scan(odds_client, migrated, now=NOW)

    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=migrated)
    prices = db.query(
        "SELECT market_key, price FROM odds WHERE event_id = ? AND outcome_name = 'BK Hacken'",
        (event["id"],),
        settings=migrated,
    )
    assert [row["price"] for row in prices] == [2.9]
    # Un marche absent du nouveau releve conserve son dernier etat connu.
    totals = db.query(
        "SELECT id FROM odds WHERE event_id = ? AND market_key = 'totals'",
        (event["id"],),
        settings=migrated,
    )
    assert len(totals) == 6


@respx.mock
async def test_une_competition_en_echec_n_interrompt_pas_le_scan(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    routes = _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )
    routes["soccer_epl"].mock(return_value=httpx.Response(503, text="indisponible"))

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.total_events == 2
    assert [failure.label for failure in report.failures] == ["Premier League"]
    assert "503" in report.failures[0].error


@respx.mock
async def test_evenement_hors_fenetre_ignore(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    await run_scan(odds_client, migrated, now=NOW)

    assert db.query_one("SELECT id FROM events WHERE home = 'AIK'", settings=migrated) is None


@respx.mock
async def test_borne_de_fenetre_transmise_a_l_api(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    routes = _mock_all_competitions({})

    await run_scan(odds_client, migrated, now=NOW)

    request = routes["soccer_france_ligue_one"].calls[0].request
    assert request.url.params["commenceTimeTo"] == "2026-08-04T21:59:59Z"
