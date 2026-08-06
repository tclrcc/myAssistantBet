from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services import board as board_service
from myassistantbet.services.enrich import (
    build_estimate,
    run_enrich,
)
from myassistantbet.services.markets import (
    FOOTBALL_MARKETS,
    PLAYER_PROP_MARKETS,
    TENNIS_MARKETS,
    markets_for,
)
from myassistantbet.services.scan import active_competitions, run_scan

from .helpers import NOW, QUOTA_HEADERS

EVENT_ID = "3c7f9a1b2d4e5f60718293a4b5c6d7e8"
EVENT_ODDS_URL = f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/{EVENT_ID}/odds"


async def _seed_session(client: OddsAPIClient, settings: Settings, payload: Any) -> int:
    """Scanne, puis coche le premier evenement. Renvoie l'id de session."""
    for competition in active_competitions(settings):
        key = competition["oddsapi_key"]
        respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200,
                json=payload if key == "soccer_sweden_allsvenskan" else [],
                headers=QUOTA_HEADERS,
            )
        )
    await run_scan(client, settings, now=NOW)

    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=settings)
    return board_service.toggle_selection(int(event["id"]), True, settings)


# -- Choix des marches ------------------------------------------------------


def test_marches_football(migrated: Settings) -> None:
    assert markets_for("football", "soccer_sweden_allsvenskan", migrated) == FOOTBALL_MARKETS
    assert len(FOOTBALL_MARKETS) == 14


def test_props_buteurs_seulement_sur_la_liste_blanche(migrated: Settings) -> None:
    # Allsvenskan : hors liste blanche, on ne depense pas pour des marches vides.
    hors_liste = markets_for("football", "soccer_sweden_allsvenskan", migrated)
    dans_liste = markets_for("football", "soccer_epl", migrated)

    assert not set(PLAYER_PROP_MARKETS) & set(hors_liste)
    assert set(PLAYER_PROP_MARKETS) <= set(dans_liste)
    assert len(dans_liste) == len(hors_liste) + 2


