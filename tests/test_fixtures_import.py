"""Import de matchs depuis API-Football, pour ce que The Odds API ne sert pas."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.providers.apifootball import BASE_URL, APIFootballClient
from myassistantbet.providers.oddsapi import OddsAPIClient
from myassistantbet.services.competitions import set_active
from myassistantbet.services.enrich import build_estimate, run_enrich
from myassistantbet.services.fixtures import (
    SOURCE,
    _book_key,
    import_competition,
    import_odds,
)
from myassistantbet.services.labels import bookmaker_label

NOW = datetime(2026, 8, 6, 9, 0, tzinfo=UTC)

LEAGUES = {
    "errors": [],
    "response": [
        {
            "league": {"id": 3, "name": "UEFA Europa League", "type": "Cup"},
            "seasons": [{"year": 2026, "current": True}],
        }
    ],
}

FIXTURES = {
    "errors": [],
    "response": [
        {
            "fixture": {"id": 900001, "date": "2026-08-06T18:00:00+00:00"},
            "teams": {"home": {"id": 1, "name": "KuPS"}, "away": {"id": 2, "name": "U Craiova"}},
        },
        {
            "fixture": {"id": 900002, "date": "2026-08-07T19:00:00+00:00"},
            "teams": {
                "home": {"id": 3, "name": "Rangers"},
                "away": {"id": 4, "name": "Jagiellonia"},
            },
        },
        # Hors fenetre : la plage du fournisseur est en jours pleins.
        {
            "fixture": {"id": 900003, "date": "2026-08-09T19:00:00+00:00"},
            "teams": {"home": {"id": 5, "name": "Lech"}, "away": {"id": 6, "name": "KI"}},
        },
    ],
}


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _europa(settings: Settings) -> int:
    """La Ligue Europa : rattachee a API-Football, non servie par The Odds API."""
    row = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'soccer_uefa_europa_league'",
        settings=settings,
    )
    if row is None:
        sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
        db.execute(
            "INSERT INTO competitions (sport_id, oddsapi_key, apifootball_league_id, label, "
            "priority, active, api_active) VALUES (?, 'soccer_uefa_europa_league', 3, "
            "'UEFA Europa League', 0, 1, 0)",
            (sport["id"],),
            settings=settings,
        )
        row = db.query_one(
            "SELECT id FROM competitions WHERE oddsapi_key = 'soccer_uefa_europa_league'",
            settings=settings,
        )
    competition_id = int(row["id"])
    set_active(competition_id, True, settings)
    db.execute(
        "UPDATE competitions SET api_active = 0, apifootball_league_id = 3 WHERE id = ?",
        (competition_id,),
        settings=settings,
    )
    return competition_id


def _mock_api() -> None:
    respx.get(f"{BASE_URL}/leagues").mock(return_value=httpx.Response(200, json=LEAGUES))
    respx.get(f"{BASE_URL}/fixtures").mock(return_value=httpx.Response(200, json=FIXTURES))


@respx.mock
async def test_les_matchs_absents_des_cotes_entrent_par_api_football(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """The Odds API ne sert aucun evenement pour les tours preliminaires ; le
    fournisseur de contexte, lui, les connait."""
    competition_id = _europa(migrated)
    _mock_api()

    report = await import_competition(
        APIFootballClient(http_client, migrated), competition_id, migrated, now=NOW
    )

    assert report.created == 2, "les deux matchs de la fenetre, pas celui du 9"
    rows = db.query(
        "SELECT home, away, source FROM events ORDER BY commence_time", settings=migrated
    )
    assert [row["home"] for row in rows] == ["KuPS", "Rangers"]
    assert all(row["source"] == SOURCE for row in rows), "la provenance explique l'absence de cotes"


@respx.mock
async def test_import_idempotent(http_client: httpx.AsyncClient, migrated: Settings) -> None:
    """Relancer un import ne duplique rien : cle naturelle sur le fixture."""
    competition_id = _europa(migrated)
    _mock_api()
    client = APIFootballClient(http_client, migrated)

    await import_competition(client, competition_id, migrated, now=NOW)
    second = await import_competition(client, competition_id, migrated, now=NOW)

    assert second.created == 0
    assert second.updated == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM events", settings=migrated)["n"] == 2


@respx.mock
async def test_une_competition_servie_par_les_cotes_est_refusee(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Importer par-dessus le scan creerait deux fois le meme match, sous deux
    orthographes que rien ne saurait rapprocher."""
    competition_id = _europa(migrated)
    db.execute(
        "UPDATE competitions SET api_active = 1 WHERE id = ?", (competition_id,), settings=migrated
    )
    _mock_api()

    report = await import_competition(
        APIFootballClient(http_client, migrated), competition_id, migrated, now=NOW
    )

    assert report.served_elsewhere is True
    assert report.created == 0
    assert "doublons" in report.note
    assert db.query_one("SELECT COUNT(*) AS n FROM events", settings=migrated)["n"] == 0


