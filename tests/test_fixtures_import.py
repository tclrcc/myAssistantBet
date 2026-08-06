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
from myassistantbet.services.competitions import set_active
from myassistantbet.services.fixtures import SOURCE, import_competition

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
