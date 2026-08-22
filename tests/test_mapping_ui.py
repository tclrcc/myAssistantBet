from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services.context import (
    CAUSE_FIXTURE_ABSENT,
    CAUSE_REPAIRS,
    KIND_MAPPING,
)
from myassistantbet.services.mapping_ui import pending_count, pending_events, resolve_manually
from myassistantbet.services.matching import lookup_alias

CANDIDATES = [
    {"id": 376, "name": "BK Hacken", "score": 0.62},
    {"id": 379, "name": "Hammarby", "score": 0.41},
]


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _seed_pending(settings: Settings, *, both: bool = False) -> int:
    """Cree un evenement en attente de resolution, avec ses candidats memorises."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at, mapping_pending) "
        "VALUES (?, ?, 'evt-1', 'Racing de Nulle Part', 'Djurgardens IF', "
        "'2026-08-03T15:30:00Z', 'api', ?, 1)",
        (competition["sport_id"], competition["id"], db.utcnow()),
        settings=settings,
    )
    event_id = int(db.query_one("SELECT id FROM events", settings=settings)["id"])
    payload = {
        "reason": "correspondance incertaine",
        "teams": [
            {"oddsapi_name": "Racing de Nulle Part", "resolved": False, "candidates": CANDIDATES},
            {
                "oddsapi_name": "Djurgardens IF",
                "resolved": not both,
                "candidates": CANDIDATES if both else [],
            },
        ],
    }
    db.execute(
        "INSERT INTO context (event_id, kind, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
        (event_id, KIND_MAPPING, json.dumps(payload), db.utcnow()),
        settings=settings,
    )
    return event_id


# -- Lecture ----------------------------------------------------------------


def test_liste_des_evenements_en_attente(migrated: Settings) -> None:
    _seed_pending(migrated)

    events = pending_events(migrated)

    assert len(events) == 1
    assert events[0].affiche == "Racing de Nulle Part – Djurgardens IF"
    assert events[0].competition == "Allsvenskan"
    assert [team.oddsapi_name for team in events[0].unresolved] == ["Racing de Nulle Part"]
    assert pending_count(migrated) == 1


def test_aucun_evenement_en_attente(migrated: Settings) -> None:
    assert pending_events(migrated) == []
    assert pending_count(migrated) == 0


# -- Resolution -------------------------------------------------------------


def test_resolution_manuelle_memorise_l_alias_et_leve_le_drapeau(migrated: Settings) -> None:
    event_id = _seed_pending(migrated)

    resolu = resolve_manually(event_id, {"Racing de Nulle Part": (376, "BK Hacken")}, migrated)

    assert resolu is True
    alias = lookup_alias("Racing de Nulle Part", migrated)
    assert alias.apifootball_id == 376
    event = db.query_one("SELECT mapping_pending FROM events", settings=migrated)
    assert event["mapping_pending"] == 0
    assert pending_count(migrated) == 0


def test_resolution_partielle_laisse_l_evenement_en_attente(migrated: Settings) -> None:
    event_id = _seed_pending(migrated, both=True)

    resolu = resolve_manually(event_id, {"Racing de Nulle Part": (376, "BK Hacken")}, migrated)

    assert resolu is False
    assert pending_count(migrated) == 1
    assert lookup_alias("Racing de Nulle Part", migrated) is not None


def test_resolution_sans_choix_ne_fait_rien(migrated: Settings) -> None:
    event_id = _seed_pending(migrated)

    assert resolve_manually(event_id, {}, migrated) is False
    assert pending_count(migrated) == 1


def test_le_choix_manuel_est_marque_comme_tel(migrated: Settings) -> None:
    event_id = _seed_pending(migrated)

    resolve_manually(event_id, {"Racing de Nulle Part": (376, "BK Hacken")}, migrated)

    row = db.query_one("SELECT source FROM team_aliases", settings=migrated)
    assert row["source"] == "manual"


# -- Routes -----------------------------------------------------------------


def test_page_mapping(client: TestClient, isolated_settings: Settings) -> None:
    _seed_pending(isolated_settings)

    response = client.get("/mapping")

    assert response.status_code == 200
    assert "Racing de Nulle Part – Djurgardens IF" in response.text
    assert "BK Hacken (62 %)" in response.text
    assert "Hammarby" in response.text


def test_page_mapping_vide(client: TestClient) -> None:
    assert "Aucune correspondance en attente" in client.get("/mapping").text


def test_resolution_via_le_formulaire(client: TestClient, isolated_settings: Settings) -> None:
    event_id = _seed_pending(isolated_settings)

    response = client.post(
        f"/mapping/{event_id}", data={"choice": "Racing de Nulle Part|376|BK Hacken"}
    )

    assert response.status_code == 200
    assert "Aucune correspondance en attente" in response.text
    assert lookup_alias("Racing de Nulle Part", isolated_settings).apifootball_id == 376


def test_choix_vide_ignore(client: TestClient, isolated_settings: Settings) -> None:
    event_id = _seed_pending(isolated_settings)

    response = client.post(f"/mapping/{event_id}", data={"choice": ""})

    assert response.status_code == 200
    assert pending_count(isolated_settings) == 1


def test_choix_malforme_ignore(client: TestClient, isolated_settings: Settings) -> None:
    event_id = _seed_pending(isolated_settings)

    response = client.post(f"/mapping/{event_id}", data={"choice": "n_importe_quoi"})

    assert response.status_code == 200
    assert pending_count(isolated_settings) == 1


def test_le_bandeau_signale_le_travail_en_attente(
    client: TestClient, isolated_settings: Settings
) -> None:
    _seed_pending(isolated_settings)

    response = client.get("/")

    assert "1 à résoudre" in response.text
    assert 'href="/mapping"' in response.text


def test_le_bandeau_reste_muet_sans_mapping_en_attente(client: TestClient) -> None:
    assert "à résoudre" not in client.get("/").text


def _seed_sans_rien_a_trancher(settings: Settings) -> int:
    """Les deux equipes appariees, et aucune rencontre qui les oppose.

    C'est l'etat reel de quatre matchs le 22/08/2026 : trois de Premiership
    ecossaise reprogrammes au 15/09, un reporte. L'evenement reste
    `mapping_pending` et il n'y a **aucun nom a saisir**.
    """
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at, mapping_pending) "
        "VALUES (?, ?, 'evt-2', 'Motherwell', 'Aberdeen', "
        "'2026-08-22T14:00:00Z', 'api', ?, 1)",
        (competition["sport_id"], competition["id"], db.utcnow()),
        settings=settings,
    )
    event_id = int(db.query_one("SELECT id FROM events ORDER BY id DESC", settings=settings)["id"])
    payload = {
        "reason": "aucun match ne reunit ces deux equipes",
        "teams": [
            {"oddsapi_name": "Motherwell", "resolved": True, "candidates": []},
            {"oddsapi_name": "Aberdeen", "resolved": True, "candidates": []},
        ],
    }
    db.execute(
        "INSERT INTO context (event_id, kind, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
        (event_id, KIND_MAPPING, json.dumps(payload), db.utcnow()),
        settings=settings,
    )
    return event_id


def test_un_evenement_sans_nom_a_trancher_ne_propose_aucun_bouton(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Un bouton qui n'ecrit rien est pire qu'un bouton absent.**

    Les deux equipes etant appariees, le formulaire ne portait aucun champ :
    l'envoi passait par `resolve_manually` avec un choix vide, qui rend `False`
    sans rien ecrire, et la page se rerendait a l'identique. L'echec et le cas
    ordinaire rendaient la meme chose, sur le seul ecran cense debloquer.
    """
    _seed_sans_rien_a_trancher(isolated_settings)

    page = client.get("/mapping").text

    assert "Motherwell – Aberdeen" in page
    assert "aucun match ne reunit ces deux equipes" in page
    assert "<form" not in page, "aucun formulaire quand il n'y a rien a saisir"
    assert CAUSE_REPAIRS[CAUSE_FIXTURE_ABSENT] in page, "l'ecran doit nommer le geste utile"


def test_le_formulaire_reste_la_ou_il_y_a_un_nom_a_trancher(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le garde-fou ne doit pas emporter le cas pour lequel cet ecran existe."""
    _seed_pending(isolated_settings)

    page = client.get("/mapping").text

    assert "<form" in page
    assert "BK Hacken (62 %)" in page
