from __future__ import annotations

import json
from collections.abc import Iterator
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import session as session_service
from myassistantbet.services.manual import (
    ManualError,
    build,
    parse_links,
    parse_odds,
    save,
)
from myassistantbet.services.prompt import build_prompt

PARIS = ZoneInfo("Europe/Paris")


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _etape(settings: Settings, **overrides: str) -> int:
    """Cree une etape de cyclisme complete et renvoie son id."""
    values = {
        "sport_key": "cycling",
        "competition": "Tour de France",
        "home": "Étape 12 — Briançon > Alpe d'Huez",
        "away": "",
        "date_value": "2026-08-04",
        "time_value": "13:15",
        "odds_raw": "Pogacar 2.50\nVingegaard 3.00\nEvenepoel 7.50",
        "links_raw": "https://www.procyclingstats.com/race/tdf/stage-12",
        "notes": "Vent de face dans la vallee",
        "profile": "Haute montagne, 4 cols, arrivee au sommet 13.8 km a 8.1 %",
        "startlist": "Pogacar, Vingegaard, Evenepoel, Roglic",
    }
    values.update(overrides)
    return save(build(settings=settings, **values), settings)


# -- Lecture des cotes saisies ----------------------------------------------


@pytest.mark.parametrize(
    "line",
    ["Pogacar 2.50", "Pogacar = 2.50", "Pogacar; 2,50", "  Pogacar   2.50  "],
)
def test_formats_de_cote_acceptes(line: str) -> None:
    parsed = parse_odds(line)

    assert parsed.outcomes == [("Pogacar", 2.5)]
    assert parsed.rejected == []


def test_nom_a_plusieurs_mots() -> None:
    assert parse_odds("Remco Evenepoel 7.5").outcomes == [("Remco Evenepoel", 7.5)]


def test_ligne_illisible_est_signalee_pas_ignoree() -> None:
    parsed = parse_odds("Pogacar 2.50\nn'importe quoi\nVingegaard 3.00")

    assert [name for name, _ in parsed.outcomes] == ["Pogacar", "Vingegaard"]
    assert parsed.rejected == ["n'importe quoi"]


def test_cote_inferieure_ou_egale_a_un_refusee() -> None:
    parsed = parse_odds("Pogacar 1.00\nVingegaard 0.5")

    assert parsed.outcomes == []
    assert len(parsed.rejected) == 2


def test_lignes_vides_ignorees_silencieusement() -> None:
    parsed = parse_odds("\n\nPogacar 2.50\n\n")

    assert parsed.outcomes == [("Pogacar", 2.5)]
    assert parsed.rejected == []


def test_liens_seules_les_urls() -> None:
    assert parse_links("https://a.fr\npas une url\nhttp://b.fr\n") == [
        "https://a.fr",
        "http://b.fr",
    ]


# -- Validation -------------------------------------------------------------


def test_participant_obligatoire(migrated: Settings) -> None:
    with pytest.raises(ManualError, match="obligatoire"):
        build("cycling", "Tour", "", "", "2026-08-04", "13:15", "", "", "", settings=migrated)


def test_second_participant_obligatoire_hors_cyclisme(migrated: Settings) -> None:
    with pytest.raises(ManualError, match="obligatoire"):
        build(
            "tennis", "ATP 250", "Moutet", "", "2026-08-04", "13:15", "", "", "", settings=migrated
        )


def test_second_participant_facultatif_en_cyclisme(migrated: Settings) -> None:
    event = build(
        "cycling", "Tour", "Étape 12", "", "2026-08-04", "13:15", "", "", "", settings=migrated
    )

    assert event.away == ""


def test_date_invalide(migrated: Settings) -> None:
    with pytest.raises(ManualError, match="Date ou heure invalide"):
        build("tennis", "ATP", "A", "B", "hier", "13:15", "", "", "", settings=migrated)


def test_sport_inconnu(migrated: Settings) -> None:
    with pytest.raises(ManualError, match="Sport inconnu"):
        build("petanque", "", "A", "B", "2026-08-04", "13:15", "", "", "", settings=migrated)


def test_competition_par_defaut(migrated: Settings) -> None:
    event = build(
        "cycling", "  ", "Étape 12", "", "2026-08-04", "13:15", "", "", "", settings=migrated
    )

    assert event.competition == "Saisie manuelle"