@respx.mock
async def test_une_competition_sans_ligue_rattachee_le_dit(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Jamais de silence : sans rattachement, l'import n'a rien pour travailler."""
    competition_id = _europa(migrated)
    db.execute(
        "UPDATE competitions SET apifootball_league_id = NULL WHERE id = ?",
        (competition_id,),
        settings=migrated,
    )

    report = await import_competition(
        APIFootballClient(http_client, migrated), competition_id, migrated, now=NOW
    )

    assert report.error is not None
    assert "ligue" in report.note


@respx.mock
def test_import_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    competition_id = _europa(isolated_settings)
    _mock_api()

    response = client.post(f"/competitions/{competition_id}/fixtures")

    assert response.status_code == 200
    assert "<html" not in response.text, "une route ciblee par HTMX rend le fragment"
    assert "Import des matchs" in response.text


# -- Cotes de substitution ----------------------------------------------------

ODDS = {
    "errors": [],
    "response": [
        {
            "bookmakers": [
                {
                    "id": 11,
                    "name": "1xBet",
                    "bets": [
                        {
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "9.99"},
                                {"value": "Away", "odd": "9.99"},
                            ],
                        }
                    ],
                },
                {
                    "id": 21,
                    "name": "888Sport",
                    "bets": [
                        {
                            "name": "Match Winner",
                            "values": [
                                {"value": "Home", "odd": "1.85"},
                                {"value": "Draw", "odd": "3.40"},
                                {"value": "Away", "odd": "4.20"},
                            ],
                        },
                        {
                            "name": "Goals Over/Under",
                            "values": [
                                {"value": "Over 2.5", "odd": "2.05"},
                                {"value": "Under 2.5", "odd": "1.75"},
                            ],
                        },
                        {"name": "Marche inconnu", "values": [{"value": "X", "odd": "1.50"}]},
                    ],
                },
            ]
        }
    ],
}


def _event_avec_fixture(settings: Settings) -> int:
    competition_id = _europa(settings)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, apifootball_fixture_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 900001, 'KuPS', 'U Craiova', "
        "'2026-08-06T18:00:00Z', ?, ?)",
        (sport["id"], competition_id, SOURCE, db.utcnow()),
        settings=settings,
    )
    row = db.query_one(
        "SELECT id FROM events WHERE apifootball_fixture_id = 900001", settings=settings
    )
    return int(row["id"])


@respx.mock
async def test_le_book_le_plus_proche_de_betclic_est_prefere(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Prendre le premier book venu ferait passer pour jouable un prix dont
    l'ecart a Betclic n'a jamais ete mesure. L'ordre de preference decide."""
    event_id = _event_avec_fixture(migrated)
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=ODDS))

    report = await import_odds(APIFootballClient(http_client, migrated), event_id, migrated)

    assert report.bookmaker == "888Sport", "1xBet est present mais bien plus loin de Betclic"
    prix = {
        row["outcome_name"]: row["price"]
        for row in db.query(
            "SELECT outcome_name, price FROM odds WHERE event_id = ? AND market_key = 'h2h'",
            (event_id,),
            settings=migrated,
        )
    }
    assert prix == {"KuPS": 1.85, "Draw": 3.40, "U Craiova": 4.20}


@respx.mock
async def test_les_issues_sont_traduites_vers_le_format_de_l_application(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Le fournisseur ecrit « Over 2.5 » la ou l'app stocke un nom et une ligne
    separee : sans traduction, ces cotes ne rejoindraient jamais celles de
    The Odds API dans le rendu."""
    event_id = _event_avec_fixture(migrated)
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=ODDS))

    await import_odds(APIFootballClient(http_client, migrated), event_id, migrated)

    totals = db.query(
        "SELECT outcome_name, point, price FROM odds WHERE event_id = ? AND market_key = 'totals' "
        "ORDER BY outcome_name",
        (event_id,),
        settings=migrated,
    )
    assert [(r["outcome_name"], r["point"]) for r in totals] == [("Over", 2.5), ("Under", 2.5)]


@respx.mock
async def test_un_marche_non_modelise_est_compte_et_annonce(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    event_id = _event_avec_fixture(migrated)
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=ODDS))

    report = await import_odds(APIFootballClient(http_client, migrated), event_id, migrated)

    assert report.ignored == 1
    assert "non modelise" in report.note


@respx.mock
async def test_aucun_book_retenu_ne_sert_le_match(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Une absence constatee est une information : pas de repli sur un book
    quelconque, qui ferait passer pour jouable un prix jamais mesure."""
    event_id = _event_avec_fixture(migrated)
    sans = {"errors": [], "response": [{"bookmakers": [ODDS["response"][0]["bookmakers"][0]]}]}
    migrated.apifootball_bookmakers = "888Sport,BetVictor"
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=sans))

    report = await import_odds(APIFootballClient(http_client, migrated), event_id, migrated)

    assert report.outcomes == 0
    assert "aucune cote" in report.note
    assert db.query_one("SELECT COUNT(*) AS n FROM odds", settings=migrated)["n"] == 0


