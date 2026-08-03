from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.base import ProviderError, last_known_quota
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient, expected_cost

ODDS_URL = f"{BASE_URL}/sports/soccer_sweden_allsvenskan/odds"
SPORTS_URL = f"{BASE_URL}/sports"

QUOTA_HEADERS = {
    "x-requests-remaining": "4821",
    "x-requests-used": "179",
    "x-requests-last": "2",
}


def test_cout_un_marche_un_bookmaker() -> None:
    assert expected_cost(["h2h"], ["betclic_fr"]) == 1


def test_cout_deux_marches_un_bookmaker() -> None:
    # Le scan de l'etage A : h2h + totals sur Betclic seul.
    assert expected_cost(["h2h", "totals"], ["betclic_fr"]) == 2


def test_cout_un_groupe_entame_de_bookmakers_compte_pour_une_region() -> None:
    dix = [f"book_{i}" for i in range(10)]
    onze = [*dix, "book_10"]

    assert expected_cost(["h2h"], dix) == 1
    assert expected_cost(["h2h"], onze) == 2


def test_cout_nul_sans_marche() -> None:
    assert expected_cost([], ["betclic_fr"]) == 0


@respx.mock
async def test_get_odds_renvoie_les_evenements_et_le_cout(
    odds_client: OddsAPIClient, load_fixture: Any
) -> None:
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    respx.get(ODDS_URL).mock(return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS))

    events, cost = await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert len(events) == 3
    assert cost == 2


@respx.mock
async def test_get_odds_persiste_le_quota(odds_client: OddsAPIClient, migrated: Settings) -> None:
    respx.get(ODDS_URL).mock(return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS))

    await odds_client.get_odds("soccer_sweden_allsvenskan")

    rows = db.query("SELECT * FROM api_usage", settings=migrated)
    assert len(rows) == 1
    assert rows[0]["provider"] == "oddsapi"
    assert rows[0]["endpoint"] == "/sports/soccer_sweden_allsvenskan/odds"
    assert rows[0]["cost"] == 2
    assert rows[0]["remaining"] == 4821
    assert last_known_quota("oddsapi", migrated) == {
        "remaining": 4821,
        "called_at": rows[0]["called_at"],
    }


@respx.mock
async def test_cout_facture_prime_sur_l_estimation(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    # Le fournisseur annonce 5 credits la ou l'estimation locale en prevoit 2.
    headers = {**QUOTA_HEADERS, "x-requests-last": "5"}
    respx.get(ODDS_URL).mock(return_value=httpx.Response(200, json=[], headers=headers))

    _, cost = await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert cost == 5
    assert db.query_one("SELECT cost FROM api_usage", settings=migrated)["cost"] == 5


@respx.mock
async def test_estimation_utilisee_sans_header_de_cout(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    respx.get(ODDS_URL).mock(
        return_value=httpx.Response(200, json=[], headers={"x-requests-remaining": "100"})
    )

    _, cost = await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert cost == 2


@respx.mock
async def test_endpoint_sports_est_gratuit(odds_client: OddsAPIClient, migrated: Settings) -> None:
    respx.get(SPORTS_URL).mock(
        return_value=httpx.Response(200, json=[{"key": "soccer_epl"}], headers=QUOTA_HEADERS)
    )

    sports = await odds_client.get_sports()

    assert sports == [{"key": "soccer_epl"}]
    # Le header annonce 2, mais /sports ne coute rien : c'est le fournisseur qui
    # fait foi, on trace donc ce qu'il facture reellement.
    assert (
        db.query_one("SELECT endpoint FROM api_usage", settings=migrated)["endpoint"] == "/sports"
    )


@respx.mock
async def test_retry_puis_succes_sur_429(odds_client: OddsAPIClient) -> None:
    route = respx.get(ODDS_URL).mock(
        side_effect=[
            httpx.Response(429, text="slow down"),
            httpx.Response(200, json=[], headers=QUOTA_HEADERS),
        ]
    )

    events, _ = await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert events == []
    assert route.call_count == 2


@respx.mock
async def test_abandon_apres_trois_tentatives(odds_client: OddsAPIClient) -> None:
    route = respx.get(ODDS_URL).mock(return_value=httpx.Response(503, text="indisponible"))

    with pytest.raises(ProviderError) as excinfo:
        await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert route.call_count == 3
    assert excinfo.value.status_code == 503


@respx.mock
async def test_pas_de_retry_sur_erreur_client(odds_client: OddsAPIClient) -> None:
    route = respx.get(ODDS_URL).mock(return_value=httpx.Response(401, text="cle invalide"))

    with pytest.raises(ProviderError):
        await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert route.call_count == 1


@respx.mock
async def test_retry_sur_timeout_reseau(odds_client: OddsAPIClient) -> None:
    route = respx.get(ODDS_URL).mock(
        side_effect=[
            httpx.ConnectTimeout("trop lent"),
            httpx.Response(200, json=[], headers=QUOTA_HEADERS),
        ]
    )

    await odds_client.get_odds("soccer_sweden_allsvenskan")

    assert route.call_count == 2


@respx.mock
async def test_cache_dev_evite_un_second_appel(
    http_client: httpx.AsyncClient, migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrated, "dev_cache", True)
    client = OddsAPIClient(http_client, migrated)
    route = respx.get(ODDS_URL).mock(
        return_value=httpx.Response(200, json=[{"id": "abc"}], headers=QUOTA_HEADERS)
    )

    first, first_cost = await client.get_odds("soccer_sweden_allsvenskan")
    second, second_cost = await client.get_odds("soccer_sweden_allsvenskan")

    assert route.call_count == 1
    assert first == second == [{"id": "abc"}]
    assert first_cost == 2
    assert second_cost == 0, "une reponse servie par le cache ne consomme aucun credit"
    assert len(db.query("SELECT * FROM api_usage", settings=migrated)) == 1


@respx.mock
async def test_la_cle_api_n_est_pas_ecrite_dans_le_cache(
    http_client: httpx.AsyncClient, migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(migrated, "dev_cache", True)
    client = OddsAPIClient(http_client, migrated)
    respx.get(ODDS_URL).mock(return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS))

    await client.get_odds("soccer_sweden_allsvenskan")

    cached = list(migrated.dev_cache_dir.rglob("*.json"))
    assert cached, "le cache dev doit avoir ecrit un fichier"
    for path in cached:
        assert "cle-odds-de-test" not in path.read_text(encoding="utf-8")
        assert "cle-odds-de-test" not in path.name


@respx.mock
async def test_la_cle_api_est_envoyee_en_parametre(odds_client: OddsAPIClient) -> None:
    route = respx.get(ODDS_URL).mock(return_value=httpx.Response(200, json=[]))

    await odds_client.get_odds("soccer_sweden_allsvenskan")

    request = route.calls[0].request
    assert request.url.params["apiKey"] == "cle-odds-de-test"
    assert request.url.params["bookmakers"] == "betclic_fr"
    assert request.url.params["markets"] == "h2h,totals"
    assert request.url.params["oddsFormat"] == "decimal"


def test_url_de_production() -> None:
    # Verrou contre une URL de test laissee en place par erreur : les mocks
    # respx suivent BASE_URL, ils ne detecteraient pas la substitution.
    assert BASE_URL == "https://api.the-odds-api.com/v4"
