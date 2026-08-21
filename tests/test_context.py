from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.apifootball import BASE_URL, APIFootballClient
from myassistantbet.providers.base import ProviderError
from myassistantbet.providers.weather import GEOCODING_URL, WeatherClient
from myassistantbet.services.context import (
    CAUSE_BLOCK_NOTES,
    CAUSE_LABELS,
    CAUSE_NOT_COVERED,
    CAUSE_PROVIDER_EMPTY,
    CAUSE_SERVED,
    CAUSE_UI_NOTES,
    CAUSE_UNMAPPED,
    CAUSE_UNREACHABLE,
    CAUSE_UNRESOLVED,
    COLLECTION_FAULTS,
    DOMESTIC_RESOLVED,
    KIND_DOMESTIC,
    KIND_FORM,
    KIND_H2H,
    KIND_INJURIES,
    KIND_MAPPING,
    KIND_PROFILE,
    KIND_RECENT,
    KIND_REFEREE,
    KIND_STANDINGS,
    KIND_TEAMS,
    REFEREE_MIN_SAMPLE,
    SHEETS_LAST,
    UNRESOLVED_FORMS,
    context_lines,
    failure_causes,
    fetch_context,
    load,
    refresh_due_lineups,
    store,
)
from myassistantbet.services.matching import save_alias
from myassistantbet.services.prompt import build_prompt
from myassistantbet.services.render import UNAVAILABLE
from myassistantbet.services.thresholds import save as save_threshold

from .helpers import RATE_HEADERS, mock_context_routes

EVENT = {
    "id": 1,
    "home": "BK Hacken",
    "away": "Djurgardens IF",
    "commence_time": "2026-08-03T15:30:00Z",
    "apifootball_league_id": 113,
}


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
    """Les routes du bloc CONTEXTE, tenues dans `helpers` avec celles du dossier."""
    return mock_context_routes(load_fixture)


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
    # Le compte suit les buts : les lettres portent sur la competition, les buts
    # sur les cinq derniers matchs toutes competitions.
    assert lines["Forme 5"] == "BK Hacken VVNDV (5j) 9-4/5 | Djurgardens IF VVVND (5j) 11-3/5"
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

    # Injoignable, et non « non couvert » : la premiere se retente au prochain
    # enrichissement, la seconde ne se retentera jamais.
    assert load(1, migrated)[KIND_INJURIES] == {
        "available": False,
        "state": "unreachable",
    }
    assert _lines(migrated)["Absents"] == (
        "source injoignable au dernier releve — a retenter ou a chercher"
    )
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
async def test_un_quota_journalier_epuise_n_est_jamais_retente(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """**Les deux plafonds arrivent par le meme chemin, un seul retombe.**

    Le debit par minute se libere en quelques secondes et vaut un backoff ; le
    quota journalier ne se libere qu'a minuit. Le retenter brule trois unites
    par appel exactement quand il n'en reste plus, et recommence pour chaque
    appel restant de l'enrichissement.

    Le declencheur n'a pas ete observe en direct : la cle du quota journalier
    est `requests` et non `ratelimit`, donc elle sort deja par le chemin des
    erreurs definitives. Ce qui est fragile est le repli sur le **message** —
    le jour ou le fournisseur formule son plafond journalier avec les mots
    « too many requests », il basculerait dans le retry sans qu'aucun test ne
    bronche. C'est ce jour-la que ce test sert.
    """
    route = respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(
            200,
            json={"errors": {"requests": "Too many requests for the day. Your plan allows 7500."}},
            headers=RATE_HEADERS,
        )
    )
    client = APIFootballClient(http_client, migrated, backoff_base=0)
    client.payload_retry_delay = 0

    with pytest.raises(ProviderError):
        await client.injuries(1)

    assert route.call_count == 1, "une seule tentative : rien ne retombera avant minuit"


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
    assert lignes["Absents"] == (
        "non interroges — le fournisseur ne couvre pas les absents sur cette "
        "competition, la recherche est le seul chemin"
    )
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
async def test_un_match_hors_du_pays_du_receveur_est_un_terrain_neutre(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Dinamo Minsk « recevait » au Stadion Beroe en Bulgarie, ML Vitebsk a
    Mezokovesd en Hongrie, Hapoel Tel-Aviv a Miskolc : trois « domiciles » qui
    n'en sont pas, et le bloc n'en disait rien.

    Neutre veut dire **hors du pays du club**, pas seulement hors de son stade :
    c'est la difference entre une contrainte logistique et une contrainte
    politique ou securitaire, et seule la seconde change la lecture."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": 999,
        "name": "Arena Lublin",
        "city": "Lublin",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Lieu"] == (
        "Arena Lublin, Lublin (POL) — TERRAIN NEUTRE, BK Hacken recoit hors de son pays"
    )


