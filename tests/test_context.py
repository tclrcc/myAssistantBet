from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.apifootball import BASE_URL, APIFootballClient
from myassistantbet.providers.base import ProviderError
from myassistantbet.services.context import (
    KIND_H2H,
    KIND_INJURIES,
    KIND_MAPPING,
    context_lines,
    fetch_context,
    load,
)
from myassistantbet.services.matching import save_alias
from myassistantbet.services.render import UNAVAILABLE

RATE_HEADERS = {"x-ratelimit-requests-remaining": "82", "x-ratelimit-requests-limit": "100"}

EVENT = {
    "id": 1,
    "home": "BK Hacken",
    "away": "Djurgardens IF",
    "commence_time": "2026-08-03T15:30:00Z",
    "apifootball_league_id": 113,
}


@pytest.fixture
def api_client(http_client: httpx.AsyncClient, migrated: Settings) -> APIFootballClient:
    return APIFootballClient(http_client, migrated)


def _seed_event(settings: Settings) -> None:
    """Insere l'evenement de reference, rattache a l'Allsvenskan."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (1, ?, ?, 'evt-1', ?, ?, ?, 'api', ?)",
        (
            competition["sport_id"],
            competition["id"],
            EVENT["home"],
            EVENT["away"],
            EVENT["commence_time"],
            db.utcnow(),
        ),
        settings=settings,
    )


def _mock_all(load_fixture: Any) -> None:
    """Repond a tous les endpoints API-Football avec les fixtures capturees."""
    respx.get(f"{BASE_URL}/fixtures", params__contains={"date": "2026-08-03"}).mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_fixtures_date.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/standings").mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_standings.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/teams/statistics", params__contains={"team": "376"}).mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_stats_home.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/teams/statistics", params__contains={"team": "377"}).mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_stats_away.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/fixtures", params__contains={"team": "376"}).mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_recent_home.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/fixtures", params__contains={"team": "377"}).mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_recent_away.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_injuries.json"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/fixtures/headtohead").mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_h2h.json"), headers=RATE_HEADERS
        )
    )
    # Ajoute en dernier a dessein : plusieurs tests designent une route par son
    # index (`respx.routes[1]`), qu'une insertion en tete decalerait.
    respx.get(f"{BASE_URL}/leagues").mock(
        return_value=httpx.Response(
            200, json=load_fixture("apifootball_leagues.json"), headers=RATE_HEADERS
        )
    )


def _lines(settings: Settings) -> dict[str, str]:
    return dict(context_lines(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], settings))


# -- Provider ---------------------------------------------------------------


@respx.mock
async def test_cle_envoyee_en_header(api_client: APIFootballClient, migrated: Settings) -> None:
    route = respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await api_client.injuries(1)

    assert route.calls[0].request.headers["x-apisports-key"] == ""


@respx.mock
async def test_quota_persiste(api_client: APIFootballClient, migrated: Settings) -> None:
    respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await api_client.injuries(1)

    row = db.query_one("SELECT * FROM api_usage WHERE provider = 'apifootball'", settings=migrated)
    assert row["endpoint"] == "/injuries"
    assert row["cost"] == 1
    assert row["remaining"] == 82


@respx.mock
async def test_erreur_applicative_en_http_200_devient_une_erreur(
    api_client: APIFootballClient,
) -> None:
    # Piege du fournisseur : une cle invalide renvoie 200 avec `errors` rempli.
    respx.get(f"{BASE_URL}/standings").mock(
        return_value=httpx.Response(
            200, json={"errors": {"token": "Invalid API key"}, "response": []}
        )
    )

    with pytest.raises(ProviderError, match="Invalid API key"):
        await api_client.standings(113, 2026)


@respx.mock
async def test_erreurs_en_liste_aussi(api_client: APIFootballClient) -> None:
    respx.get(f"{BASE_URL}/standings").mock(
        return_value=httpx.Response(200, json={"errors": ["quota depasse"], "response": []})
    )

    with pytest.raises(ProviderError, match="quota depasse"):
        await api_client.standings(113, 2026)


# -- Mapping ----------------------------------------------------------------


@respx.mock
async def test_mapping_automatique(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)

    report = await fetch_context(api_client, EVENT, migrated)

    assert report.mapping_pending is False
    event = db.query_one("SELECT * FROM events WHERE id = 1", settings=migrated)
    assert event["apifootball_fixture_id"] == 1122334
    assert event["mapping_pending"] == 0


@respx.mock
async def test_mapping_incertain_ne_declenche_aucun_autre_appel(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)
    inconnu = {**EVENT, "home": "Racing Club de Nulle Part"}
    standings = respx.routes[1]

    report = await fetch_context(api_client, inconnu, migrated)

    assert report.mapping_pending is True
    assert standings.call_count == 0, "aucun appel supplementaire tant que le mapping est incertain"
    event = db.query_one("SELECT mapping_pending FROM events WHERE id = 1", settings=migrated)
    assert event["mapping_pending"] == 1


@respx.mock
async def test_candidats_memorises_pour_la_resolution_manuelle(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, {**EVENT, "home": "Racing Club de Nulle Part"}, migrated)

    payload = load(1, migrated)[KIND_MAPPING]
    unresolved = [team for team in payload["teams"] if not team["resolved"]]
    assert [team["oddsapi_name"] for team in unresolved] == ["Racing Club de Nulle Part"]
    assert unresolved[0]["candidates"], "les candidats sont proposes a l'utilisateur"


@respx.mock
async def test_alias_manuel_debloque_le_mapping(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)
    save_alias("Racing Club de Nulle Part", 376, "BK Hacken", "manual", migrated)

    report = await fetch_context(
        api_client, {**EVENT, "home": "Racing Club de Nulle Part"}, migrated
    )

    assert report.mapping_pending is False


# -- Contexte rendu ---------------------------------------------------------


@respx.mock
async def test_bloc_contexte_complet(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Critere d'acceptation : forme, classement, absents et H2H sont presents."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)
    lines = _lines(migrated)

    assert lines["Classement"] == "BK Hacken 4e (34pts, 16j) | Djurgardens IF 2e (39pts, 16j)"
    assert lines["Forme 5"] == "BK Hacken VVNDV (9-4) | Djurgardens IF VVVND (11-3)"
    assert lines["Dom/Ext"] == (
        "BK Hacken dom 6V-1N-1D 2.1 bpm | Djurgardens IF ext 4V-2N-2D 1.4 bpm"
    )
    assert lines["H2H (3)"] == "1-1 · 0-2 D · 2-2"
    assert "Rygaard" in lines["Absents"]
    assert "Djurgardens IF — aucun signale" in lines["Absents"]
    assert lines["Repos"] == "BK Hacken 6j | Djurgardens IF 3j"


@respx.mock
async def test_lettres_de_forme_traduites(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """W->V, D->N (nul), L->D (defaite) : le piege classique."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert "VVNDV" in _lines(migrated)["Forme 5"]


