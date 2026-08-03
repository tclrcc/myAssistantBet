from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet.main import app


@pytest.fixture
def client() -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_health_repond_200(client: TestClient) -> None:
    response = client.get("/health")

    assert response.status_code == 200
    payload = response.json()
    assert payload["status"] == "ok"
    assert payload["version"]


def test_health_expose_l_etat_de_la_base(client: TestClient) -> None:
    db_state = client.get("/health").json()["db"]

    assert db_state["ok"] is True
    assert db_state["schema_version"] == 2
    assert db_state["journal_mode"] == "wal"
    assert "events" in db_state["tables"]
    assert "odds" in db_state["tables"]


def test_health_expose_la_config_sans_secret(client: TestClient) -> None:
    response = client.get("/health")
    config = response.json()["config"]

    assert config["tz"] == "Europe/Paris"
    assert config["odds_api_credit_floor"] == 500
    assert config["odds_api_key_present"] is True
    assert config["apifootball_key_present"] is False
    assert "cle-odds-de-test" not in response.text


def test_le_demarrage_applique_les_migrations(client: TestClient) -> None:
    # Le lifespan a tourne a l'ouverture du TestClient : la base doit etre complete.
    assert len(client.get("/health").json()["db"]["tables"]) == 11