@respx.mock
async def test_le_releve_n_ecrase_jamais_une_cote_d_un_autre_book(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    """Relancer remplace le seul book releve : ni Betclic ni la saisie manuelle."""
    event_id = _event_avec_fixture(migrated)
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
        "VALUES (?, 'manual', 'outright', 'Qualification', 1.44, ?)",
        (event_id, db.utcnow()),
        settings=migrated,
    )
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=ODDS))
    client = APIFootballClient(http_client, migrated)

    await import_odds(client, event_id, migrated)
    await import_odds(client, event_id, migrated)

    books = db.query(
        "SELECT bookmaker, COUNT(*) AS n FROM odds WHERE event_id = ? GROUP BY bookmaker",
        (event_id,),
        settings=migrated,
    )
    compte = {row["bookmaker"]: row["n"] for row in books}
    assert compte["manual"] == 1, "la saisie manuelle survit au relevé"
    assert compte["888sport"] == 5, "5 issues, pas 10 : le relevé remplace, il n'ajoute pas"


@respx.mock
async def test_un_match_sans_fixture_rattache_le_dit(
    http_client: httpx.AsyncClient, migrated: Settings
) -> None:
    competition_id = _europa(migrated)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, 'A', 'B', '2026-08-06T18:00:00Z', 'api', ?)",
        (sport["id"], competition_id, db.utcnow()),
        settings=migrated,
    )
    row = db.query_one("SELECT id FROM events WHERE home = 'A'", settings=migrated)

    report = await import_odds(APIFootballClient(http_client, migrated), int(row["id"]), migrated)

    assert report.error is not None
    assert "rattache" in report.note


def test_les_substituts_portent_le_marqueur_de_reference() -> None:
    """Betclic n'est pas au catalogue d'API-Football : ces prix ne sont jamais
    jouables tels quels, et le suffixe le dit jusque dans le prompt."""
    for name in Settings().apifootball_books:
        libelle = bookmaker_label(_book_key(name))
        assert libelle.endswith("(ref.)"), f"{name} rendu « {libelle} » sans marqueur"


# -- L'enrichissement prend en charge ces matchs ------------------------------


def _shortlist(settings: Settings, event_id: int) -> int:
    """Met un evenement dans une session, comme le ferait une coche du board."""
    db.execute("INSERT INTO sessions (created_at) VALUES (?)", (db.utcnow(),), settings=settings)
    session = db.query_one("SELECT id FROM sessions ORDER BY id DESC LIMIT 1", settings=settings)
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
        (int(session["id"]), event_id),
        settings=settings,
    )
    return int(session["id"])


def test_un_match_hors_odds_api_devient_un_substitut_et_non_un_ecarte(
    migrated: Settings,
) -> None:
    """Sans cet aiguillage, une shortlist entiere de qualifs Europa produisait
    un prompt vide : « aucun de ces evenements n'est servi par l'API »."""
    event_id = _event_avec_fixture(migrated)
    session_id = _shortlist(migrated, event_id)

    estimate = build_estimate(session_id, migrated, now=NOW)

    assert estimate.skipped == []
    assert [t.label for t in estimate.substitutes] == ["KuPS – U Craiova"]
    assert estimate.cost == 0, "un releve de substitution ne coute aucun credit Odds API"


def test_une_selection_de_substituts_n_est_pas_bloquee(migrated: Settings) -> None:
    """Le garde-fou de credit porte sur ce qui s'achete. Bloquer un relevé
    gratuit parce que le quota est bas serait un refus sans objet."""
    event_id = _event_avec_fixture(migrated)
    session_id = _shortlist(migrated, event_id)

    estimate = build_estimate(session_id, migrated, now=NOW)
    estimate.remaining = 0

    assert estimate.allowed is True
    assert estimate.blocked_reason is None


@respx.mock
async def test_enrichir_releve_les_cotes_et_le_contexte_d_un_substitut(
    http_client: httpx.AsyncClient, odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Le parcours normal : cocher, enrichir, generer — sans passer match par
    match sur la fiche."""
    event_id = _event_avec_fixture(migrated)
    session_id = _shortlist(migrated, event_id)
    respx.get(f"{BASE_URL}/odds").mock(return_value=httpx.Response(200, json=ODDS))
    respx.get(f"{BASE_URL}/leagues").mock(return_value=httpx.Response(200, json=LEAGUES))
    respx.get(f"{BASE_URL}/fixtures").mock(return_value=httpx.Response(200, json=FIXTURES))
    respx.get(url__regex=rf"{BASE_URL}/.*").mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []})
    )

    report = await run_enrich(
        odds_client,
        session_id,
        migrated,
        context_client=APIFootballClient(http_client, migrated),
        now=NOW,
    )

    resultat = report.results[0]
    assert resultat.odds_rows == 5
    assert resultat.substitute_book == "888Sport"
    assert (
        db.query_one(
            "SELECT COUNT(*) AS n FROM odds WHERE event_id = ?", (event_id,), settings=migrated
        )["n"]
        == 5
    )


@respx.mock
async def test_sans_client_api_football_le_substitut_le_dit(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Jamais de silence : un match qui ne peut rien recevoir doit le porter."""
    event_id = _event_avec_fixture(migrated)
    session_id = _shortlist(migrated, event_id)

    report = await run_enrich(odds_client, session_id, migrated, now=NOW)

    assert report.results[0].error is not None
    assert "API-Football" in report.results[0].error