@respx.mock
async def test_h2h_toujours_du_point_de_vue_de_l_equipe_a_domicile(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)
    payload = load(1, migrated)[KIND_H2H]

    # Le match du 21/09 s'est joue chez Djurgarden (2-0) : rendu 0-2 D pour Hacken.
    assert payload["home_id"] == 376
    assert _lines(migrated)["H2H (3)"].split(" · ")[1] == "0-2 D"


@respx.mock
async def test_absents_non_couverts_sont_explicites(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Critere d'acceptation : ce qui manque est dit, jamais tu."""
    _seed_event(migrated)
    _mock_all(load_fixture)
    respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(200, json={"errors": ["not covered"], "response": []})
    )

    report = await fetch_context(api_client, EVENT, migrated)

    assert load(1, migrated)[KIND_INJURIES] == {"available": False}
    assert _lines(migrated)["Absents"] == UNAVAILABLE
    assert any("absents" in error for error in report.errors)


@respx.mock
async def test_classement_indisponible_omet_la_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)
    respx.get(f"{BASE_URL}/standings").mock(return_value=httpx.Response(503, text="HS"))

    await fetch_context(api_client, EVENT, migrated)

    assert "Classement" not in _lines(migrated)
    assert "Forme 5" in _lines(migrated), "les autres donnees restent recuperees"