@respx.mock
@pytest.mark.anyio
async def test_un_stade_different_dans_le_meme_pays_n_est_pas_neutre(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Travaux, sanction, capacite : un club deplace dans son pays reste chez
    lui, **le public suit**. Le confondre avec un exil ferait chercher un
    facteur qui n'existe pas."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {"id": 999, "name": "Ullevi", "city": "Goteborg"}
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )
    stade = load_fixture("apifootball_venue.json")
    stade["response"][0]["country"] = "Sweden"
    routes["venue"].mock(return_value=httpx.Response(200, json=stade, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    ligne = _lines(migrated)["Lieu"]
    assert "TERRAIN NEUTRE" not in ligne
    assert ligne == "Ullevi, Goteborg (SWE)"


@respx.mock
@pytest.mark.anyio
async def test_le_meme_stade_sous_deux_orthographes_n_est_pas_une_delocalisation(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le faux positif qui a fait ignorer la ligne.** « Parken Stadium,
    Copenhagen — hors de København » annoncait une delocalisation entre deux
    orthographes de la meme ville, et le bruit a fini par rendre la ligne
    inutile — l'inverse de son but.

    La comparaison porte desormais sur les **identifiants** : le `venue` d'un
    match en a un, et le commentaire qui disait le contraire datait d'une
    lecture trop rapide de la charge utile."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    # Meme stade que celui de l'equipe, sous un autre nom de ville.
    matchs["response"][0]["fixture"]["venue"] = {
        "id": 1234,
        "name": "Bravida Arena",
        "city": "Gothenburg",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    ligne = _lines(migrated)["Lieu"]
    assert "TERRAIN NEUTRE" not in ligne, "meme identifiant : la ville s'ecrit comme elle veut"
    assert ligne == "Bravida Arena, Gothenburg (SWE)"


@respx.mock
@pytest.mark.anyio
async def test_le_pays_du_stade_ne_se_demande_pas_sur_un_match_a_domicile(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le stade habituel n'a pas besoin d'etre situe : on sait deja que le club y
    est chez lui, et l'appel couterait une requete par match."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": 1234,
        "name": "Bravida Arena",
        "city": "Goteborg",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert not routes["venue"].called


@respx.mock
@pytest.mark.anyio
async def test_un_stade_sans_identifiant_est_quand_meme_nomme(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Mesure du 12/08/2026** : `fixture.venue.id` est nul sur 210 matchs sur
    210 d'une saison de Conference League, et servi sur 380 sur 380 d'une saison
    de Premier League. Le drapeau de terrain neutre est donc structurellement
    muet sur les competitions UEFA — exactement la ou les delocalisations
    arrivent.

    Rendre « donnees non disponibles » y jetait le nom du stade et sa ville, que
    le fournisseur sert pourtant. Or c'est cela qui fait sauter l'anomalie aux
    yeux : un club israelien qui « recoit » a Miskolc se lit sans qu'aucun
    drapeau soit calcule."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": None,
        "name": "DVTK Stadion",
        "city": "Miskolc",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Lieu"] == (
        "DVTK Stadion, Miskolc — pas d'identifiant de stade ici, terrain neutre non verifiable"
    )


@respx.mock
@pytest.mark.anyio
async def test_une_competition_sans_ligue_ne_declenche_aucun_appel(
    api_client: APIFootballClient, migrated: Settings
) -> None:
    """**Un credit depense pour un message qui decrit le fournisseur.** Sans
    identifiant de ligue, `/leagues` part avec un `id` vide et rend une erreur
    applicative en HTTP 200 : « id: The Id field cannot be empty ». La fiche d'un
    match affichait cela tel quel, la ou la cause se corrige en une saisie.

    Vu sur une nuit de Leagues Cup : sept matchs a zero ligne de contexte, et le
    bouton d'un match seul ne disait pas pourquoi. L'enrichissement d'une
    session, lui, se gardait deja par `context_possible` — ce chemin-la n'avait
    aucun garde-fou.

    Le test ne simule **aucune route** : le moindre appel le ferait echouer."""
    _seed_event(migrated)

    report = await fetch_context(api_client, {**EVENT, "apifootball_league_id": None}, migrated)

    assert report.kinds == []
    assert len(report.errors) == 1
    assert "non rattachee" in report.errors[0]
    assert "/competitions" in report.errors[0], "la ligne doit dire ou se corrige la cause"


def _geo(candidats: list[dict[str, Any]]) -> respx.Route:
    """Le geocodeur Open-Meteo : gratuit, sans cle, et sans quota."""
    return respx.get(f"{GEOCODING_URL}/v1/search").mock(
        return_value=httpx.Response(200, json={"results": candidats})
    )


def _ville_du_match(
    routes: dict[str, respx.Route], load_fixture: Any, nom: str, ville: str
) -> None:
    """Pose un stade **sans identifiant**, comme les competitions UEFA en servent."""
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {"id": None, "name": nom, "city": ville}
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )


def _pays_du_club(routes: dict[str, respx.Route], load_fixture: Any, pays: str) -> None:
    equipe = load_fixture("apifootball_team.json")
    equipe["response"][0]["team"] = {**equipe["response"][0]["team"], "country": pays}
    routes["team"].mock(return_value=httpx.Response(200, json=equipe, headers=RATE_HEADERS))


@respx.mock
@pytest.mark.anyio
async def test_le_pays_d_un_stade_sans_identifiant_vient_du_geocodage(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """**La moitie manquante du drapeau.** Le pays d'un stade ne se demande a
    API-Football qu'avec un identifiant de stade, nul sur 210 matchs sur 210
    d'une saison de Conference League — donc absent exactement la ou les
    delocalisations arrivent. Le geocodeur n'en a pas besoin, et ne coute rien.

    Un club israelien qui « recoit » a Miskolc (HUN) se lit alors sans qu'aucun
    drapeau soit calcule : c'est le partage voulu, le lieu est sur, la
    comparaison reste declaree hors de portee."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    _ville_du_match(routes, load_fixture, "DVTK Stadion", "Miskolc")
    _geo([{"name": "Miskolc", "country": "Hungary", "population": 154521}])

    await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert _lines(migrated)["Lieu"] == (
        "DVTK Stadion, Miskolc (HUN)"
        " — pas d'identifiant de stade ici, terrain neutre non verifiable"
    )


@respx.mock
@pytest.mark.anyio
async def test_un_homonyme_dans_le_pays_du_club_emporte_la_decision(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Mesure du 12/08/2026 : « Ried » compte 87 homonymes exacts, l'Allemagne
    en tete avec 2 987 habitants, et le SV Ried est **autrichien**. Le plus
    peuple des homonymes se serait trompe de pays.

    La preference ne cache aucune delocalisation : aucune des cinq connues n'a
    d'homonyme dans le pays de son club."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    _pays_du_club(routes, load_fixture, "Austria")
    _ville_du_match(routes, load_fixture, "Innviertel Arena", "Ried")
    _geo(
        [
            {"name": "Ried", "country": "Germany", "population": 2987},
            {"name": "Ried", "country": "Italy", "population": 1440},
            {"name": "Ried", "country": "The Netherlands", "population": 405},
            {"name": "Ried", "country": "Austria", "population": 371},
        ]
    )

    await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert _lines(migrated)["Lieu"].startswith("Innviertel Arena, Ried (AUT)")


@respx.mock
@pytest.mark.anyio
async def test_une_ville_trop_petite_pour_un_stade_ne_situe_rien(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """**Le faux positif que le geocodage produisait**, et il est du meme genre
    que celui qui avait fait ignorer la ligne : « Brugge » ne rend aucun
    candidat belge — Bruges y vit sous un autre nom — et le premier homonyme est
    un village allemand de 1 019 habitants.

    Dire qu'un club joue hors de chez lui est une affirmation forte : sans ville
    de taille plausible, la ligne ne dit rien plutot que de se tromper de pays."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    _pays_du_club(routes, load_fixture, "Belgium")
    _ville_du_match(routes, load_fixture, "Jan Breydel Stadion", "Brugge")
    _geo(
        [
            {"name": "Brügge", "country": "Germany", "population": 1019},
            {"name": "Brugge", "country": "Switzerland", "population": 0},
        ]
    )

    await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert _lines(migrated)["Lieu"] == (
        "Jan Breydel Stadion, Brugge"
        " — pas d'identifiant de stade ici, terrain neutre non verifiable"
    )


@respx.mock
@pytest.mark.anyio
async def test_deux_homonymes_de_meme_ordre_ne_tranchent_pas(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Deux vraies villes du meme nom dans deux pays, et le club dans aucun des
    deux : le plus peuple gagnerait a pile ou face. En cas de doute, rien — la
    regle du projet, appliquee la ou une erreur serait invisible."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    _ville_du_match(routes, load_fixture, "Estadio Municipal", "Valencia")
    _geo(
        [
            {"name": "Valencia", "country": "Venezuela", "population": 1400000},
            {"name": "Valencia", "country": "Spain", "population": 800000},
        ]
    )

    await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert "(" not in _lines(migrated)["Lieu"].split(" — ")[0]


@respx.mock
@pytest.mark.anyio
async def test_un_stade_identifie_ne_declenche_aucun_geocodage(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Le geocodage ne prend le relais que la ou l'identifiant manque. Un match
    au stade habituel est deja situe par le pays du club : appeler couterait un
    appel par match pour reapprendre ce qu'on sait."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": 1234,
        "name": "Bravida Arena",
        "city": "Goteborg",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )
    geo = _geo([{"name": "Goteborg", "country": "Sweden", "population": 579281}])

    await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert not geo.called


@respx.mock
@pytest.mark.anyio
async def test_un_geocodeur_injoignable_n_emporte_pas_le_nom_du_stade(
    api_client: APIFootballClient,
    geo_client: WeatherClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Le pays est un complement gratuit, jamais une dependance : sans lui le
    nom du stade et sa ville se lisent tres bien, et ce sont eux qui font sauter
    l'anomalie aux yeux."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    _ville_du_match(routes, load_fixture, "DVTK Stadion", "Miskolc")
    respx.get(f"{GEOCODING_URL}/v1/search").mock(return_value=httpx.Response(404))

    report = await fetch_context(api_client, EVENT, migrated, geo_client=geo_client)

    assert _lines(migrated)["Lieu"].startswith("DVTK Stadion, Miskolc —")
    assert any("pays du lieu" in erreur for erreur in report.errors)


@respx.mock
@pytest.mark.anyio
async def test_sans_lieu_du_tout_la_ligne_se_declare_inconnue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Quatrieme etat, et il compte autant que les autres : un domicile
    **suppose** qui n'en est pas serait pire qu'un « non renseigne » franc.

    C'est la meme regle que le fuseau du lieu — declarer ce qu'on n'a pas vaut
    mieux que promouvoir une approximation."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {"id": None, "name": None, "city": None}
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Lieu"] == "donnees non disponibles pour cette competition"


@respx.mock
@pytest.mark.anyio
async def test_une_moitie_de_lieu_absente_ne_se_comble_pas_avec_le_stade_habituel(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le bloc n'omettait pas une qualification, il inventait un lieu.**

    Mesure du 13/08/2026 : le fournisseur ne servait aucun nom de stade pour
    KI Klaksvik, seulement la ville. Chaque moitie se repliant de son cote sur le
    stade habituel, le bloc a rendu `Injector Arena, Torshavn` — Injector Arena
    est le terrain de KI, a Klaksvik, quand la rencontre se jouait au stade
    national de Torshavn. Et la mention qui suivait, « terrain neutre non
    verifiable », invitait a lire l'anomalie sans dire qu'elle avait ete
    composee : une omission se signale, un fait fabrique se cite.
    """
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {"id": None, "name": None, "city": "Torshavn"}
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)
    ligne = _lines(migrated)["Lieu"]

    assert ligne.startswith("Torshavn — stade non precise")
    # Le stade habituel reste rendu, mais **derriere son propre libelle** :
    # c'est le seul endroit ou il peut paraitre sans se faire passer pour le
    # lieu du match, et c'est ce qui rend l'ecart lisible d'un coup d'oeil.
    assert "(habituel : " in ligne
    assert not ligne.startswith("Injector"), "le stade habituel ne prend jamais la tete"


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
    _lot(migrated, "football")

    body = build_prompt(1, settings=migrated).body

    assert "Buts marq." in body
    assert "jamais** : les traiter comme des probabilités" in body
    assert "espérance" in body
    assert "les siens uniquement" in body, "le sens de la ligne doit etre sans ambiguite"


# -- Compositions : la seule donnee dont la disponibilite depend de l'heure ----

#: Coup d'envoi a 15h30 UTC. Une heure avant, la compo est publiee.
PROCHE = datetime(2026, 8, 3, 14, 30, tzinfo=UTC)
#: Cinq heures et demie avant : le fournisseur n'a encore rien.
LOIN = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)

LEAGUES_SANS_COMPOS = {
    "errors": [],
    "response": [
        {
            "league": {"id": 113, "name": "Allsvenskan", "type": "League"},
            "seasons": [
                {
                    "year": 2026,
                    "current": True,
                    "coverage": {
                        "fixtures": {"events": True, "lineups": False},
                        "standings": True,
                        "injuries": True,
                    },
                }
            ],
        }
    ],
}


def _mock_lineups(load_fixture: Any, payload: Any = None) -> respx.Route:
    return respx.get(f"{BASE_URL}/fixtures/lineups").mock(
        return_value=httpx.Response(
            200,
            json=payload if payload is not None else load_fixture("apifootball_lineups.json"),
            headers=RATE_HEADERS,
        )
    )


@respx.mock
@pytest.mark.anyio
async def test_la_compo_n_est_pas_demandee_trop_tot(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Mesure en reel : a 2h30, 3h30 et 5h45 du coup d'envoi, l'endpoint rend
    zero equipe. Appeler la depenserait un appel par match et par
    enrichissement pour une reponse vide.

    Et **aucune mention** n'est produite : contrairement aux absents, une compo
    qui manque cinq heures avant ne dit rien de l'equipe. L'annoncer « non
    disponible » ferait chercher un trou de collecte la ou il n'y a qu'une
    heure trop tot.
    """
    _seed_event(migrated)
    _mock_all(load_fixture)
    route = _mock_lineups(load_fixture)

    await fetch_context(api_client, EVENT, migrated, now=LOIN)

    assert route.call_count == 0
    assert "Compos" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_la_compo_arrive_a_l_approche_du_coup_d_envoi(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Ce que l'utilisateur collait a la main : qui joue vraiment. La formation
    accompagne le onze — c'est l'ecart avec l'habitude de la saison, que la
    ligne « Formations » donne deja, qui se lit ici."""
    _seed_event(migrated)
    _mock_all(load_fixture)
    route = _mock_lineups(load_fixture)

    await fetch_context(api_client, EVENT, migrated, now=PROCHE)

    assert route.call_count == 1
    compos = _lines(migrated)["Compos"]
    assert "BK Hacken (4-4-2) P. Hansson, E. Lindberg" in compos
    assert "Djurgardens IF (4-3-3) H. Widell" in compos
    assert compos.count("|") == 1, "une equipe de chaque cote, comme les autres lignes"


@respx.mock
@pytest.mark.anyio
async def test_le_banc_n_entre_pas_dans_le_prompt(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Vingt-quatre noms de plus y couteraient plus qu'ils n'apprennent. Il est
    collecte quand meme : il ne coute aucun appel de plus."""
    _seed_event(migrated)
    _mock_all(load_fixture)
    _mock_lineups(load_fixture)

    await fetch_context(api_client, EVENT, migrated, now=PROCHE)

    assert "R. Palm" not in _lines(migrated)["Compos"]
    stored = load(1, migrated)["lineups"]
    assert "R. Palm" in stored["home"]["bench"]


@respx.mock
@pytest.mark.anyio
async def test_une_compo_non_couverte_n_est_pas_demandee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Meme sous-objet que les statistiques de match, meme piege. Le drapeau
    vaut la peine d'etre lu : sur la Super League chinoise, `injuries` est faux
    quand `lineups` est vrai."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COMPOS, headers=RATE_HEADERS)
    )
    route = _mock_lineups(load_fixture)

    await fetch_context(api_client, EVENT, migrated, now=PROCHE)

    assert route.call_count == 0


@respx.mock
@pytest.mark.anyio
async def test_une_compo_vide_n_est_pas_figee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les compositions sortent au compte-gouttes : figer « rien » empecherait
    un second essai dix minutes plus tard de rapporter quelque chose."""
    _seed_event(migrated)
    _mock_all(load_fixture)
    _mock_lineups(load_fixture, {"errors": [], "response": []})

    await fetch_context(api_client, EVENT, migrated, now=PROCHE)

    assert "lineups" not in load(1, migrated)
    assert "Compos" not in _lines(migrated)


# -- Balayage planifie des compositions ---------------------------------------


def _shortlist(settings: Settings, event_id: int = 1) -> None:
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('x', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    session = db.query_one("SELECT id FROM sessions ORDER BY id DESC LIMIT 1", settings=settings)
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
        (session["id"], event_id),
        settings=settings,
    )
    db.execute(
        "UPDATE events SET apifootball_fixture_id = 1122334 WHERE id = ?",
        (event_id,),
        settings=settings,
    )


def _memorise_mapping(settings: Settings, lineups: bool = True) -> None:
    """Ce que `resolve_fixture` ecrit au rapprochement, et que le balayage relit."""
    store(
        1,
        "teams",
        {
            "home": 376,
            "away": 377,
            "league": 113,
            "season": 2026,
            "coverage": {"fixtures": {"lineups": lineups}, "injuries": True},
        },
        settings,
    )


@respx.mock
@pytest.mark.anyio
async def test_le_balayage_ne_coute_qu_un_appel_par_match(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Tout ce dont il a besoin est deja en base : l'identifiant de match sur
    l'evenement, la couverture memorisee au rapprochement. Repasser par
    `fetch_context` couterait une dizaine d'appels pour une seule donnee."""
    _seed_event(migrated)
    _shortlist(migrated)
    _memorise_mapping(migrated)
    route = _mock_lineups(load_fixture)
    autres = _mock_all(load_fixture)

    sweep = await refresh_due_lineups(api_client, migrated, now=PROCHE)

    assert route.call_count == 1
    assert sweep.fetched == ["BK Hacken – Djurgardens IF"]
    # `lineups` est exclu : c'est justement l'endpoint que le balayage appelle,
    # et il figure desormais dans le jeu de routes complet.
    assert all(route.call_count == 0 for nom, route in autres.items() if nom != "lineups"), (
        "aucun autre endpoint appele"
    )
    assert "BK Hacken (4-4-2)" in dict(_lines(migrated))["Compos"]


@respx.mock
@pytest.mark.anyio
async def test_le_balayage_respecte_la_fenetre(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Il tourne toutes les dix minutes : hors fenetre, il ne doit rien couter
    de plus qu'une lecture en base."""
    _seed_event(migrated)
    _shortlist(migrated)
    _memorise_mapping(migrated)
    route = _mock_lineups(load_fixture)

    sweep = await refresh_due_lineups(api_client, migrated, now=LOIN)

    assert route.call_count == 0
    assert sweep.checked == 0


@respx.mock
@pytest.mark.anyio
async def test_le_balayage_ignore_une_competition_sans_compositions(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La couverture est deja connue : la relire est gratuit, l'appel non."""
    _seed_event(migrated)
    _shortlist(migrated)
    _memorise_mapping(migrated, lineups=False)
    route = _mock_lineups(load_fixture)

    await refresh_due_lineups(api_client, migrated, now=PROCHE)

    assert route.call_count == 0


@respx.mock
@pytest.mark.anyio
async def test_le_balayage_ne_redemande_pas_une_compo_connue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une composition ne change plus une fois publiee, et le balayage repasse
    toutes les dix minutes : sans ce filtre, chaque match serait redemande
    jusqu'a son coup d'envoi."""
    _seed_event(migrated)
    _shortlist(migrated)
    _memorise_mapping(migrated)
    route = _mock_lineups(load_fixture)
    await refresh_due_lineups(api_client, migrated, now=PROCHE)

    await refresh_due_lineups(api_client, migrated, now=PROCHE)

    assert route.call_count == 1, "le second passage ne redemande rien"


@respx.mock
@pytest.mark.anyio
async def test_le_balayage_ignore_un_match_hors_shortlist(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un match que personne n'a coche n'ira dans aucun prompt : payer sa
    composition depenserait le quota sur une rencontre que rien ne lira."""
    _seed_event(migrated)
    _memorise_mapping(migrated)
    db.execute("UPDATE events SET apifootball_fixture_id = 1122334 WHERE id = 1", settings=migrated)
    route = _mock_lineups(load_fixture)

    await refresh_due_lineups(api_client, migrated, now=PROCHE)

    assert route.call_count == 0


# -- Ce qui arrivait deja et n'etait pas lu -----------------------------------


@respx.mock
@pytest.mark.anyio
async def test_les_buts_encaisses_completent_les_buts_marques(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`goals.for.under_over` etait lu, `goals.against.under_over` jamais — dans
    la meme charge utile, deja persistee entiere. On savait dans combien de
    matchs une equipe avait marque deux buts, pas dans combien elle en avait
    encaisse deux, ce qui est pourtant la seule ligne qui decrive une defense."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert "Buts pris" in lignes
    assert lignes["Buts pris"] != lignes["Buts marq."], "deux cotes, deux comptes"
    assert routes["stats_home"].call_count == 1, "aucun appel de plus"


def _feuilles(route: respx.Route, noms_par_match: dict[int, list[str]]) -> respx.Route:
    """Une feuille de match par identifiant de rencontre, pour l'equipe 376.

    L'equipe 377 garde un onze stable : la meme reponse sert les deux cotes, et
    seul le domicile doit produire une ligne.
    """

    def _repondre(request: httpx.Request) -> httpx.Response:
        fixture = int(request.url.params["fixture"])
        return httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "team": {"id": 376},
                        "startXI": [
                            {"player": {"name": nom}} for nom in noms_par_match.get(fixture, [])
                        ],
                        "substitutes": [],
                    },
                    {
                        "team": {"id": 377},
                        "startXI": [{"player": {"name": "Stable"}}],
                        "substitutes": [],
                    },
                ],
            },
            headers=RATE_HEADERS,
        )

    return route.mock(side_effect=_repondre)


@respx.mock
@pytest.mark.anyio
async def test_l_effectif_se_reconstruit_la_ou_les_absents_ne_sont_pas_couverts(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`coverage.injuries` est faux sur **46 des 65** evenements rapproches en
    base — 71 % —, alors que les compositions sont servies sur 55. La ligne la
    plus decisive du bloc etait donc morte sur trois quarts du board avec, sous
    la main, de quoi la reconstruire.

    Un joueur vu sur deux feuilles puis absent des deux dernieres est signale
    avec la date ou on l'a vu pour la derniere fois."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COUVERTURE, headers=RATE_HEADERS)
    )
    feuilles = _feuilles(
        routes["lineups"],
        {
            800000: ["Andersson", "Berg", "Carlsson"],
            800001: ["Andersson", "Berg", "Carlsson"],
            800002: ["Andersson", "Berg", "Knap"],
            800003: ["Andersson", "Berg", "Knap"],
        },
    )

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert lignes["Absents"] == (
        "non interroges — le fournisseur ne couvre pas les absents sur cette "
        "competition, la recherche est le seul chemin"
    ), "le fournisseur ne couvre toujours pas"
    # La fenetre accompagne la liste : « plus vu depuis le 19/07 » ne dit pas sur
    # quoi il repose, et c'est ce qui a rendu un faux positif indetectable.
    assert lignes["Effectif"] == (
        "BK Hacken — Knap plus vu depuis le 19/07 "
        "(fenetre lue : 4 feuille(s), du 13/07 au 28/07, toutes competitions)"
    )
    assert feuilles.call_count == SHEETS_LAST, "une feuille par match de la fenetre"


@respx.mock
@pytest.mark.anyio
async def test_aucune_feuille_n_est_payee_la_ou_les_absents_sont_couverts(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La ou `/injuries` repond, il dit mieux et gratuitement. Ce bloc est un
    substitut, jamais un doublon — et il coute un appel par feuille."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    feuilles = _feuilles(routes["lineups"], {800000: ["Andersson"]})

    await fetch_context(api_client, EVENT, migrated)

    assert feuilles.call_count == 0, "aucun appel la ou la couverture existe"
    assert "Effectif" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_un_effectif_stable_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Ecrire « aucun » affirmerait un effectif au complet, ce que des feuilles
    de match ne peuvent pas prouver : un joueur ecarte avant la fenetre lue n'y
    figure pas du tout."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COUVERTURE, headers=RATE_HEADERS)
    )
    _feuilles(
        routes["lineups"], dict.fromkeys((800000, 800001, 800002, 800003), ["Andersson", "Berg"])
    )

    await fetch_context(api_client, EVENT, migrated)

    assert "Effectif" not in _lines(migrated)


def _h2h(
    settings: Settings,
    *,
    jours: int,
    league: int | None = 113,
    inverse: bool = True,
    buts: tuple[int, int] = (2, 0),
) -> None:
    """Une confrontation directe unique, datee par rapport au match analyse.

    `buts` est le score de l'aller **tel qu'il s'est joue** : le premier nombre
    pour celui qui recevait ce jour-la, donc pour l'equipe qui se deplace
    aujourd'hui."""
    joue = datetime(2026, 8, 3, 15, 30, tzinfo=UTC) - timedelta(days=jours)
    store(1, KIND_TEAMS, {"home": 376, "away": 377, "league": 113, "season": 2026}, settings)
    store(
        1,
        KIND_H2H,
        {
            "home_id": 376,
            "matches": [
                {
                    # `inverse` : celui qui recoit aujourd'hui se deplacait.
                    "home_id": 377 if inverse else 376,
                    "home_goals": buts[0],
                    "away_goals": buts[1],
                    "date": joue.isoformat(),
                    "league_id": league,
                }
            ],
        },
        settings,
    )


def test_l_aller_d_une_double_confrontation_est_nomme(migrated: Settings) -> None:
    """La fiche de verification appelle ca « le premier determinant du
    scenario » et rien ne le servait : le resume H2H gardait les scores et
    jetait la competition, si bien qu'un aller de coupe d'Europe ne se
    distinguait pas d'un match de championnat d'il y a deux ans.

    Le score se lit du point de vue de l'equipe qui recoit aujourd'hui, comme
    « H2H » — deux conventions dans le meme bloc se liraient a l'envers."""
    _seed_event(migrated)
    _h2h(migrated, jours=7)

    lignes = _lines(migrated)

    assert lignes["Aller"] == "0-2 le 27/07, Djurgardens IF recevait"


@pytest.mark.parametrize(
    ("cas", "kwargs"),
    [
        ("une autre competition", {"league": 3}),
        ("le meme terrain, donc pas un retour", {"inverse": False}),
        ("trop ancien pour une double confrontation", {"jours": 40}),
        ("competition inconnue au rapprochement", {"league": None}),
    ],
)
def test_aucun_aller_hors_des_trois_conditions(
    migrated: Settings, cas: str, kwargs: dict[str, Any]
) -> None:
    """Le terrain inverse est le discriminant fort : sans lui, deux journees de
    championnat rapprochees passeraient pour une double confrontation."""
    _seed_event(migrated)
    _h2h(migrated, **{"jours": 7, **kwargs})

    assert "Aller" not in _lines(migrated), cas


def test_un_releve_d_avant_le_champ_ne_produit_pas_d_aller(migrated: Settings) -> None:
    """Un h2h stocke avant que la competition ne soit gardee n'a pas de
    `league_id` : aucune ligne, jusqu'au prochain enrichissement. Le rendu ne
    doit pas lever pour autant."""
    _seed_event(migrated)
    store(1, KIND_TEAMS, {"home": 376, "away": 377, "league": 113, "season": 2026}, migrated)
    store(
        1,
        KIND_H2H,
        {
            "home_id": 376,
            "matches": [
                {"home_id": 377, "home_goals": 2, "away_goals": 0, "date": "2026-07-27T15:30:00Z"}
            ],
        },
        migrated,
    )

    lignes = _lines(migrated)

    assert "Aller" not in lignes
    assert lignes["H2H (1)"] == "0-2 D", "la suite des scores tient toujours"


def _standing(rank: int, played: int, stake: str = "") -> dict[str, Any]:
    return {"rank": rank, "points": 0, "played": played, "diff": 0, "stake": stake}


def test_un_classement_a_zero_match_joue_ne_classe_rien(migrated: Settings) -> None:
    """Le fournisseur classe tout le monde des avant le premier coup d'envoi :
    l'Eredivisie ouvrait sa saison avec « FC Zwolle 7e (0pts, 0j, +0) » et
    « Ajax 8e (0pts, 0j, +0) », et l'enjeu qui s'en deduit annoncait
    « Conference League - Play Offs » sur une saison pas encore commencee.

    Toutes les statistiques de saison se taisaient deja sur ces deux matchs :
    ces deux lignes-la etaient les seules a passer au travers."""
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {
            "home": _standing(7, 0, "Promotion - Eredivisie (Conference League - Play Offs)"),
            "away": _standing(8, 0),
        },
        migrated,
    )

    lignes = _lines(migrated)

    assert "Classement" not in lignes
    assert "Enjeu" not in lignes


def test_des_la_premiere_journee_le_classement_est_rendu(migrated: Settings) -> None:
    """Le seuil de **rendu** est un match et non cinq : des la premiere journee
    le rang decrit un resultat reel.

    Mais il ne **classe** pas pour autant, et c'est la reserve qui le dit — la
    meme que celle d'`Enjeu`, sous le meme seuil, les deux lignes sortant du
    meme classement a la meme journee. Sans elle, deux 5es separes par une
    division sortaient a egalite apparente.
    """
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {"home": _standing(9, 1, "Premiership (Relegation Group)"), "away": _standing(4, 1)},
        migrated,
    )

    lignes = _lines(migrated)

    assert lignes["Classement"].startswith("BK Hacken 9e")
    assert "(après 1j — indicatif)" in lignes["Classement"]
    # Le compte de journees vit **dans la reserve** : l'ecrire aussi dans le
    # detail le ferait paraitre deux fois entre deux parentheses voisines.
    assert "1j," not in lignes["Classement"]
    assert lignes["Enjeu"].endswith("Premiership (Relegation Group) (après 1j — indicatif)")


def test_un_classement_etabli_ne_porte_aucune_reserve(migrated: Settings) -> None:
    """Au-dela du seuil, le rang classe : la reserve disparait des deux lignes,
    et le compte de journees reprend sa place dans le detail."""
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {"home": _standing(9, 20, "Relegation Round"), "away": _standing(4, 20)},
        migrated,
    )

    lignes = _lines(migrated)

    assert "indicatif" not in lignes["Classement"]
    assert "20j" in lignes["Classement"]
    assert "indicatif" not in lignes["Enjeu"]


def test_la_forme_dit_sur_combien_de_matchs_portent_ses_buts(migrated: Settings) -> None:
    """Les deux moities de la ligne ne portent pas sur la meme fenetre : les
    lettres viennent de la seule competition du jour, les buts des cinq derniers
    matchs toutes competitions. Elles coincidaient partout sauf en debut de
    saison, ou « Celtic V (6-8) » se lisait « une victoire, six buts marques,
    huit encaisses » — et « Slask Wroclaw DV (12-4) » douze buts en deux
    matchs."""
    _seed_event(migrated)
    store(1, KIND_FORM, {"home": {"form": "W"}, "away": {"form": "LW"}}, migrated)
    store(
        1,
        KIND_RECENT,
        {
            "home": {"goals_for": 6, "goals_against": 8, "matches": 5},
            "away": {"goals_for": 12, "goals_against": 4, "matches": 5},
        },
        migrated,
    )

    forme = _lines(migrated)["Forme 5"]

    # Chaque moitie porte desormais **son** denominateur : une lettre pour la
    # competition, cinq matchs pour les buts. Un seul compte laissait croire a
    # douze buts en deux matchs.
    assert "V (1j) 6-8/5" in forme
    assert "DV (2j) 12-4/5" in forme


@respx.mock
@pytest.mark.anyio
async def test_l_enjeu_vient_du_classement_et_ne_coute_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`description` arrivait avec le classement et partait a la poubelle. C'est
    pourtant l'« enjeu reel » que la fiche de verification reclame a chaque
    match, et que la recherche web devait deviner du rang.

    Le libelle est recopie **tel quel** : il vient de la competition, et le
    reecrire serait s'en porter garant.
    """
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["standings"].mock(
        return_value=httpx.Response(
            200,
            json={
                "errors": [],
                "response": [
                    {
                        "league": {
                            "standings": [
                                [
                                    {
                                        "rank": 1,
                                        "team": {"id": 376, "name": "BK Hacken"},
                                        "points": 41,
                                        "goalsDiff": 12,
                                        "description": "Play-offs",
                                        "all": {"played": 16},
                                    }
                                ]
                            ]
                        }
                    }
                ],
            },
            headers=RATE_HEADERS,
        )
    )

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert lignes["Enjeu"] == "BK Hacken Play-offs"
    assert "+12" in lignes["Classement"], "la difference de buts separe deux egalites de points"


@respx.mock
@pytest.mark.anyio
async def test_fautes_et_possession_sortent_du_meme_appel(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un appel `/fixtures/statistics` rend dix-huit statistiques ; cinq etaient
    gardees, treize jetees avant la base. En garder deux de plus ne coute aucun
    appel — seulement de la place."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    stats = load_fixture("apifootball_fixture_statistics.json")
    for equipe in stats["response"]:
        equipe["statistics"] += [
            {"type": "Fouls", "value": 13},
            {"type": "Ball Possession", "value": "56%"},
        ]
    routes["fixture_stats"].mock(return_value=httpx.Response(200, json=stats, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    lignes = _lines(migrated)
    assert "Fautes" in lignes and "subies" in lignes["Fautes"]
    assert "%" in lignes["Possession"]


def test_la_possession_est_le_seul_pourcentage_du_bloc() -> None:
    """L'interdit vise les **frequences d'issues** : « BTTS 56 % » invite a
    diviser par une cote, ce qui est un calcul d'esperance. Une part de ballon
    ne se rapporte a aucun marche et rien ne se divise par elle — son unite
    naturelle est le pourcentage, et le template l'explique."""
    from myassistantbet.config import PACKAGE_DIR

    template = (PACKAGE_DIR / "templates" / "prompts" / "session_default.md.j2").read_text(
        encoding="utf-8"
    )

    assert "seul pourcentage du bloc" in template
    assert "fréquences" in template and "diviser par une cote" in template


@respx.mock
@pytest.mark.anyio
async def test_le_xg_arrive_du_meme_appel(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Buts attendus produits puis concedes. Le « concede » vient de l'adversaire
    du meme match, comme les corners : un seul appel donne les deux cotes."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    stats = load_fixture("apifootball_fixture_statistics.json")
    for index, equipe in enumerate(stats["response"]):
        equipe["statistics"].append(
            {"type": "expected_goals", "value": "1.85" if index else "0.92"}
        )
    routes["fixture_stats"].mock(return_value=httpx.Response(200, json=stats, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "concédé" in _lines(migrated)["xG"]


@respx.mock
@pytest.mark.anyio
async def test_un_xg_non_servi_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Verifie en reel : la Super League chinoise rend `expected_goals: null`.
    Son absence ne dit rien de l'equipe, seulement du fournisseur — donc aucune
    ligne, et surtout pas un zero qui se lirait comme une equipe sans occasion."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    stats = load_fixture("apifootball_fixture_statistics.json")
    for equipe in stats["response"]:
        equipe["statistics"].append({"type": "expected_goals", "value": None})
    routes["fixture_stats"].mock(return_value=httpx.Response(200, json=stats, headers=RATE_HEADERS))

    await fetch_context(api_client, EVENT, migrated)

    assert "xG" not in _lines(migrated)


def test_le_template_interdit_de_convertir_le_xg() -> None:
    """C'est la ligne la plus tentante a convertir en probabilite du bloc entier.
    Meme garde-fou que l'Elo, et meme raison : rapprochee d'une cote, elle
    devient le calcul d'esperance de la section 9. Le fait que le chiffre vienne
    du fournisseur n'y change rien."""
    from myassistantbet.config import PACKAGE_DIR

    template = (PACKAGE_DIR / "templates" / "prompts" / "session_default.md.j2").read_text(
        encoding="utf-8"
    )
    bloc = template[template.index("**« xG »**") : template.index("**« Possession »**")]

    assert "jamais** en probabilité" in bloc
    assert "cote" in bloc
    assert "sortie de modèle" in bloc


def _lot(settings: Settings, sport: str) -> int:
    """Une session portant un match de ce sport, et son identifiant.

    Le preambule du prompt ne documente que les sports **presents dans le lot** :
    une session de football n'a pas a payer les quarante lignes d'explication du
    tennis. Ces tests portent donc sur un lot du bon sport — sur une session
    vide, aucun garde-fou ne se rendrait, et pour cause.
    """
    row = db.query_one(f"SELECT id FROM sports WHERE key = '{sport}'", settings=settings)
    db.execute(
        "INSERT INTO events (id, sport_id, home, away, commence_time, source, created_at) "
        "VALUES (900, ?, 'A', 'B', '2099-01-01T18:00:00Z', 'api', ?)",
        (row["id"], db.utcnow()),
        settings=settings,
    )
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (1, 900)", settings=settings
    )
    return 1


def test_l_enjeu_de_debut_de_saison_est_date_et_marque(migrated: Settings) -> None:
    """A la 3e journee sur 32, « Relegation Playoffs » decrit l'ordre
    alphabetique autant que le niveau. Le prompt ordonne pourtant de recopier
    cette ligne comme l'enjeu reel, sans recherche.

    Elle est **datee** plutot que supprimee : l'information reste — c'est bien ce
    que la competition declare — et sa portee est dite.
    """
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {"home": _standing(9, 2, "Relegation Playoffs"), "away": _standing(4, 2)},
        migrated,
    )

    enjeu = _lines(migrated)["Enjeu"]

    assert "Relegation Playoffs (après 2j — indicatif)" in enjeu


def test_un_enjeu_de_saison_avancee_n_est_pas_marque(migrated: Settings) -> None:
    """Passe le seuil, le classement a decante : l'enjeu vaut pour lui-meme."""
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {"home": _standing(9, 20, "Relegation Playoffs"), "away": _standing(4, 20)},
        migrated,
    )

    assert "indicatif" not in _lines(migrated)["Enjeu"]


def test_le_seuil_de_l_enjeu_se_lit_dans_les_reglages(migrated: Settings) -> None:
    """Le total d'une saison ne se deduit pas du nombre d'equipes — une Superliga
    danoise joue 32 journees a douze equipes — donc le seuil se regle."""
    _seed_event(migrated)
    store(
        1,
        KIND_STANDINGS,
        {"home": _standing(9, 3, "Play-offs"), "away": _standing(4, 3)},
        migrated,
    )
    assert "indicatif" in _lines(migrated)["Enjeu"]

    save_threshold("enjeu_min_journees", "2", migrated)

    assert "indicatif" not in _lines(migrated)["Enjeu"]


# -- Le scenario d'une manche retour -----------------------------------------

#: Les configurations rencontrees en deux jours de tours preliminaires. `buts`
#: est le score de l'aller du point de vue de celui qui recevait alors, donc de
#: **Djurgardens IF**, qui se deplace aujourd'hui. `BK Hacken` recoit.
SCENARIOS = [
    (
        "avance de trois, qualification acquise en l'etat",
        (3, 0),
        "cumul 0-3 — Djurgardens IF qualifie en l'etat ; "
        "BK Hacken (a domicile) doit gagner de 3 pour egaliser, de 4 pour passer",
    ),
    (
        "avance de deux, le cas le plus frequent de la semaine",
        (2, 0),
        "cumul 0-2 — Djurgardens IF qualifie en l'etat ; "
        "BK Hacken (a domicile) doit gagner de 2 pour egaliser, de 3 pour passer",
    ),
    (
        "avance d'un but",
        (1, 0),
        "cumul 0-1 — Djurgardens IF qualifie en l'etat ; "
        "BK Hacken (a domicile) doit gagner de 1 pour egaliser, de 2 pour passer",
    ),
    (
        "l'equipe qui recoit aujourd'hui mene : l'obligation change de camp",
        (0, 2),
        "cumul 2-0 — BK Hacken qualifie en l'etat ; "
        "Djurgardens IF (a l'exterieur) doit gagner de 2 pour egaliser, de 3 pour passer",
    ),
    (
        "aller nul avec buts — la lecture qui se trompe le plus souvent",
        (2, 2),
        "cumul 2-2 — rien n'est fait, le vainqueur de ce match passe",
    ),
    (
        "aller nul et vierge",
        (0, 0),
        "cumul 0-0 — rien n'est fait, le vainqueur de ce match passe",
    ),
]


@pytest.mark.parametrize(("cas", "buts", "attendu"), SCENARIOS)
def test_le_scenario_d_une_manche_retour_est_calcule(
    migrated: Settings, cas: str, buts: tuple[int, int], attendu: str
) -> None:
    """Vingt-quatre manches retour en une semaine ont demande le meme calcul
    refait a la main : cumul, qui mene, combien il faut a celui qui est mene.
    Il est deterministe, donc il ne se delegue pas au modele."""
    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=buts)

    assert _lines(migrated)["Scenario"] == attendu, cas


def test_le_scenario_separe_egaliser_de_passer(migrated: Settings) -> None:
    """Les deux seuils ne produisent pas la meme fin de match, et c'est le
    second qui decide si l'equipe s'ouvre encore a la 80e. Un cumul seul laisse
    ce travail a faire."""
    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=(2, 0))

    ligne = _lines(migrated)["Scenario"]

    assert "doit gagner de 2 pour egaliser" in ligne
    assert "de 3 pour passer" in ligne


def test_le_scenario_ne_se_porte_pas_garant_d_un_reglement(migrated: Settings) -> None:
    """La regle des buts a l'exterieur, la prolongation et les tirs au but sont
    des regles de **competition**, pas de l'arithmetique. Le preambule les
    enonce une fois pour le lot et la fiche de la competition prime : les
    affirmer par match reviendrait a se porter garant d'un reglement qu'on n'a
    pas lu — la Supercoupe d'Europe va aux tirs au but sans prolongation."""
    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=(2, 0))

    ligne = _lines(migrated)["Scenario"]

    for mot in ("prolongation", "tirs au but", "buts a l'exterieur"):
        assert mot not in ligne, f"« {mot} » est une regle, pas un calcul"


@pytest.mark.parametrize(
    ("cas", "kwargs"),
    [
        ("une autre competition", {"league": 3}),
        ("le meme terrain, donc pas un retour", {"inverse": False}),
        ("trop ancien pour une double confrontation", {"jours": 40}),
    ],
)
def test_aucun_scenario_hors_des_trois_conditions(
    migrated: Settings, cas: str, kwargs: dict[str, Any]
) -> None:
    """La detection est **partagee** avec « Aller » : deux ecritures auraient
    fini par diverger, et le bloc aurait annonce un scenario sur une rencontre
    que l'autre ligne ne reconnaissait plus comme un aller."""
    _seed_event(migrated)
    _h2h(migrated, **{"jours": 7, **kwargs})

    assert "Scenario" not in _lines(migrated), cas


def test_le_mode_d_emploi_du_scenario_ne_se_paie_que_sur_un_lot_qui_en_porte(
    migrated: Settings,
) -> None:
    """Meme regle que partout : le preambule ne documente que les lignes que le
    lot porte vraiment. Une manche retour est l'exception, pas l'ordinaire."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    session_id = board_service.toggle_selection(1, True, migrated)
    # Avant le coup d'envoi : un match commence quitte le prompt.
    avant = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    sans = " ".join(build_prompt(session_id, settings=migrated, now=avant).body.split())
    _h2h(migrated, jours=7, buts=(2, 0))
    avec = " ".join(build_prompt(session_id, settings=migrated, now=avant).body.split())

    assert "**« Scénario »** est un **calcul**" not in sans
    assert "**« Scénario »** est un **calcul**" in avec


def test_le_preambule_porte_la_regle_que_la_ligne_ne_dit_pas(migrated: Settings) -> None:
    """La ligne fait de l'arithmetique, le preambule porte le reglement — une
    fois pour le lot, et en disant que la fiche de la competition prime. Sans
    quoi le bloc affirmerait une prolongation sur une Supercoupe qui va
    directement aux tirs au but."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=(2, 0))
    session_id = board_service.toggle_selection(1, True, migrated)
    avant = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    corps = " ".join(build_prompt(session_id, settings=migrated, now=avant).body.split())

    assert "abolie en Coupe d'Europe depuis 2021" in corps
    assert "La fiche de la compétition prime sur cette phrase" in corps


def test_le_mode_d_emploi_de_l_aller_renvoie_au_scenario(migrated: Settings) -> None:
    """« a toi de dire s'il s'agit d'une double confrontation » etait juste tant
    que rien ne la calculait. Toute condition ajoutee a une ligne se verifie
    contre la phrase du preambule qui la decrit."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=(2, 0))
    session_id = board_service.toggle_selection(1, True, migrated)
    avant = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)

    corps = " ".join(build_prompt(session_id, settings=migrated, now=avant).body.split())

    assert "à toi de dire s'il s'agit d'une double confrontation" not in corps
    assert "La ligne « Scénario » en tire l'arithmétique" in corps


@respx.mock
@pytest.mark.anyio
async def test_le_scenario_ne_promet_pas_un_avantage_du_terrain_a_l_etranger(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« (a domicile) » suppose un avantage, et sur un lot reel trois manches
    retour auraient rendu la mention fausse : Vitebsk « recevait » en Hongrie,
    Minsk en Bulgarie. Le mot inverse alors le sens de la phrase."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["venue"] = {
        "id": 999,
        "name": "Arena Lublin",
        "city": "Lublin",
    }
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)
    # L'aller s'est joue chez l'adversaire, qui mene : nous sommes menes « chez
    # nous », sauf que ce chez-nous est en Pologne.
    _h2h(migrated, jours=7, buts=(2, 0))

    scenario = _lines(migrated)["Scenario"]

    assert "(a domicile)" not in scenario
    assert "nominalement a domicile, terrain neutre" in scenario


@respx.mock
@pytest.mark.anyio
async def test_l_aller_porte_le_stade_ou_il_s_est_joue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le fait decisif d'une manche retour reelle : Dynamo Kyiv avait recu a
    Lublin, si bien que personne n'avait joue « a l'exterieur » et que le
    scenario se lisait de travers.

    **Aucun appel de plus** — le stade vient du meme `/fixtures/headtohead`. Dire
    s'il etait neutre demanderait le stade habituel de l'equipe qui recevait ce
    jour-la, donc un appel par match : arbitrage rendu, et c'est non. Le nom du
    lieu suffit a faire sauter l'anomalie aux yeux."""
    _seed_event(migrated)
    store(1, KIND_TEAMS, {"home": 376, "away": 377, "league": 113}, migrated)
    store(
        1,
        KIND_H2H,
        {
            "home_id": 376,
            "matches": [
                {
                    "home_id": 377,
                    "home_goals": 1,
                    "away_goals": 0,
                    "date": "2026-07-27T15:30:00Z",
                    "league_id": 113,
                    "venue": {"id": 999, "name": "Arena Lublin", "city": "Lublin"},
                }
            ],
        },
        migrated,
    )

    assert _lines(migrated)["Aller"] == (
        "0-1 le 27/07, Djurgardens IF recevait a Arena Lublin, Lublin"
    )


@respx.mock
@pytest.mark.anyio
async def test_un_aller_sans_stade_nomme_ne_change_pas_la_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un releve d'avant ce champ n'a pas de stade : la ligne reste ce qu'elle
    etait, et surtout ne rend pas un « a » orphelin."""
    _seed_event(migrated)
    _h2h(migrated, jours=7, buts=(2, 0))

    assert _lines(migrated)["Aller"] == "0-2 le 27/07, Djurgardens IF recevait"


@respx.mock
@pytest.mark.anyio
async def test_l_arbitre_est_nomme_sans_un_appel_de_plus(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un marche Cartons est servi sur une partie des blocs sans qu'aucune ligne
    ne permette de le lire. Le nom vient du match deja resolu : il ne coute rien,
    et il supprime une requete sur deux — sans lui, il fallait chercher **qui**
    arbitre avant de chercher son historique."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Arbitre"] == "M. Oliver"


@respx.mock
@pytest.mark.anyio
async def test_une_designation_qui_n_est_pas_tombee_se_dit(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Deux etats a distinguer, et ils appellent deux comportements opposes : un
    arbitre non designe ne se cherche pas, il s'attend. Un arbitre nomme, si.

    **La journee decide, pas le match** : la designation de celui-ci n'est pas
    tombee, mais la competition en sert — c'est ce qui la distingue d'une
    competition qui n'en publie aucun chez ce fournisseur.
    """
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    matchs = load_fixture("apifootball_fixtures_date.json")
    matchs["response"][0]["fixture"]["referee"] = None
    matchs["response"][1]["fixture"]["referee"] = "M. Oliver"
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json=matchs, headers=RATE_HEADERS)
    )

    await fetch_context(api_client, EVENT, migrated)

    assert _lines(migrated)["Arbitre"] == "non encore designe"


