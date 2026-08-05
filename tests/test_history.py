from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coupons as coupons_service
from myassistantbet.services.history import (
    Analysis,
    HistoryError,
    add_pick,
    analysis,
    delete_pick,
    list_picks,
    list_sessions,
    pickable_groups,
    set_event,
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


def test_pick_sans_match_est_hors_match(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    add_pick(session_id, "safe", "Combiné", "3 sélections", settings=migrated)

    pick = list_picks(session_id, migrated)[0]
    assert pick.event_id is None
    assert pick.event_label == "— hors match —", (
        "« combiné » designe un coupon a plusieurs jambes depuis la phase 10"
    )


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


def _joue(
    settings: Settings,
    session_id: int,
    event_id: int | None,
    tier: str,
    result: str,
    market: str = "O/U",
    selection: str = "Over",
    stake: str = "",
) -> int:
    """Saisit une selection **et la joue** : un pick ne compte qu'en coupon.

    C'est le chemin reel depuis la phase 10 — `played` ne passe a vrai qu'au
    rattachement. Marquer le pick a la main ferait passer les tests sans que le
    parcours fonctionne.
    """
    pick_id = add_pick(
        session_id,
        tier,
        market,
        selection,
        event_id=str(event_id) if event_id else "",
        stake=stake,
        settings=settings,
    )
    set_result(pick_id, result, settings)
    coupons_service.create(session_id, [pick_id], settings=settings)
    return pick_id


def _picks(settings: Settings, session_id: int, event_id: int, results: dict[str, str]) -> None:
    for tier, result in results.items():
        _joue(settings, session_id, event_id, tier, result)


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
        _joue(migrated, session_id, event_id, tier, result)

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
    _joue(migrated, foot_session, tennis_event, "fun", "loss", "Vainqueur", "Moutet")

    by_sport = {row.label: row for row in stats(migrated).by_sport}

    assert by_sport["Football"].rate_label == "100 %"
    assert by_sport["Tennis"].rate_label == "0 %"


def test_pick_sans_match_classe_hors_sport(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    _joue(migrated, session_id, None, "safe", "win", "Combiné", "3 sélections")

    labels = {row.label for row in stats(migrated).by_sport}

    assert "—" in labels


def test_total_general(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for tier, result in [("safe", "win"), ("fun", "loss"), ("ultra_fun", "win")]:
        _joue(migrated, session_id, event_id, tier, result)

    overall = stats(migrated).overall

    assert (overall.won, overall.lost) == (2, 1)
    assert overall.rate_label == "67 %"


def test_stats_vides(migrated: Settings) -> None:
    assert stats(migrated).empty is True


def test_aucun_indicateur_financier(migrated: Settings) -> None:
    """SPEC section 9 : la mise est memorisee, jamais agregee."""
    session_id, event_id = _session_avec_match(migrated)
    _joue(migrated, session_id, event_id, "safe", "win", stake="100")

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
    assert "Sessions" in response.text
    assert "Statistiques" in response.text, "les taux ont leur propre page"


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
    assert response.text.strip().startswith('<div id="worksheet">')
    assert list_picks(session_id, isolated_settings)[0].result == "win"


def test_suppression_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.post(f"/picks/{pick_id}/delete")

    assert response.status_code == 200
    assert "Aucune sélection" in response.text


def test_pick_inconnu_renvoie_404(client: TestClient) -> None:
    assert client.post("/picks/999/delete").status_code == 404


def test_taux_affiches_apres_saisie(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    _joue(isolated_settings, session_id, event_id, "safe", "win")

    response = client.get("/stats")

    assert "100 %" in response.text
    assert "🟢 SAFE" in response.text
    assert "Football" in response.text
    assert "Ce que valent tes paris" in response.text


def test_page_de_stats_vide(client: TestClient) -> None:
    response = client.get("/stats")

    assert response.status_code == 200
    assert "Rien à mesurer" in response.text


# -- Selecteur de match : sport et competition ------------------------------


def test_les_matchs_sont_groupes_par_sport_et_competition(migrated: Settings) -> None:
    """Retrouver un match parmi vingt : le groupe evite de lire chaque ligne."""
    foot_session, _ = _session_avec_match(migrated, "football")
    tennis_event = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "18:00",
            "Moutet 1.80",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(tennis_event, True, migrated)

    groups = dict(pickable_groups(foot_session, migrated))

    assert "Football · Amical" in groups
    assert "Tennis · ATP Canadian Open" in groups
    assert [event.label for event in groups["Tennis · ATP Canadian Open"]] == ["Moutet – Bergs"]


def test_un_evenement_sans_competition_reste_groupe(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    groups = dict(pickable_groups(session_id, migrated))

    assert all(" · " in name for name in groups), "chaque groupe nomme sport et competition"


def test_le_selecteur_de_match_porte_les_groupes(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _ = _session_avec_match(isolated_settings)

    response = client.get(f"/history/{session_id}")

    assert '<optgroup label="Football · Amical">' in response.text


# -- Rattacher une selection a un match hors shortlist ----------------------


def _hors_shortlist(settings: Settings) -> int:
    """Un match connu mais jamais coche : c'est le cas d'un match commence."""
    return save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Michelsen",
            "Cerundolo",
            "2026-08-04",
            "22:20",
            "Michelsen 1.62",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _fenetre_autour(settings: Settings, session_id: int, moment: str) -> None:
    """Cale la session sur une date fixe : la fenetre en depend."""
    db.execute(
        "UPDATE sessions SET created_at = ? WHERE id = ?", (moment, session_id), settings=settings
    )


def test_un_match_hors_shortlist_reste_proposable(migrated: Settings) -> None:
    """Un match qui a commence quitte le board : il n'a jamais pu etre coche."""
    session_id, _ = _session_avec_match(migrated)
    _fenetre_autour(migrated, session_id, "2026-08-04T12:00:00Z")
    _hors_shortlist(migrated)

    groups = dict(pickable_groups(session_id, migrated))

    assert "Football · Amical" in groups, "la shortlist reste en tete"
    hors = groups["Hors sélection — Tennis · ATP Canadian Open"]
    assert [event.label for event in hors] == ["04/08 22:20 · Michelsen – Cerundolo"], (
        "l'horaire distingue un match d'un autre jour"
    )


def test_un_match_trop_loin_n_est_pas_propose(migrated: Settings) -> None:
    """Sinon le menu proposerait le catalogue entier."""
    session_id, _ = _session_avec_match(migrated)
    _fenetre_autour(migrated, session_id, "2026-08-01T12:00:00Z")
    _hors_shortlist(migrated)

    groups = dict(pickable_groups(session_id, migrated))

    assert list(groups) == ["Football · Amical"]


def test_rattachement_d_une_selection_a_un_match(migrated: Settings) -> None:
    """Sans reprise, une selection restait « — hors match — » pour toujours."""
    session_id, _ = _session_avec_match(migrated)
    event_id = _hors_shortlist(migrated)
    pick_id = add_pick(session_id, "safe", "Vainqueur", "Michelsen", settings=migrated)
    assert list_picks(session_id, migrated)[-1].event_label == "— hors match —"

    set_event(pick_id, str(event_id), migrated)

    pick = list_picks(session_id, migrated)[-1]
    assert pick.event_id == event_id
    assert pick.event_label == "Michelsen – Cerundolo"


def test_detachement_d_une_selection(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)

    set_event(pick_id, "", migrated)

    assert list_picks(session_id, migrated)[0].event_id is None


def test_un_match_inconnu_est_refuse(migrated: Settings) -> None:
    """Ecrire un identifiant absent laisserait un pick pointant sur du vide."""
    session_id, _ = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=migrated)

    with pytest.raises(HistoryError):
        set_event(pick_id, "4242", migrated)
    with pytest.raises(HistoryError):
        set_event(pick_id, "pas un nombre", migrated)


def test_rattachement_rend_la_selection_visible_par_sport(migrated: Settings) -> None:
    """Sans match, une selection n'a pas de sport : elle manque aux statistiques."""
    session_id, _ = _session_avec_match(migrated)
    event_id = _hors_shortlist(migrated)
    pick_id = add_pick(session_id, "safe", "Vainqueur", "Michelsen", settings=migrated)
    set_result(pick_id, "win", migrated)

    assert [row.label for row in analysis(migrated).by_sport] == ["—"]

    set_event(pick_id, str(event_id), migrated)

    assert [row.label for row in analysis(migrated).by_sport] == ["Tennis"]


def test_selecteur_de_match_charge_a_la_demande(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Cible d'un echange HTMX : un fragment, jamais la page entiere."""
    session_id, _ = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.get(f"/picks/{pick_id}/event")

    assert response.status_code == 200
    assert response.text.strip().startswith('<form class="pick-event"')
    assert '<optgroup label="Football · Amical">' in response.text


def test_rattachement_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.post(f"/picks/{pick_id}/event", data={"event_id": str(event_id)})

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="worksheet">')
    assert list_picks(session_id, isolated_settings)[0].event_id == event_id


def test_rattachement_a_un_match_inconnu_ne_casse_pas_la_page(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _ = _session_avec_match(isolated_settings)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)

    response = client.post(f"/picks/{pick_id}/event", data={"event_id": "4242"})

    assert response.status_code == 200
    assert "Match inconnu" in response.text
    assert list_picks(session_id, isolated_settings)[0].event_id is None


# -- Ce que vaut l'analyse --------------------------------------------------


def _propose(
    settings: Settings,
    session_id: int,
    event_id: int,
    tier: str,
    result: str,
    market: str = "O/U",
    confidence: str = "",
) -> int:
    """Une selection **non jouee** dont on connait le resultat."""
    pick_id = add_pick(
        session_id,
        tier,
        market,
        "Over",
        event_id=str(event_id),
        confidence=confidence,
        settings=settings,
    )
    set_result(pick_id, result, settings)
    return pick_id


def test_l_analyse_compte_aussi_ce_qui_n_a_pas_ete_joue(migrated: Settings) -> None:
    """C'est toute la difference avec `stats()`, qui ne mesure que le terrain."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win")
    _joue(migrated, session_id, event_id, "safe", "loss")

    report = analysis(migrated)

    assert report.settled == 2
    assert stats(migrated).overall.settled == 1, "un seul pari joue"


def test_l_analyse_oppose_les_jouees_aux_ecartees(migrated: Settings) -> None:
    """Si l'ecarte gagne autant que le joue, le tri n'apporte rien."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(3):
        _joue(migrated, session_id, event_id, "safe", "win")
    _propose(migrated, session_id, event_id, "fun", "loss")
    _propose(migrated, session_id, event_id, "fun", "loss")

    report = analysis(migrated)

    assert report.played.rate == 1.0
    assert report.skipped.rate == 0.0
    assert report.comparable


def test_la_comparaison_se_tait_s_il_manque_un_cote(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win")

    assert not analysis(migrated).comparable, "rien de joue : il n'y a rien a opposer"


def test_l_analyse_expose_la_confiance(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "loss", confidence="5")
    _propose(migrated, session_id, event_id, "safe", "win", confidence="3")

    by_confidence = {row.key: row for row in analysis(migrated).by_confidence}

    assert by_confidence["5"].rate == 0.0
    assert by_confidence["3"].rate == 1.0


def test_les_marches_vus_une_fois_sont_annonces_pas_tus(migrated: Settings) -> None:
    """Un plafond silencieux se lit « tout est couvert » alors que non."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(2):
        _propose(migrated, session_id, event_id, "safe", "win", market="O/U 2.5")
    _propose(migrated, session_id, event_id, "safe", "win", market="Score exact")

    report = analysis(migrated)

    assert [row.label for row in report.by_market] == ["O/U 2.5"]
    assert report.hidden_markets == 1


def test_l_analyse_vide(migrated: Settings) -> None:
    assert analysis(migrated).empty is True


def test_aucun_champ_financier_sur_l_analyse() -> None:
    """Meme garde que partout ailleurs (SPEC.md section 9)."""
    interdits = {"roi", "profit", "stake", "gain", "solde", "esperance", "ev", "value", "edge"}
    noms = {item.name for item in fields(Analysis)}
    noms |= {name for name in dir(Analysis) if not name.startswith("_")}

    assert not (noms & interdits)