@respx.mock
async def test_contexte_relu_sans_reseau(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)
    await fetch_context(api_client, EVENT, migrated)
    appels = sum(route.call_count for route in respx.routes)

    # Rejouer le rendu ne doit toucher a aucune API.
    _lines(migrated)
    _lines(migrated)

    assert sum(route.call_count for route in respx.routes) == appels


@respx.mock
async def test_cache_partage_entre_matchs_de_la_meme_ligue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) SELECT 2, sport_id, competition_id, 'evt-2', "
        "home, away, commence_time, 'api', created_at FROM events WHERE id = 1",
        settings=migrated,
    )
    _mock_all(load_fixture)
    standings = respx.routes[1]
    cache: dict[str, object] = {}

    await fetch_context(api_client, EVENT, migrated, cache)
    await fetch_context(api_client, {**EVENT, "id": 2}, migrated, cache)

    assert standings.call_count == 1, "le classement n'est paye qu'une fois par ligue"


def test_aucun_contexte_ne_produit_aucune_ligne(migrated: Settings) -> None:
    assert context_lines(1, "A", "B", "2026-08-03T15:30:00Z", migrated) == []


def test_url_de_production() -> None:
    # Meme verrou que pour The Odds API : les mocks respx suivent BASE_URL et ne
    # detecteraient pas une URL de test laissee en place.
    assert BASE_URL == "https://v3.football.api-sports.io"


# -- Saison : le piege qui rendait tout le contexte muet ----------------------


@respx.mock
@pytest.mark.anyio
async def test_la_saison_accompagne_toujours_la_recherche_de_match(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sans `season`, l'API repond « The Season field is required » et le
    contexte disparaissait en silence — ce qui se lisait comme un probleme de
    rapprochement de noms alors que l'appel n'avait jamais abouti."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    recherche = next(
        call.request
        for call in respx.calls
        if call.request.url.path.endswith("/fixtures") and "date" in call.request.url.params
    )
    assert recherche.url.params["season"] == "2026"
    assert recherche.url.params["league"] == "113"


@respx.mock
@pytest.mark.anyio
async def test_la_saison_est_lue_chez_le_fournisseur_jamais_deduite_de_la_date(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Deduire la saison du mois se tromperait sur tout championnat joue en
    annee civile (MLS, Bresil, Norvege) comme sur un match de fevrier."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    assert await api_client.current_season(113) == 2026


@respx.mock
@pytest.mark.anyio
async def test_la_saison_n_est_payee_qu_une_fois_par_ligue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) SELECT 2, sport_id, competition_id, 'evt-2', "
        "home, away, commence_time, 'api', created_at FROM events WHERE id = 1",
        settings=migrated,
    )
    _mock_all(load_fixture)
    leagues = respx.routes[-1]
    cache: dict[str, object] = {}

    await fetch_context(api_client, EVENT, migrated, cache)
    await fetch_context(api_client, {**EVENT, "id": 2}, migrated, cache)

    assert leagues.call_count == 1


# -- Debit : une saturation annoncee en HTTP 200 ------------------------------