def test_liste_blanche_configurable(migrated: Settings, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(migrated, "player_props_leagues", "soccer_sweden_allsvenskan")

    assert set(PLAYER_PROP_MARKETS) <= set(
        markets_for("football", "soccer_sweden_allsvenskan", migrated)
    )
    assert not set(PLAYER_PROP_MARKETS) & set(markets_for("football", "soccer_epl", migrated))


def test_marches_tennis(migrated: Settings) -> None:
    assert markets_for("tennis", "tennis_atp_us_open", migrated) == TENNIS_MARKETS


def test_cyclisme_ne_declenche_aucun_appel(migrated: Settings) -> None:
    assert markets_for("cycling", "", migrated) == ()


# -- Estimation de cout -----------------------------------------------------


@respx.mock
async def test_estimation_un_credit_par_marche(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.events == 1
    assert estimate.cost == 14, "14 marches football, un bookmaker, donc 14 credits"
    assert estimate.remaining == 4821
    assert estimate.remaining_after == 4807
    assert estimate.allowed is True
    assert estimate.blocked_reason is None


@respx.mock
async def test_estimation_bloquee_sous_le_plancher(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('oddsapi', '/x', 2, 505, '2099-01-01T00:00:00Z')",
        settings=migrated,
    )

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.remaining == 505
    assert estimate.remaining_after == 491
    assert estimate.allowed is False
    assert "plancher" in estimate.blocked_reason


@respx.mock
async def test_un_match_commence_ne_coute_plus_rien(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Acheter les marches profonds d'un match lance, c'est bruler des credits."""
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    # Le match du fixture debute a 15h30 UTC : on se place apres le coup d'envoi.
    apres = datetime(2026, 8, 3, 16, 0, tzinfo=UTC)

    estimate = build_estimate(session_id, migrated, apres)

    assert estimate.events == 0
    assert estimate.cost == 0
    assert estimate.considered == 1, "il reste coche, il n'est simplement plus enrichissable"
    assert estimate.started == ["BK Hacken – Djurgardens IF"]
    assert estimate.allowed is False
    assert estimate.blocked_reason == (
        "Rien a enrichir : tous les matchs selectionnes ont deja commence."
    )


@respx.mock
async def test_aucun_appel_pour_un_match_commence(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    route = respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(200, json={"bookmakers": []}, headers=QUOTA_HEADERS)
    )

    report = await run_enrich(
        odds_client, session_id, migrated, now=datetime(2026, 8, 3, 16, 0, tzinfo=UTC)
    )

    assert route.call_count == 0
    assert report.cost == 0
    assert report.failures, "le refus est rapporte, pas silencieux"


def test_estimation_sans_selection(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.events == 0
    assert estimate.cost == 0
    assert estimate.allowed is False
    assert estimate.blocked_reason == "Aucun evenement selectionne."


def test_evenement_manuel_est_ignore_sans_cout(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'cycling'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Etape 12', 'Alpe d''Huez', '2026-08-03T12:00:00Z', 'manual', ?)",
        (sport["id"], db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT id FROM events", settings=migrated)
    board_service.toggle_selection(int(event["id"]), True, migrated)

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.events == 0
    assert estimate.cost == 0
    assert estimate.skipped == ["Etape 12 – Alpe d'Huez"]


def test_etape_sans_second_participant_na_pas_de_tiret_orphelin(migrated: Settings) -> None:
    """Une etape cycliste n'a pas d'adversaire : le libelle s'arrete au nom."""
    session_id = board_service.current_session(migrated)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'cycling'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Etape 12 — Pau > Hautacam', '', '2026-08-03T12:00:00Z', 'manual', ?)",
        (sport["id"], db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT id FROM events", settings=migrated)
    board_service.toggle_selection(int(event["id"]), True, migrated)

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.skipped == ["Etape 12 — Pau > Hautacam"]


# -- Execution --------------------------------------------------------------


@respx.mock
async def test_enrichissement_stocke_les_marches_profonds(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.finished is True
    assert report.failures == []
    assert report.done == 1
    assert report.results[0].markets_received == 8

    markets = {
        row["market_key"]
        for row in db.query("SELECT DISTINCT market_key FROM odds", settings=migrated)
    }
    assert {"correct_score", "btts", "team_totals", "alternate_totals_corners"} <= markets
    assert "h2h" in markets, "le releve de l'etage A est conserve"


@respx.mock
async def test_marches_demandes_a_l_api(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    route = respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(200, json={"bookmakers": []}, headers=QUOTA_HEADERS)
    )

    await run_enrich(odds_client, session_id, migrated, now=NOW)

    params = route.calls[0].request.url.params
    assert set(params["markets"].split(",")) == set(FOOTBALL_MARKETS)
    assert params["bookmakers"] == "betclic_fr"


@respx.mock
async def test_enrichissement_refuse_sous_le_plancher_sans_aucun_appel(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('oddsapi', '/x', 2, 501, '2099-01-01T00:00:00Z')",
        settings=migrated,
    )
    route = respx.get(EVENT_ODDS_URL).mock(return_value=httpx.Response(200, json={}))

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert route.call_count == 0, "aucun credit ne doit etre depense"
    assert report.finished is True
    assert "plancher" in report.failures[0].error


@respx.mock
async def test_un_evenement_en_echec_n_interrompt_pas_les_autres(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    # On coche aussi le second match du scan.
    second = db.query_one("SELECT id FROM events WHERE home = 'IFK Norrkoping'", settings=migrated)
    board_service.toggle_selection(int(second["id"]), True, migrated)

    respx.get(EVENT_ODDS_URL).mock(return_value=httpx.Response(503, text="indisponible"))
    respx.get(
        f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/9f8e7d6c5b4a39281706f5e4d3c2b1a0/odds"
    ).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.done == 2
    assert len(report.failures) == 1
    assert report.failures[0].label == "BK Hacken – Djurgardens IF"


@respx.mock
async def test_progression_rapportee_a_chaque_etape(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )

    steps: list[tuple[int, int, bool]] = []
    await run_enrich(
        odds_client,
        session_id,
        migrated,
        on_progress=lambda report: steps.append((report.done, report.percent, report.finished)),
        now=NOW,
    )

    assert steps == [(1, 100, False), (1, 100, True)]


@respx.mock
async def test_enrichissement_idempotent(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )

    await run_enrich(odds_client, session_id, migrated, now=NOW)
    first = len(db.query("SELECT id FROM odds", settings=migrated))
    await run_enrich(odds_client, session_id, migrated, now=NOW)
    second = len(db.query("SELECT id FROM odds", settings=migrated))

    assert first == second


@respx.mock
async def test_reponse_inexploitable_ne_tue_pas_l_enrichissement(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une reponse de forme imprevue est signalee, jamais propagee en exception."""
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(200, json=["forme", "inattendue"], headers=QUOTA_HEADERS)
    )

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.finished is True
    assert len(report.failures) == 1
    assert "inexploitable" in report.failures[0].error


# -- Orchestration avec le contexte sportif ---------------------------------


@respx.mock
async def test_enrichissement_recupere_aussi_le_contexte(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    from myassistantbet.providers.apifootball import APIFootballClient

    from .test_context import _mock_all

    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    _mock_all(load_fixture)

    report = await run_enrich(
        odds_client,
        session_id,
        migrated,
        context_client=APIFootballClient(http_client, migrated),
        now=NOW,
    )

    result = report.results[0]
    assert result.ok is True
    assert result.mapping_pending is False
    assert {"standings", "form", "injuries", "h2h"} <= set(result.context_kinds)
    assert result.context_note == ""


@respx.mock
async def test_contexte_absent_n_empeche_pas_les_cotes(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """API-Football hors service : les cotes doivent quand meme etre recuperees."""
    from myassistantbet.providers.apifootball import BASE_URL as AF_BASE
    from myassistantbet.providers.apifootball import APIFootballClient

    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    respx.get(url__startswith=AF_BASE).mock(return_value=httpx.Response(503, text="HS"))

    report = await run_enrich(
        odds_client,
        session_id,
        migrated,
        context_client=APIFootballClient(http_client, migrated),
        now=NOW,
    )

    assert report.results[0].ok is True, "l'echec du contexte ne fait pas echouer le match"
    assert report.results[0].odds_rows > 0
    assert report.context_notes, "le manque est signale visiblement, pas tu"


@respx.mock
async def test_sans_client_de_contexte_l_enrichissement_reste_possible(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.results[0].ok is True
    assert report.results[0].context_kinds == []


# -- Tennis -----------------------------------------------------------------


def _tennis_event(settings: Settings) -> int:
    """Un match de tennis rattache a une competition Odds API active."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=settings,
    )
    db.execute(
        "UPDATE competitions SET active = 1 WHERE id = ?", (competition["id"],), settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 'evt-tennis', 'Alcaraz', 'Sinner', "
        "'2026-08-04T17:00:00Z', 'api', ?)",
        (competition["sport_id"], competition["id"], db.utcnow()),
        settings=settings,
    )
    row = db.query_one(
        "SELECT id FROM events WHERE oddsapi_event_id = 'evt-tennis'", settings=settings
    )
    return int(row["id"])


def test_estimation_tennis_huit_marches(migrated: Settings) -> None:
    event_id = _tennis_event(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)

    estimate = build_estimate(session_id, migrated, NOW)

    assert estimate.events == 1
    assert estimate.cost == 8, "8 marches tennis, un bookmaker, donc 8 credits"
    assert estimate.targets[0].markets == TENNIS_MARKETS


@respx.mock
async def test_etage_b_tennis(odds_client: OddsAPIClient, migrated: Settings) -> None:
    event_id = _tennis_event(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    route = respx.get(f"{BASE_URL}/sports/tennis_atp_us_open/events/evt-tennis/odds").mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "evt-tennis",
                "bookmakers": [
                    {
                        "key": "betclic_fr",
                        "last_update": "2026-08-04T06:12:00Z",
                        "markets": [
                            {
                                "key": "h2h",
                                "outcomes": [
                                    {"name": "Alcaraz", "price": 1.4},
                                    {"name": "Sinner", "price": 2.95},
                                ],
                            },
                            {
                                "key": "totals_s1",
                                "outcomes": [
                                    {"name": "Over", "price": 1.85, "point": 9.5},
                                    {"name": "Under", "price": 1.95, "point": 9.5},
                                ],
                            },
                        ],
                    }
                ],
            },
            headers=QUOTA_HEADERS,
        )
    )

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.failures == []
    assert set(route.calls[0].request.url.params["markets"].split(",")) == set(TENNIS_MARKETS)
    markets = {
        row["market_key"]
        for row in db.query("SELECT DISTINCT market_key FROM odds", settings=migrated)
    }
    assert markets == {"h2h", "totals_s1"}


@respx.mock
async def test_l_enrichissement_recupere_aussi_le_dossier_d_equipe(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Le dossier entre dans le parcours normal : cocher, enrichir, generer. Une
    donnee qui vaut pour une equipe et non pour une rencontre y arrive comme le
    reste, et sa ligne part dans le prompt sans autre geste."""
    from myassistantbet.providers.apifootball import APIFootballClient
    from myassistantbet.services.dossier import KIND_COACH
    from myassistantbet.services.session import renderable_events

    from .test_context import _mock_all

    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    _mock_all(load_fixture)
    # Le quota simule des fixtures est celui d'un plan d'essai (82 restants) :
    # sous le plancher par defaut, le dossier serait suspendu a juste titre.
    permissif = migrated.model_copy(update={"apifootball_call_floor": 50})

    report = await run_enrich(
        odds_client,
        session_id,
        permissif,
        context_client=APIFootballClient(http_client, permissif),
        now=NOW,
    )

    result = report.results[0]
    assert KIND_COACH in result.context_kinds
    assert result.dossier_note == ""
    lignes = dict(renderable_events(session_id, permissif, NOW)[0].context_lines)
    assert lignes["Entraineur"] == (
        "BK Hacken P. Gustafsson (depuis 06/2023, 3 ans) | "
        "Djurgardens IF M. Lindqvist (depuis 06/2026, 1 mois)"
    )


@respx.mock
async def test_un_plancher_d_appels_franchi_ne_dit_pas_le_contexte_partiel(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Deux causes distinctes, deux mentions distinctes. Le contexte de ce match
    est complet ; c'est le dossier qui n'est pas parti, faute de quota. L'annoncer
    comme un « contexte partiel » enverrait chercher un probleme de rapprochement
    la ou il n'y a qu'un compteur bas — et le taire serait pire."""
    from myassistantbet.providers.apifootball import APIFootballClient

    from .test_context import _mock_all

    session_id = await _seed_session(
        odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json")
    )
    respx.get(EVENT_ODDS_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    routes = _mock_all(load_fixture)

    report = await run_enrich(
        odds_client,
        session_id,
        migrated,
        context_client=APIFootballClient(http_client, migrated),
        now=NOW,
    )

    result = report.results[0]
    assert routes["coachs_home"].call_count == 0, "le plancher a retenu l'appel"
    assert result.context_note == "", "le contexte, lui, est complet"
    assert "plancher" in result.dossier_note
    assert result.notes == [result.dossier_note], "et l'UI le liste quand meme"
