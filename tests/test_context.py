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
    KIND_PROFILE,
    context_lines,
    fetch_context,
    load,
)
from myassistantbet.services.matching import save_alias
from myassistantbet.services.prompt import build_prompt
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


def _mock_all(load_fixture: Any) -> dict[str, respx.Route]:
    """Repond a tous les endpoints API-Football avec les fixtures capturees.

    Rend les routes **par nom** et non par position : les designer par
    `respx.routes[1]` cassait chaque test des qu'un appel etait ajoute, ce qui
    poussait a inserer les nouveaux mocks a la fin pour de mauvaises raisons.
    """

    def _mock(chemin: str, fichier: str, **selecteurs: Any) -> respx.Route:
        return respx.get(f"{BASE_URL}{chemin}", **selecteurs).mock(
            return_value=httpx.Response(200, json=load_fixture(fichier), headers=RATE_HEADERS)
        )

    return {
        "fixtures_date": _mock(
            "/fixtures",
            "apifootball_fixtures_date.json",
            params__contains={"date": "2026-08-03"},
        ),
        "standings": _mock("/standings", "apifootball_standings.json"),
        "stats_home": _mock(
            "/teams/statistics", "apifootball_stats_home.json", params__contains={"team": "376"}
        ),
        "stats_away": _mock(
            "/teams/statistics", "apifootball_stats_away.json", params__contains={"team": "377"}
        ),
        "recent_home": _mock(
            "/fixtures", "apifootball_recent_home.json", params__contains={"team": "376"}
        ),
        "recent_away": _mock(
            "/fixtures", "apifootball_recent_away.json", params__contains={"team": "377"}
        ),
        "injuries": _mock("/injuries", "apifootball_injuries.json"),
        "h2h": _mock("/fixtures/headtohead", "apifootball_h2h.json"),
        "leagues": _mock("/leagues", "apifootball_leagues.json"),
        # Le dossier d'equipe fait partie d'un enrichissement complet depuis
        # qu'il existe : sans ces deux routes, tout test qui enrichit tomberait
        # sur un appel non simule.
        "coachs_home": _mock(
            "/coachs", "apifootball_coachs_home.json", params__contains={"team": "376"}
        ),
        "coachs_away": _mock(
            "/coachs", "apifootball_coachs_away.json", params__contains={"team": "377"}
        ),
        "fixture_stats": _mock("/fixtures/statistics", "apifootball_fixture_statistics.json"),
        "team": _mock("/teams", "apifootball_team.json"),
    }


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
    routes = _mock_all(load_fixture)
    inconnu = {**EVENT, "home": "Racing Club de Nulle Part"}
    standings = routes["standings"]

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
        "BK Hacken dom 6V-1N-1D 2.1 bpm/8j | Djurgardens IF ext 4V-2N-2D 1.4 bpm/8j"
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
    routes = _mock_all(load_fixture)
    standings = routes["standings"]
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
    routes = _mock_all(load_fixture)
    leagues = routes["leagues"]
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


# -- Profil corners et cartons ------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_le_bloc_porte_le_profil_corners_cartons_et_tirs(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le prompt proposait des lignes de corners sans rien savoir de ce qu'une
    equipe en produit ou en concede : le marche etait rendu, l'angle absent."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert "Corners" in lignes
    assert "Cartons" in lignes
    assert "Tirs" in lignes