@respx.mock
@pytest.mark.anyio
async def test_une_saturation_de_debit_est_retentee(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """API-Football annonce ses depassements de debit en HTTP 200, comme ses
    erreurs applicatives : `RETRY_STATUSES` ne les voit pas. Sans ce crochet,
    une seconde d'attente manquante laissait un trou definitif dans le contexte
    d'une session — verifie en reel sur une rafale d'enrichissement."""
    sature = httpx.Response(
        200,
        json={"errors": {"rateLimit": "Too many requests. You have exceeded the limit."}},
        headers=RATE_HEADERS,
    )
    servi = httpx.Response(
        200, json={"errors": [], "response": [{"ok": True}]}, headers=RATE_HEADERS
    )
    route = respx.get(f"{BASE_URL}/injuries").mock(side_effect=[sature, servi])
    client = APIFootballClient(http_client, migrated, backoff_base=0)
    client.payload_retry_delay = 0

    assert await client.injuries(1) == [{"ok": True}]
    assert route.call_count == 2


@respx.mock
@pytest.mark.anyio
async def test_une_cle_invalide_echoue_sans_insister(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """La distinction est tout l'interet : un debit depasse se retente, une cle
    invalide non — insister ne la rendrait pas valide et brulerait du quota."""
    route = respx.get(f"{BASE_URL}/standings").mock(
        return_value=httpx.Response(
            200, json={"errors": {"token": "Invalid API key"}, "response": []}
        )
    )
    client = APIFootballClient(http_client, migrated, backoff_base=0)

    with pytest.raises(ProviderError, match="Invalid API key"):
        await client.standings(113, 2026)
    assert route.call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_une_saturation_persistante_finit_par_echouer_visiblement(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Retenter n'est pas taire : apres les tentatives, l'erreur remonte."""
    respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(
            200, json={"errors": ["rateLimit: Too many requests."]}, headers=RATE_HEADERS
        )
    )
    client = APIFootballClient(http_client, migrated, backoff_base=0)
    client.payload_retry_delay = 0

    with pytest.raises(ProviderError, match="debit depasse"):
        await client.injuries(1)


@respx.mock
@pytest.mark.anyio
async def test_une_reponse_saturee_n_est_jamais_mise_en_cache(
    http_client: httpx.AsyncClient, migrated: Settings, tmp_path: Any
) -> None:
    """Une saturation n'est pas une reponse : la cacher ferait servir l'erreur
    a toutes les generations suivantes, sans plus jamais appeler l'API."""
    migrated.dev_cache = True
    migrated.dev_cache_dir = tmp_path / "cache"
    respx.get(f"{BASE_URL}/injuries").mock(
        side_effect=[
            httpx.Response(200, json={"errors": {"rateLimit": "Too many requests."}}),
            httpx.Response(200, json={"errors": [], "response": [{"ok": True}]}),
        ]
    )
    client = APIFootballClient(http_client, migrated, backoff_base=0)
    client.payload_retry_delay = 0

    await client.injuries(1)

    fichiers = (
        list(migrated.dev_cache_dir.rglob("*.json")) if migrated.dev_cache_dir.exists() else []
    )
    assert len(fichiers) == 1, "seule la reponse servie est cachee"


# -- Formes d'enveloppe : liste ou objet selon l'endpoint ---------------------


@respx.mock
@pytest.mark.anyio
async def test_les_statistiques_d_equipe_arrivent_en_objet_pas_en_liste(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`/teams/statistics` met un objet dans `response`. Le lire comme une liste
    renvoyait toujours `None` : les lignes « Forme 5 » et « Dom/Ext » n'ont
    jamais ete rendues alors que leur code existait et etait teste — la fixture
    enveloppait l'objet dans un tableau, ce que le fournisseur ne fait pas."""
    _mock_all(load_fixture)

    stats = await api_client.team_statistics(113, 2026, 376)

    assert stats is not None, (
        "une forme d'enveloppe non prevue ne fait pas de bruit, elle fait un trou"
    )
    assert stats["form"] == "WLDWWWWDLW"
    assert stats["fixtures"]["wins"]["home"] == 6


@respx.mock
@pytest.mark.anyio
async def test_le_bloc_porte_la_forme_et_le_bilan_domicile_exterieur(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert "Forme 5" in lignes
    assert "Dom/Ext" in lignes
