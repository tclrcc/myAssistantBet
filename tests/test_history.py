from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services.history import (
    HistoryError,
    add_pick,
    delete_pick,
    list_picks,
    list_sessions,
    set_result,
    stats,
)
from myassistantbet.services.manual import build, save


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session_avec_match(settings: Settings, sport: str = "football") -> tuple[int, int]:
    """Cree un evenement manuel et le coche. Renvoie (session_id, event_id)."""
    event_id = save(
        build(
            sport,
            "Amical",
            "Lyon" if sport == "football" else "Moutet",
            "Nice" if sport == "football" else "Bergs",
            "2026-08-04",
            "20:45",
            "Lyon 2.10",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings), event_id


# -- Saisie des picks -------------------------------------------------------


def test_ajout_d_un_pick(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)

    pick_id = add_pick(
        session_id,
        tier="fun",
        market="O/U 2.5",
        selection="Over 2.5",
        event_id=str(event_id),
        price="1,72",
        confidence="3",
        stake="5",
        settings=migrated,
    )

    picks = list_picks(session_id, migrated)
    assert len(picks) == 1
    assert picks[0].pick_id == pick_id
    assert picks[0].price == 1.72, "la virgule decimale est acceptee"
    assert picks[0].confidence == 3
    assert picks[0].stake == 5.0
    assert picks[0].result == "pending"
    assert picks[0].tier_label == "🔵 FUN"
    assert picks[0].event_label == "Lyon – Nice"


def test_pick_sans_match_est_un_combine(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    add_pick(session_id, "safe", "Combiné", "3 sélections", settings=migrated)

    pick = list_picks(session_id, migrated)[0]
    assert pick.event_id is None
    assert pick.event_label == "combiné / hors match"


def test_marche_et_selection_obligatoires(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    with pytest.raises(HistoryError, match="Marché"):
        add_pick(session_id, "safe", "", "Over 2.5", settings=migrated)
    with pytest.raises(HistoryError, match="Sélection"):
        add_pick(session_id, "safe", "O/U", "", settings=migrated)


def test_palier_inconnu_refuse(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    with pytest.raises(HistoryError, match="Palier inconnu"):
        add_pick(session_id, "mega_fun", "O/U", "Over", settings=migrated)


def test_cote_non_numerique_refusee(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    with pytest.raises(HistoryError, match="Cote"):
        add_pick(session_id, "safe", "O/U", "Over", price="beaucoup", settings=migrated)


def test_confiance_hors_bornes_refusee(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    with pytest.raises(HistoryError, match="Confiance"):
        add_pick(session_id, "safe", "O/U", "Over", confidence="9", settings=migrated)


def test_champs_optionnels_vides(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    add_pick(
        session_id, "safe", "O/U", "Over", price="", confidence="", stake="", settings=migrated
    )

    pick = list_picks(session_id, migrated)[0]
    assert (pick.price, pick.confidence, pick.stake) == (None, None, None)


# -- Resultats --------------------------------------------------------------


def test_mise_a_jour_du_resultat(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=migrated)

    set_result(pick_id, "win", migrated)

    assert list_picks(session_id, migrated)[0].result == "win"
    assert list_picks(session_id, migrated)[0].result_label == "gagné"


def test_resultat_inconnu_refuse(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=migrated)

    with pytest.raises(HistoryError, match="Résultat inconnu"):
        set_result(pick_id, "presque", migrated)


def test_suppression(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=migrated)

    delete_pick(pick_id, migrated)

    assert list_picks(session_id, migrated) == []


# -- Taux de reussite -------------------------------------------------------


def _picks(settings: Settings, session_id: int, event_id: int, results: dict[str, str]) -> None:
    for tier, result in results.items():
        pick_id = add_pick(
            session_id, tier, "O/U", "Over", event_id=str(event_id), settings=settings
        )
        set_result(pick_id, result, settings)


def test_taux_par_palier(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for tier, result in [
        ("safe", "win"),
        ("safe", "win"),
        ("safe", "loss"),
        ("fun", "loss"),
        ("fun", "void"),
        ("ultra_fun", "pending"),
    ]:
        pick_id = add_pick(
            session_id, tier, "O/U", "Over", event_id=str(event_id), settings=migrated
        )
        set_result(pick_id, result, migrated)

    by_tier = {row.key: row for row in stats(migrated).by_tier}

    assert by_tier["safe"].rate_label == "67 %"
    assert (by_tier["safe"].won, by_tier["safe"].lost) == (2, 1)
    assert by_tier["fun"].rate_label == "0 %"
    assert by_tier["fun"].void == 1, "un pari annule ne compte pas au denominateur"
    assert by_tier["ultra_fun"].rate is None, "rien de tranche, donc pas de taux"


def test_taux_par_sport(migrated: Settings) -> None:
    foot_session, foot_event = _session_avec_match(migrated, "football")
    tennis_event = save(
        build(
            "tennis",
            "ATP",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "11:00",
            "",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(tennis_event, True, migrated)
    _picks(migrated, foot_session, foot_event, {"safe": "win"})
    pick_id = add_pick(
        foot_session, "fun", "Vainqueur", "Moutet", event_id=str(tennis_event), settings=migrated
    )
    set_result(pick_id, "loss", migrated)

    by_sport = {row.label: row for row in stats(migrated).by_sport}

    assert by_sport["Football"].rate_label == "100 %"
    assert by_sport["Tennis"].rate_label == "0 %"


def test_pick_sans_match_classe_hors_sport(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "Combiné", "3 sélections", settings=migrated)
    set_result(pick_id, "win", migrated)

    labels = {row.label for row in stats(migrated).by_sport}

    assert "—" in labels


def test_total_general(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for tier, result in [("safe", "win"), ("fun", "loss"), ("ultra_fun", "win")]:
        pick_id = add_pick(
            session_id, tier, "O/U", "Over", event_id=str(event_id), settings=migrated
        )
        set_result(pick_id, result, migrated)

    overall = stats(migrated).overall

    assert (overall.won, overall.lost) == (2, 1)
    assert overall.rate_label == "67 %"


def test_stats_vides(migrated: Settings) -> None:
    assert stats(migrated).empty is True


def test_aucun_indicateur_financier(migrated: Settings) -> None:
    """SPEC section 9 : la mise est memorisee, jamais agregee."""
    session_id, event_id = _session_avec_match(migrated)
    add_pick(
        session_id, "safe", "O/U", "Over", event_id=str(event_id), stake="100", settings=migrated
    )

    result = stats(migrated)

    for row in [*result.by_tier, *result.by_sport, result.overall]:
        assert not hasattr(row, "roi")
        assert not hasattr(row, "profit")
        assert not hasattr(row, "stake")


# -- Sessions ---------------------------------------------------------------


def test_liste_des_sessions(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)

    sessions = list_sessions(migrated)

    assert len(sessions) == 1
    assert sessions[0].events == 1
    assert sessions[0].picks == 1
    assert sessions[0].prompts == 0


# -- Routes -----------------------------------------------------------------


def test_page_historique(client: TestClient, isolated_settings: Settings) -> None:
    _session_avec_match(isolated_settings)

    response = client.get("/history")

    assert response.status_code == 200
    assert "Taux de réussite" in response.text
    assert "Aucun pick enregistré" in response.text


def test_page_picks(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _session_avec_match(isolated_settings)

    response = client.get(f"/history/{session_id}")

    assert response.status_code == 200
    assert "Lyon – Nice" in response.text
    assert "🟢 SAFE" in response.text


def test_ajout_via_le_formulaire(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)

    response = client.post(
        f"/history/{session_id}/picks",
        data={
            "event_id": str(event_id),
            "tier": "fun",
            "market": "O/U 2.5",
            "selection": "Over 2.5",
            "price": "1.72",
            "confidence": "3",
            "stake": "5",
        },
    )

    assert response.status_code == 200
    assert "Over 2.5" in response.text
    assert len(db.query("SELECT * FROM picks", settings=isolated_settings)) == 1


def test_saisie_refusee_affiche_le_motif(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _session_avec_match(isolated_settings)

    response = client.post(
        f"/history/{session_id}/picks",
        data={"tier": "fun", "market": "", "selection": "Over"},
    )

    assert response.status_code == 200
    assert "« Marché » est obligatoire" in response.text
    assert db.query("SELECT * FROM picks", settings=isolated_settings) == []


def test_resultat_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.post(f"/picks/{pick_id}/result", data={"result": "win"})

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="picks">')
    assert list_picks(session_id, isolated_settings)[0].result == "win"


def test_suppression_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.post(f"/picks/{pick_id}/delete")

    assert response.status_code == 200
    assert "Aucun pick saisi" in response.text


def test_pick_inconnu_renvoie_404(client: TestClient) -> None:
    assert client.post("/picks/999/delete").status_code == 404


def test_taux_affiches_apres_saisie(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    pick_id = add_pick(
        session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=isolated_settings
    )
    set_result(pick_id, "win", isolated_settings)

    response = client.get("/history")

    assert "100 %" in response.text
    assert "🟢 SAFE" in response.text
    assert "Football" in response.text