# -- Enregistrement ---------------------------------------------------------


def test_evenement_enregistre_en_utc(migrated: Settings) -> None:
    event_id = _etape(migrated)

    row = db.query_one("SELECT * FROM events WHERE id = ?", (event_id,), settings=migrated)
    # 13:15 a Paris en aout (UTC+2) = 11:15 UTC.
    assert row["commence_time"] == "2026-08-04T11:15:00Z"
    assert row["source"] == "manual"
    assert row["oddsapi_event_id"] is None


def test_cotes_enregistrees_avec_le_bookmaker_manuel(migrated: Settings) -> None:
    event_id = _etape(migrated)

    rows = db.query(
        "SELECT * FROM odds WHERE event_id = ? ORDER BY price", (event_id,), settings=migrated
    )
    assert [row["outcome_name"] for row in rows] == ["Pogacar", "Vingegaard", "Evenepoel"]
    assert {row["bookmaker"] for row in rows} == {"manual"}
    assert {row["market_key"] for row in rows} == {"outright"}


def test_competition_manuelle_creee_puis_reutilisee(migrated: Settings) -> None:
    _etape(migrated)
    _etape(migrated, home="Étape 13")

    rows = db.query("SELECT * FROM competitions WHERE label = 'Tour de France'", settings=migrated)
    assert len(rows) == 1, "la course n'est creee qu'une fois"
    assert rows[0]["oddsapi_key"] is None


def test_references_et_profil_persistes(migrated: Settings) -> None:
    event_id = _etape(migrated)

    row = db.query_one(
        "SELECT payload_json FROM context WHERE event_id = ? AND kind = 'manual_note'",
        (event_id,),
        settings=migrated,
    )
    payload = json.loads(row["payload_json"])
    assert payload["links"] == ["https://www.procyclingstats.com/race/tdf/stage-12"]
    assert payload["profile"].startswith("Haute montagne")
    assert payload["startlist"].startswith("Pogacar")


def test_aucun_contexte_si_rien_a_stocker(migrated: Settings) -> None:
    event_id = _etape(migrated, links_raw="", notes="", profile="", startlist="")

    assert db.query("SELECT * FROM context", settings=migrated) == []
    assert event_id > 0


# -- Rendu du bloc cyclisme -------------------------------------------------


def test_bloc_cyclisme(migrated: Settings) -> None:
    event_id = _etape(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)

    block = session_service.render_blocks(session_id, migrated)[0]

    assert block.splitlines()[0] == (
        "### M1 · CYCLISME · Tour de France · Étape 12 — Briançon > Alpe d'Huez · 04/08 13:15"
    )
    assert "  Profil      Haute montagne, 4 cols" in block
    assert "  Startlist   Pogacar, Vingegaard, Evenepoel, Roglic" in block
    assert "  References  https://www.procyclingstats.com/race/tdf/stage-12" in block
    assert "  Infos       Vent de face dans la vallee" in block
    assert "MARCHES (saisie manuelle)" in block or "MARCHES (manual)" in block
    assert "Pogacar 2.50" in block


def test_bloc_cyclisme_sans_second_participant() -> None:
    """Le separateur « – » ne doit pas apparaitre pour une etape seule."""
    from myassistantbet.services.render import RenderableEvent, render_event

    block = render_event(
        RenderableEvent(
            index=1,
            sport_key="cycling",
            competition="Tour de France",
            home="Étape 12",
            away="",
            commence_local=datetime(2026, 8, 4, 13, 15, tzinfo=PARIS),
        )
    )

    assert block == "### M1 · CYCLISME · Tour de France · Étape 12 · 04/08 13:15"


# -- Routes -----------------------------------------------------------------


def test_formulaire_affiche(client: TestClient) -> None:
    response = client.get("/manual")

    assert response.status_code == 200
    assert "Ajouter un événement manuel" in response.text
    assert "Startlist / favoris" in response.text