@respx.mock
@pytest.mark.anyio
async def test_le_preambule_fait_de_l_absence_d_historique_un_fait(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« Aucun historique dans cette confederation » est une caracteristique du
    match, pas un echec de recherche : l'arbitre somalien d'une Supercoupe
    dirigeait son premier match en Europe, et ce fait a fini en section F comme
    un manque au lieu d'y etre porte comme un trait de la rencontre."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    _mock_all(load_fixture)
    await fetch_context(api_client, EVENT, migrated)
    session_id = board_service.toggle_selection(1, True, migrated)

    corps = " ".join(
        build_prompt(
            session_id, settings=migrated, now=datetime(2026, 8, 3, 12, tzinfo=UTC)
        ).body.split()
    )

    assert "**économie de recherche, pas un fait**" in corps
    assert "à écrire en section A comme telle et non en section F comme un manque" in corps
    assert "ne cherche pas, la désignation n'est pas tombée" in corps


@respx.mock
@pytest.mark.anyio
async def test_un_titulaire_d_une_autre_competition_n_est_jamais_porte_manquant(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le cas Hapoel, verifie avant d'etre code : trois joueurs annonces « plus
    vus depuis le 23/07 » alors qu'ils figuraient sur les feuilles du 30/07 et du
    06/08.

    **La fenetre etait deja toutes competitions** — elle sort de
    `/fixtures?team=&last=`, qui ne filtre sur aucune competition, et le chemin
    rejoue a l'identique sur les memes equipes ne reproduit pas le defaut. Ce
    test verrouille la propriete plutot que de corriger une regle qui etait
    juste : un joueur present sur une feuille d'une **autre** competition compte
    comme vu."""
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    routes["leagues"].mock(
        return_value=httpx.Response(200, json=LEAGUES_SANS_COUVERTURE, headers=RATE_HEADERS)
    )
    # Le plus recent des quatre matchs est une coupe nationale, hors du lot :
    # Knap y figure, donc il n'a pas disparu.
    _feuilles(
        routes["lineups"],
        {
            800000: ["Andersson", "Berg", "Knap"],
            800001: ["Andersson", "Berg", "Knap"],
            800002: ["Andersson", "Berg", "Knap"],
            800003: ["Andersson", "Berg", "Knap"],
        },
    )

    await fetch_context(api_client, EVENT, migrated)

    assert "Effectif" not in _lines(migrated)


# -- Agregats de saison d'un match de coupe ---------------------------------


def _leagues_payload(league_id: int, name: str, kind: str = "League") -> dict[str, Any]:
    """Une reponse `/leagues?team=` a une seule competition."""
    return {
        "errors": [],
        "response": [
            {
                "league": {"id": league_id, "name": name, "type": kind},
                "country": {"name": "Sweden"},
                "seasons": [{"year": 2026, "current": True, "coverage": {"standings": True}}],
            }
        ],
    }


def _standings_payload(league_id: int, name: str, team_id: int, rank: int) -> dict[str, Any]:
    return {
        "errors": [],
        "response": [
            {
                "league": {
                    "id": league_id,
                    "name": name,
                    "season": 2026,
                    "standings": [
                        [
                            {
                                "rank": rank,
                                "team": {"id": team_id, "name": "peu importe"},
                                "points": 12,
                                "all": {"played": 6},
                                "description": "Relegation Round",
                            }
                        ]
                    ],
                }
            }
        ],
    }


def _mock_domestic(away_leagues: dict[str, Any]) -> None:
    """Les routes propres a une coupe, posees **avant** les generiques.

    respx retient la premiere route qui correspond : les mocks specifiques
    doivent donc etre enregistres en tete, sinon le `/leagues` generique de
    `helpers` repondrait a la place.
    """
    respx.get(f"{BASE_URL}/leagues", params__contains={"team": "376"}).mock(
        return_value=httpx.Response(
            200, json=_leagues_payload(113, "Allsvenskan"), headers=RATE_HEADERS
        )
    )
    respx.get(f"{BASE_URL}/leagues", params__contains={"team": "377"}).mock(
        return_value=httpx.Response(200, json=away_leagues, headers=RATE_HEADERS)
    )
    respx.get(f"{BASE_URL}/standings", params__contains={"league": "114"}).mock(
        return_value=httpx.Response(
            200, json=_standings_payload(114, "Superettan", 377, 1), headers=RATE_HEADERS
        )
    )


@respx.mock
async def test_les_agregats_d_une_coupe_viennent_du_championnat_domestique(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Une coupe n'a ni classement ni assez de matchs pour une frequence.**

    `/teams/statistics` et `/standings` sont scopes a une competition : sur un
    tour de coupe, ses participants y ont joue un ou deux matchs. Le bloc
    perdait ses dix lignes les plus decisives exactement la ou l'ecart de
    niveau **est** le fait de la rencontre.
    """
    _seed_event(migrated)
    _mock_domestic(_leagues_payload(114, "Superettan"))
    _mock_all(load_fixture)

    report = await fetch_context(api_client, dict(EVENT, domestic_aggregates=True), migrated)

    assert report.ok
    lignes = _lines(migrated)
    # Chaque agregat porte sa competition d'origine : sans elle, « 1er » contre
    # « 4e » se lirait comme un match equilibre alors que ce sont deux divisions.
    assert "BK Hacken (Allsvenskan)" in lignes["Classement"]
    assert "Djurgardens IF (Superettan)" in lignes["Classement"]
    assert "BK Hacken (Allsvenskan)" in lignes["Forme 5"]
    assert "BK Hacken (Allsvenskan)" in lignes["Dom/Ext"]


@respx.mock
async def test_l_enjeu_ne_suit_pas_le_classement_hors_de_sa_competition(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un championnat declare « Relegation » ou « Play-offs ». Ce n'est pas
    l'enjeu d'un tour de coupe, et le prompt batit des scenarios de motivation
    sur cette ligne — le format d'une coupe releve de sa fiche, pas du bloc."""
    _seed_event(migrated)
    _mock_domestic(_leagues_payload(114, "Superettan"))
    _mock_all(load_fixture)

    await fetch_context(api_client, dict(EVENT, domestic_aggregates=True), migrated)

    lignes = _lines(migrated)
    assert "Classement" in lignes, "le classement, lui, est bien lu"
    assert "Enjeu" not in lignes


@respx.mock
async def test_un_championnat_domestique_ambigu_produit_un_motif_nomme(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Un trou sans motif est precisement ce qu'on repare.**

    Le fournisseur classe parfois une supercoupe en `League` : deux
    championnats en cours, et on ne tranche pas — trancher attribuerait a une
    equipe les statistiques d'une autre competition. Mais dix lignes
    disparaissent alors du bloc, et sans motif elles se liraient comme une
    equipe sans passe.
    """
    _seed_event(migrated)
    ambigu = _leagues_payload(114, "Superettan")
    ambigu["response"].append(
        {
            "league": {"id": 999, "name": "Supercup", "type": "League"},
            "country": {"name": "Sweden"},
            "seasons": [{"year": 2026, "current": True, "coverage": {"standings": True}}],
        }
    )
    _mock_domestic(ambigu)
    _mock_all(load_fixture)

    await fetch_context(api_client, dict(EVENT, domestic_aggregates=True), migrated)

    lignes = _lines(migrated)
    assert "Agregats" in lignes
    assert "Djurgardens IF : plusieurs championnats" in lignes["Agregats"]
    assert "Superettan, Supercup" in lignes["Agregats"]
    # L'equipe resolue garde les siens : le motif porte sur l'autre seulement.
    assert "BK Hacken (Allsvenskan)" in lignes["Classement"]
    assert "Djurgardens IF" not in lignes["Classement"]


@respx.mock
async def test_hors_coupe_rien_ne_change(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La regle ne se declenche que sur les competitions qui la declarent.

    Sans elle, aucun appel supplementaire n'est emis, le nom des equipes reste
    nu et l'enjeu se rend comme avant.
    """
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    equipes = respx.get(f"{BASE_URL}/leagues", params__contains={"team": "376"})

    await fetch_context(api_client, dict(EVENT), migrated)

    assert not equipes.called, "aucun appel de championnat domestique hors coupe"
    lignes = _lines(migrated)
    assert "(Allsvenskan)" not in lignes["Classement"]
    assert routes["standings"].called


# -- Typer l'echec d'un contexte --------------------------------------------


@respx.mock
async def test_une_competition_non_rattachee_nomme_sa_cause(
    api_client: APIFootballClient, migrated: Settings
) -> None:
    """**Quatre causes se repliaient sur une densite a zero.**

    Elle se lit « pas de donnees » alors qu'elle veut dire « on n'a pas pose la
    bonne question ». Les trois matchs saoudiens du 14/08 en sont l'exemple :
    leur competition n'etait rattachee a aucune ligue, donc rien n'a jamais ete
    demande, et le bloc ressemblait trait pour trait a celui d'une competition
    mal couverte.
    """
    _seed_event(migrated)
    db.execute(
        "UPDATE competitions SET apifootball_league_id = NULL WHERE apifootball_league_id = 113",
        settings=migrated,
    )

    report = await fetch_context(api_client, dict(EVENT, apifootball_league_id=None), migrated)

    assert report.cause == CAUSE_UNMAPPED
    assert failure_causes([1], migrated) == {1: CAUSE_UNMAPPED}
    # Le journal garde la tentative : sans elle, aucun denominateur.
    journal = db.query("SELECT cause FROM context_outcomes", settings=migrated)
    assert [row["cause"] for row in journal] == [CAUSE_UNMAPPED]


@respx.mock
async def test_une_fixture_non_resolue_nomme_sa_cause(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La ligue est connue, la rencontre non : c'est un defaut de collecte.

    **Et la forme compte autant que le fait**, depuis que les trois sont
    separees : ici le fournisseur ne sert **aucune** rencontre ce jour-la, donc
    ni nos noms ni notre date ne sont en cause — un alias n'y changerait rien, et
    c'est le seul des trois cas qui se retente utilement plus tard.
    """
    _seed_event(migrated)
    routes = _mock_all(load_fixture)
    # **Apres les routes generiques, et non avant.** respx retrouve une route
    # par son motif : re-mocker le meme motif remplace la reponse au lieu d'en
    # ajouter une seconde, donc l'enregistrer en tete la ferait ecraser.
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.mapping_pending
    assert report.cause == CAUSE_PROVIDER_EMPTY
    assert report.cause in COLLECTION_FAULTS, "ca se repare, ca ne se cherche pas"
    # `failure_causes` se relit sur l'etat de la base, qui ne porte que le
    # drapeau `mapping_pending` : elle rend donc la forme generique. Les deux
    # lectures ne mentent pas l'une sur l'autre — l'une dit ce qui s'est passe a
    # l'appel, l'autre ce que la base sait aujourd'hui.
    assert failure_causes([1], migrated) == {1: CAUSE_UNRESOLVED}


@respx.mock
async def test_une_source_injoignable_ne_se_relit_que_dans_le_journal(
    api_client: APIFootballClient, migrated: Settings
) -> None:
    """**C'est la cause qui a impose le journal.**

    Une competition non rattachee et une fixture non resolue sont des etats de
    la base et se relisent a tout moment. « Source injoignable », lui, n'existe
    qu'a l'instant de l'appel : resolu a la lecture seulement, il disparaitrait
    au releve suivant et son taux serait immesurable — or c'est le seul des
    quatre qui dise quelque chose du fournisseur plutot que de notre saisie.
    """
    _seed_event(migrated)
    respx.get(f"{BASE_URL}/leagues").mock(return_value=httpx.Response(500))

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.cause == CAUSE_UNREACHABLE
    # Rien dans `events` ni dans `competitions` ne porte cet etat : il ne se
    # relit que parce qu'on l'a ecrit.
    assert failure_causes([1], migrated) == {1: CAUSE_UNREACHABLE}


@respx.mock
async def test_un_contexte_servi_se_journalise_aussi(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le denominateur du taux. Sans les reussites, on compterait des pannes
    sans savoir sur combien d'essais."""
    _seed_event(migrated)
    _mock_all(load_fixture)

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.cause == CAUSE_SERVED
    assert failure_causes([1], migrated) == {}, "un contexte servi n'a pas de cause a nommer"


def test_les_deux_formulations_ne_se_recopient_pas() -> None:
    """**Deux lecteurs, deux redactions, un seul lexique.**

    Le bloc parle a une analyse qui va chercher : ce qui compte pour elle est
    de savoir si ce qui manque est une absence de fait ou une absence de
    collecte. L'ecran parle a qui va reparer : ce qui compte est le geste. Le
    **nom** de la cause, lui, est ecrit une seule fois — et les deux causes
    deja nommees sur la ligne `Absents` gardent leur mot exact.
    """
    assert CAUSE_LABELS[CAUSE_NOT_COVERED] == "non interrogés"
    assert CAUSE_LABELS[CAUSE_UNREACHABLE] == "source injoignable"
    for cause, nom in CAUSE_LABELS.items():
        assert nom in CAUSE_UI_NOTES[cause], "l'ecran nomme la cause puis le geste"
        assert CAUSE_UI_NOTES[cause] != CAUSE_BLOCK_NOTES[cause]
    # Les causes qui se reparent d'un geste, et elles seules. Les trois formes
    # d'une rencontre non resolue en font partie : elles precisent le geste, elles
    # ne changent pas sa nature.
    assert {CAUSE_UNMAPPED, CAUSE_UNRESOLVED} | UNRESOLVED_FORMS == COLLECTION_FAULTS
    assert CAUSE_NOT_COVERED not in COLLECTION_FAULTS, "rien a reparer : le fournisseur ne sert pas"
    assert CAUSE_UNREACHABLE not in COLLECTION_FAULTS, "rien a reparer : ca se retente"


# -- L'arbitre : trois etats, et le troisieme se mesure ----------------------


def _releves_d_arbitre(migrated: Settings, noms: list[str]) -> int:
    """Autant de releves d'arbitre que de noms, sur la competition de l'evenement.

    Un nom vide est une rencontre pour laquelle le fournisseur n'a servi aucun
    arbitre. C'est l'echantillon sur lequel le constat « cette competition n'en
    sert aucun » se prend — jamais un match seul, jamais une journee.
    """
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113",
        settings=migrated,
    )
    for index, nom in enumerate(noms, start=100):
        db.execute(
            "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, "
            "source, created_at) VALUES (?, ?, ?, ?, ?, '2026-08-03T15:30:00Z', 'api', ?)",
            (
                index,
                competition["sport_id"],
                competition["id"],
                f"A{index}",
                f"B{index}",
                db.utcnow(),
            ),
            settings=migrated,
        )
        store(index, KIND_REFEREE, {"name": nom}, migrated)
    return int(competition["id"])


def test_un_echantillon_court_ne_bascule_pas_une_competition_qui_designe(
    migrated: Settings,
) -> None:
    """**Le faux positif a eviter, et il se produirait des ce soir.**

    Trois matchs sans arbitre — un lot de Saudi Pro League — ne disent rien : la
    designation peut n'etre pas tombee. Sous l'echantillon, la ligne garde le
    libelle qui n'affirme rien.
    """
    _seed_event(migrated)
    competition_id = _releves_d_arbitre(migrated, ["", "", ""])
    store(1, KIND_REFEREE, {"name": ""}, migrated)

    lignes = dict(
        context_lines(
            1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated, competition_id
        )
    )

    assert lignes["Arbitre"] == "non encore designe"


def test_une_competition_qui_ne_sert_aucun_arbitre_le_dit(migrated: Settings) -> None:
    """**« Non encore designe » et « non servi ici » appellent des comportements
    opposes** : le premier dit d'attendre, le second dit d'aller chercher.

    Mesure du 14/08/2026 sur les 66 releves de la base : une competition sert un
    arbitre sur **toutes** ses rencontres ou sur aucune — 22/22 en Conference
    League, 0/7 en Leagues Cup — jamais partiellement. C'est l'absence qui
    demande un echantillon, et `REFEREE_MIN_SAMPLE` le fixe.
    """
    _seed_event(migrated)
    competition_id = _releves_d_arbitre(migrated, [""] * REFEREE_MIN_SAMPLE)
    store(1, KIND_REFEREE, {"name": ""}, migrated)

    lignes = dict(
        context_lines(
            1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated, competition_id
        )
    )

    assert lignes["Arbitre"] == "non servi sur cette competition"


def test_un_seul_arbitre_nomme_prouve_que_la_competition_designe(migrated: Settings) -> None:
    """La distribution est binaire : un nom quelque part suffit, et l'echantillon
    ne sert plus a rien. Ce qui reste est une designation qui n'est pas tombee."""
    _seed_event(migrated)
    competition_id = _releves_d_arbitre(migrated, ["M. Oliver"] + [""] * REFEREE_MIN_SAMPLE)
    store(1, KIND_REFEREE, {"name": ""}, migrated)

    lignes = dict(
        context_lines(
            1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated, competition_id
        )
    )

    assert lignes["Arbitre"] == "non encore designe"


def test_un_releve_anterieur_a_la_mesure_garde_le_comportement_d_avant(
    migrated: Settings,
) -> None:
    """Le drapeau manque sur les releves d'avant : il vaut « servi », donc
    l'ancien libelle, plutot qu'une affirmation qu'on ne peut pas gager."""
    _seed_event(migrated)
    store(1, KIND_REFEREE, {"name": ""}, migrated)

    assert _lines(migrated)["Arbitre"] == "non encore designe"


def test_une_division_qui_n_a_pas_commence_est_quand_meme_nommee(migrated: Settings) -> None:
    """**A la reprise, la regle du rang inversait ce que la ligne doit montrer.**

    Un tour de coupe oppose une division qui a joue et une qui n'a pas commence :
    le club de D3 sortait classe, celui de Bundesliga — 0 journee — ne sortait
    pas du tout, et le bloc opposait un rang a un silence. Le rang reste tu, il
    ne classe rien a zero match ; la division, elle, est un fait a toute date, et
    c'est elle qui porte l'ecart de niveau.

    Verifie en simulation sur le lot reel des 22-23/08.
    """
    _seed_event(migrated)
    store(
        1,
        KIND_DOMESTIC,
        {
            "home": {
                "state": DOMESTIC_RESOLVED,
                "league_id": 80,
                "label": "3. Liga",
                "season": 2026,
                "standings": True,
            },
            "away": {
                "state": DOMESTIC_RESOLVED,
                "league_id": 78,
                "label": "Bundesliga",
                "season": 2026,
                "standings": True,
            },
        },
        migrated,
    )
    store(
        1,
        KIND_STANDINGS,
        {
            "home": {"rank": 7, "points": 3, "played": 1, "diff": 1},
            "away": {"rank": 2, "points": 0, "played": 0, "diff": 0},
        },
        migrated,
    )

    ligne = dict(_lines(migrated))["Classement"]

    assert "BK Hacken (3. Liga) 7e" in ligne
    assert "Djurgardens IF (Bundesliga) — 0j jouée" in ligne
    assert "2e" not in ligne, "a zero match, le rang ne classe rien et reste tu"


def test_hors_coupe_une_division_sans_journee_ne_produit_rien(migrated: Settings) -> None:
    """La regle ne vaut que la ou les deux equipes viennent de deux competitions.

    Sur un championnat, les deux tables sont la meme : nommer la division a
    chaque ligne serait du bruit, et un classement a zero journee reste tu.
    """
    _seed_event(migrated)
    store(1, KIND_STANDINGS, {"home": {"rank": 7, "points": 0, "played": 0, "diff": 0}}, migrated)

    assert "Classement" not in dict(_lines(migrated))