@respx.mock
@pytest.mark.anyio
async def test_le_profil_donne_le_concede_par_l_adversaire_du_meme_match(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un seul appel par rencontre rend les deux equipes : les corners concedes
    par l'une sont ceux tires par l'autre, sans appel supplementaire."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    profil = load(1, migrated)[KIND_PROFILE]["home"]
    assert profil["corners"] == 8.0, "corners tires par l'equipe a domicile"
    assert profil["corners_against"] == 6.0, "ceux de l'adversaire, dans le meme match"
    assert profil["yellow"] == 0.0
    assert profil["shots"] == 24.0


@respx.mock
@pytest.mark.anyio
async def test_la_moyenne_porte_le_nombre_de_matchs(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« 6.0 corners » sur deux matchs et sur cinq ne disent pas la meme chose :
    le compte accompagne la moyenne, comme il accompagne un taux."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert "/" in _lines(migrated)["Corners"]


@respx.mock
@pytest.mark.anyio
async def test_un_match_sans_statistiques_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Couverture irreguliere selon les competitions : une ligne sans donnee est
    omise, jamais rendue a zero — « 0.0 corners » serait une affirmation fausse."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["fixture_stats"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert "Corners" not in lignes
    assert "Cartons" not in lignes
    assert "Classement" in lignes, "le reste du bloc n'est pas affecte"


@respx.mock
@pytest.mark.anyio
async def test_une_rencontre_partagee_n_est_payee_qu_une_fois(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Memorisation par match et non par equipe : deux adversaires qui se sont
    croises recemment partagent la rencontre."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    appels = {call.request.url.params["fixture"] for call in routes["fixture_stats"].calls}
    assert routes["fixture_stats"].call_count == len(appels), "aucun match interroge deux fois"


@respx.mock
@pytest.mark.anyio
async def test_une_moyenne_sur_trop_peu_de_matchs_n_est_pas_publiee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """En debut de saison, un seul des cinq derniers matchs revient renseigne.
    « 2.0 corners pris 9.0 » sur une rencontre se lit comme une tendance alors
    que c'est une soiree — meme raison que le seuil du retour d'experience."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    servi = load_fixture("apifootball_fixture_statistics.json")
    vide = {"errors": [], "response": []}
    appels = {"n": 0}

    def _un_seul_match(request: httpx.Request) -> httpx.Response:
        appels["n"] += 1
        return httpx.Response(200, json=servi if appels["n"] == 1 else vide, headers=RATE_HEADERS)

    routes["fixture_stats"].mock(side_effect=_un_seul_match)

    await fetch_context(api_client, EVENT, migrated)

    profil = load(1, migrated)[KIND_PROFILE]["home"]
    assert profil["matches"] == 1, "la donnee est bien collectee et persistee"
    assert "Corners" not in _lines(migrated), "mais elle n'est pas publiee sous le seuil"


@respx.mock
@pytest.mark.anyio
async def test_un_absent_annonce_deux_fois_n_est_liste_qu_une_fois(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Constate en reel : le fournisseur rend 14 lignes pour 7 absents. Sans
    dedoublonnage la ligne liste tout le monde en double, ce qui fait douter de
    la donnee entiere."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    servi = load_fixture("apifootball_injuries.json")
    double = {**servi, "response": list(servi["response"]) + list(servi["response"])}
    routes["injuries"].mock(return_value=httpx.Response(200, json=double, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    absents = load(1, migrated)[KIND_INJURIES]
    noms = [entry["name"] for entry in absents["home"] + absents["away"]]
    assert len(noms) == len(set(noms)), f"chaque absent une seule fois : {noms}"


# -- Ce que le fournisseur declare ne pas couvrir -----------------------------

#: Couverture d'une competition ou le fournisseur n'a ni classement ni absents.
#: Constate en reel sur la Conference League 2026.
LEAGUES_SANS_COUVERTURE = {
    "errors": [],
    "response": [
        {
            "league": {"id": 113, "name": "Allsvenskan", "type": "League"},
            "seasons": [
                {
                    "year": 2026,
                    "current": True,
                    "coverage": {"standings": False, "injuries": False},
                }
            ],
        }
    ],
}


@respx.mock
@pytest.mark.anyio
async def test_une_donnee_non_couverte_est_dite_et_non_affirmee_absente(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le piege repare : une liste d'absents vide se rendait « aucun signale »,
    soit l'affirmation inverse de la verite. Constate en reel sur les qualifs
    europeennes, ou six absents annonces par la presse etaient nies par le bloc."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COUVERTURE, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert lignes["Absents"] == UNAVAILABLE
    assert "aucun signale" not in lignes["Absents"]
    assert lignes["Classement"] == UNAVAILABLE, "une absence declaree se dit, elle ne s'omet pas"


@respx.mock
@pytest.mark.anyio
async def test_une_donnee_non_couverte_n_est_pas_appelee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Rien n'a echoue : il n'y a rien a chercher. Autant ne pas depenser
    l'appel — le quota par minute est la vraie contrainte sur une grosse soiree."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COUVERTURE, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert routes["injuries"].call_count == 0
    assert routes["standings"].call_count == 0


@respx.mock
@pytest.mark.anyio
async def test_une_couverture_absente_de_la_reponse_ne_bloque_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le fournisseur peut omettre le champ : on suppose alors couvert plutot
    que de faire disparaitre des donnees qui arrivaient hier."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    report = await fetch_context(api_client, EVENT, migrated)

    assert KIND_INJURIES in report.kinds
    assert "aucun signale" in _lines(migrated)["Absents"]


# -- Dom/Ext : ne rien affirmer sur zero match --------------------------------


@respx.mock
@pytest.mark.anyio
async def test_aucun_bilan_domicile_exterieur_sans_match_joue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le fournisseur repond `0V-0N-0D` et `0.0` de moyenne quand rien n'a ete
    joue : indiscernable d'une equipe qui ne gagne ni ne marque. La ligne
    apparaissait neuf fois dans un prompt de vingt-sept matchs."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    vierge = load_fixture("apifootball_stats_home.json")
    vierge["response"] = {
        **vierge["response"],
        "fixtures": {"played": {"home": 0, "away": 0, "total": 0}},
        "goals": {"for": {"average": {"home": "0.0", "away": "0.0", "total": "0.0"}}},
    }
    routes["stats_home"].mock(return_value=httpx.Response(200, json=vierge, headers=RATE_HEADERS))
    routes["stats_away"].mock(return_value=httpx.Response(200, json=vierge, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "Dom/Ext" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_le_bilan_porte_son_nombre_de_matchs(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« 0.0 bpm » sur deux matchs et sur vingt ne disent pas la meme chose, et
    la statistique porte sur cette competition, pas sur toute la saison."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert "/8j" in _lines(migrated)["Dom/Ext"]


# -- Lieu et pelouse ----------------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_une_pelouse_synthetique_est_dite(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Elle change le rythme d'un match, et rien dans le bloc ne la laissait
    deviner : il a fallu une source de niveau 4 pour l'apprendre sur Zalgiris."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Pelouse"] == "synthetique"


@respx.mock
@pytest.mark.anyio
async def test_une_pelouse_naturelle_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """C'est le cas ordinaire : l'ecrire couterait des tokens pour rien."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    equipe = load_fixture("apifootball_team.json")
    equipe["response"][0]["venue"] = {**equipe["response"][0]["venue"], "surface": "grass"}
    routes["team"].mock(return_value=httpx.Response(200, json=equipe, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "Pelouse" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_un_match_delocalise_le_dit(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Quatre « domiciles » d'une soiree de qualifications se jouaient ailleurs
    — Kyiv a Lublin, Beitar a Ploiesti — sans que le bloc en dise un mot."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": None,
        "name": "Arena Lublin",
        "city": "Lublin",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Lieu"] == "Arena Lublin, Lublin — hors de Goteborg"


@respx.mock
@pytest.mark.anyio
async def test_un_match_a_domicile_ne_produit_aucune_ligne_de_lieu(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le lieu n'est rendu que s'il surprend : « joue chez lui » sous chaque
    affiche couterait des tokens pour ne rien apprendre."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {"id": None, "name": "Bravida", "city": "Goteborg"}
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert "Lieu" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_une_ville_inconnue_n_invente_pas_de_delocalisation(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """On ne remplace pas une inconnue par une supposition."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    equipe = load_fixture("apifootball_team.json")
    equipe["response"][0]["venue"] = {**equipe["response"][0]["venue"], "city": None}
    routes["team"].mock(return_value=httpx.Response(200, json=equipe, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "Lieu" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_un_meme_stade_sous_deux_noms_de_ville_n_est_pas_une_delocalisation(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Constate en reel : `Veritas Stadion / Turku` contre `Veritas Stadion /
    Åbo`, et `Stadion Partizana / Belgrade` contre `… / Beograd`. Deux noms de
    la meme ville — la comparaison sur la ville seule inventait un match
    delocalise sur deux des dix qu'elle signalait."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": None,
        "name": "Bravida Arena",
        "city": "Gothenburg",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert "Lieu" not in _lines(migrated), "meme stade : la ville s'ecrit comme elle veut"


@respx.mock
@pytest.mark.anyio
async def test_un_nom_de_stade_proche_ne_masque_pas_une_delocalisation(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """L'autre moitie du piege : le fournisseur a garde « Teddy Stadium » pour
    un Beitar Jerusalem joue a Ploiesti. Le nom seul aurait laisse passer."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": None,
        "name": "Bravida Stadium",
        "city": "Ploiesti",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Lieu"] == "Bravida Stadium, Ploiesti — hors de Goteborg"


# -- Statistiques de saison : deja payees, longtemps jetees --------------------


@respx.mock
@pytest.mark.anyio
async def test_les_lignes_de_saison_ne_coutent_aucun_appel_de_plus(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`/teams/statistics` etait deja appele et sa charge utile persistee
    entiere : seuls `form` et le bilan dom/ext en etaient tires. Le reste — buts
    par match, clean sheets, tranches horaires, formations — dormait en base
    alors que les marches correspondants etaient achetes."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert lignes["Buts marq."] == (
        "BK Hacken >0.5 14/16 >1.5 9/16 >2.5 4/16 | Djurgardens IF >0.5 15/16 >1.5 8/16 >2.5 4/16"
    )
    assert lignes["Clean sheet"] == (
        "BK Hacken 5 CS, 2 sans marquer/16 | Djurgardens IF 4 CS, 1 sans marquer/16"
    )
    assert lignes["1re MT"] == (
        "BK Hacken 12/28 marq. 8/20 pris | Djurgardens IF 15/28 marq. 11/20 pris"
    )
    assert routes["stats_home"].call_count == 1, "un seul appel sert la forme et ces lignes"
    assert routes["stats_away"].call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_une_frequence_de_saison_ne_devient_jamais_un_pourcentage(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Garde-fou de la section 9. Une frequence observee decrit le passe, ce qui
    reste permis ; ecrite « 56 % », elle invite a la diviser par une cote, et
    c'est le calcul d'esperance interdit. La fraction porte la meme information
    avec son compte, comme « 5.2 pris 6.4/5 » pour les corners."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    for label in ("Buts marq.", "Clean sheet", "1re MT", "Cartons tps"):
        assert "%" not in lignes[label], f"la ligne « {label} » ne doit porter aucun pourcentage"
        assert "/" in lignes[label], f"la ligne « {label} » doit porter son denominateur"


@respx.mock
@pytest.mark.anyio
async def test_un_carton_sans_minute_compte_au_total_mais_a_aucune_mi_temps(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Piege verifie sur charge utile reelle : `cards.yellow` porte une tranche
    de libelle **vide** — un carton dont la minute est inconnue. L'omettre du
    denominateur ferait passer 19 cartons tardifs sur 34 pour 19 sur 33, donc
    surestimerait la part des cartons tardifs."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    # 2+3+4+5+7+9+3 tranches connues, plus 1 de minute inconnue.
    assert _lines(migrated)["Cartons tps"] == (
        "BK Hacken 19/34 apres 60e | Djurgardens IF 11/20 apres 60e"
    )


@respx.mock
@pytest.mark.anyio
async def test_le_compte_des_formations_distingue_une_equipe_stable_d_un_effectif_tournant(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sans le compte, « 4-3-3 » se lirait comme la formation habituelle alors
    qu'elle peut ne couvrir que quatre matchs sur seize : c'est alors un
    effectif tournant, soit l'information inverse."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Formations"] == (
        "BK Hacken 4-2-3-1 (11), 4-3-3 (5)/16 | Djurgardens IF 4-3-3 (4), 4-4-2 (4)/16"
    )


@respx.mock
@pytest.mark.anyio
async def test_aucune_ligne_de_saison_sans_match_joue_dans_la_competition(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le cas reel des qualifications europeennes : le fournisseur repond des
    zeros partout pour une equipe qui n'a encore rien joue dans la competition.
    « >0.5 0/0 » et « 0 CS/0 » ne decrivent personne."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    vierge = load_fixture("apifootball_stats_home.json")
    vierge["response"]["fixtures"]["played"] = {"home": 0, "away": 0, "total": 0}
    routes["stats_home"].mock(return_value=httpx.Response(200, json=vierge, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    for label in ("Buts marq.", "Clean sheet", "1re MT", "Formations", "Cartons tps"):
        assert "BK Hacken" not in lignes.get(label, ""), (
            f"la ligne « {label} » ne doit rien affirmer sur une equipe sans match joue"
        )


@respx.mock
@pytest.mark.anyio
async def test_aucune_ligne_de_saison_sous_le_seuil_de_matchs(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Meme raison que le seuil du profil et celui du retour d'experience :
    « >1.5 dans 3/4 » se lit comme une tendance alors que c'est un mois d'aout."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    debut = load_fixture("apifootball_stats_home.json")
    debut["response"]["fixtures"]["played"] = {"home": 2, "away": 2, "total": 4}
    routes["stats_home"].mock(return_value=httpx.Response(200, json=debut, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "BK Hacken" not in _lines(migrated).get("Buts marq.", "")
    assert "Djurgardens IF" in _lines(migrated)["Buts marq."], "l'autre equipe garde la sienne"


# -- Statistiques de match non couvertes : ne pas payer pour du vide ----------

#: Couverture d'une competition ou le fournisseur ne sert pas les statistiques
#: de match. Constate en reel sur la Primeira Liga 2026. Le drapeau vit dans un
#: **sous-objet**, la ou `standings` et `injuries` sont a la racine.
LEAGUES_SANS_STATS_DE_MATCH = {
    "errors": [],
    "response": [
        {
            "league": {"id": 113, "name": "Allsvenskan", "type": "League"},
            "seasons": [
                {
                    "year": 2026,
                    "current": True,
                    "coverage": {
                        "fixtures": {"events": True, "statistics_fixtures": False},
                        "standings": True,
                        "injuries": True,
                    },
                }
            ],
        }
    ],
}


@respx.mock
@pytest.mark.anyio
async def test_des_statistiques_de_match_non_couvertes_ne_sont_pas_appelees(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Jusqu'a dix appels par match etaient payes pour rien : la Primeira Liga
    2026 annonce `statistics_fixtures: false`, chaque `/fixtures/statistics`
    revient vide, et les trois lignes du profil disparaissaient sans un mot."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_STATS_DE_MATCH, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert routes["fixture_stats"].call_count == 0
    lignes = _lines(migrated)
    assert lignes["Stats match"] == UNAVAILABLE
    assert "Corners" not in lignes, "une absence declaree se dit une fois, pas trois"
    assert "Tirs" not in lignes


@respx.mock
@pytest.mark.anyio
async def test_une_couverture_de_statistiques_absente_du_sous_objet_ne_bloque_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Meme regle qu'a la racine : un champ que le fournisseur omet vaut
    couvert, sinon on ferait disparaitre des donnees qui arrivaient hier."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    sans_sous_objet = {
        "errors": [],
        "response": [
            {
                "league": {"id": 113, "name": "Allsvenskan", "type": "League"},
                "seasons": [{"year": 2026, "current": True, "coverage": {"standings": True}}],
            }
        ],
    }
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=sans_sous_objet, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert routes["fixture_stats"].call_count > 0
    assert "Corners" in _lines(migrated)


def test_le_prompt_interdit_de_rapprocher_une_frequence_d_une_cote(migrated: Settings) -> None:
    """Le garde-fou compte autant que la donnee (SPEC.md section 9). Une
    frequence passee rapprochee d'une cote est un calcul d'esperance, et le fait
    qu'elle vienne d'un releve reel n'y change rien — meme regle que pour l'Elo
    et pour le retour d'experience."""
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )

    body = build_prompt(1, settings=migrated).body

    assert "Buts marq." in body
    assert "jamais** : les traiter comme des probabilités" in body
    assert "espérance" in body
    assert "les siens uniquement" in body, "le sens de la ligne doit etre sans ambiguite"