def test_creation_via_le_formulaire(client: TestClient, isolated_settings: Settings) -> None:
    response = client.post(
        "/manual",
        data={
            "sport": "cycling",
            "competition": "Tour de France",
            "home": "Étape 12",
            "away": "",
            "date": "2026-08-04",
            "time": "13:15",
            "odds": "Pogacar 2.50\nVingegaard 3.00",
            "links": "https://www.procyclingstats.com/x",
            "notes": "",
            "profile": "Haute montagne",
            "startlist": "Pogacar",
        },
    )

    assert response.status_code == 200
    assert "Événement créé" in response.text
    assert len(db.query("SELECT * FROM events", settings=isolated_settings)) == 1
    assert len(db.query("SELECT * FROM odds", settings=isolated_settings)) == 2


def test_saisie_refusee_conserve_les_valeurs(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post(
        "/manual",
        data={
            "sport": "tennis",
            "competition": "ATP 250 Gstaad",
            "home": "Moutet",
            "away": "",
            "date": "2026-08-04",
            "time": "13:15",
            "odds": "",
            "links": "",
            "notes": "",
        },
    )

    assert response.status_code == 200
    assert "obligatoire" in response.text
    assert "ATP 250 Gstaad" in response.text, "la saisie n'est pas perdue"
    assert db.query("SELECT * FROM events", settings=isolated_settings) == []


def test_lignes_de_cote_rejetees_signalees(client: TestClient) -> None:
    response = client.post(
        "/manual",
        data={
            "sport": "cycling",
            "competition": "Tour",
            "home": "Étape 12",
            "away": "",
            "date": "2026-08-04",
            "time": "13:15",
            "odds": "Pogacar 2.50\nligne cassee ???",
            "links": "",
            "notes": "",
        },
    )

    assert "format non reconnu" in response.text
    assert "ligne cassée ???" in response.text or "ligne cassee ???" in response.text


def test_evenement_manuel_visible_sur_le_board(
    client: TestClient, isolated_settings: Settings
) -> None:
    demain = datetime.now(PARIS) + timedelta(days=1)
    client.post(
        "/manual",
        data={
            "sport": "cycling",
            "competition": "Tour de France",
            "home": "Étape 12",
            "away": "",
            "date": demain.strftime("%Y-%m-%d"),
            "time": "14:00",
            "odds": "Pogacar 2.50",
            "links": "",
            "notes": "",
        },
    )

    assert "Étape 12" in client.get("/").text


def test_le_bandeau_propose_l_ajout_manuel(client: TestClient) -> None:
    assert "+ Événement manuel" in client.get("/").text


# -- Session mixte ----------------------------------------------------------


def test_session_mixte_foot_tennis_cyclisme(migrated: Settings) -> None:
    """Critere d'acceptation de la phase 4."""
    etape = _etape(migrated)
    tennis = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "11:00",
            "Moutet 1.85\nBergs 1.95",
            "",
            "Terre battue en altitude",
            settings=migrated,
        ),
        migrated,
    )
    foot = save(
        build(
            "football",
            "Match amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10\nNul 3.40\nNice 3.20",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )

    session_id = 0
    for event_id in (etape, tennis, foot):
        session_id = board_service.toggle_selection(event_id, True, migrated)

    prompt = build_prompt(session_id, settings=migrated)

    assert prompt.blocks == 3
    assert "· TENNIS · ATP 250 Gstaad · Moutet – Bergs ·" in prompt.body
    assert "· CYCLISME · Tour de France ·" in prompt.body
    assert "· FOOT · Match amical · Lyon – Nice ·" in prompt.body
    assert "Moutet 1.85" in prompt.body
    assert "Pogacar 2.50" in prompt.body
    assert prompt.token_estimate < 8000


def test_cotes_manuelles_ont_un_libelle_lisible(migrated: Settings) -> None:
    """Aucune cle de marche brute ne doit apparaitre dans le prompt."""
    tennis = save(
        build(
            "tennis",
            "ATP 250",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "11:00",
            "Moutet 1.85\nBergs 1.95",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    foot = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10\nNice 3.20",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = 0
    for event_id in (tennis, foot):
        session_id = board_service.toggle_selection(event_id, True, migrated)

    blocks = "\n".join(session_service.render_blocks(session_id, migrated))

    assert "outright" not in blocks
    assert "  Vainqueur   Moutet 1.85 | Bergs 1.95" in blocks
    assert "  Cotes       Lyon 2.10 | Nice 3.20" in blocks
