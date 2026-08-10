from __future__ import annotations

import re
from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coupons as coupons_service
from myassistantbet.services import market_families
from myassistantbet.services.competitions import set_category
from myassistantbet.services.history import (
    ANALYSIS_MIN_DAYS,
    ANALYSIS_MIN_ROWS,
    ANALYSIS_MIN_TOTAL,
    FEEDBACK_MIN_DAYS,
    FEEDBACK_MIN_ROWS,
    FEEDBACK_MIN_TOTAL,
    Analysis,
    HistoryError,
    Lot,
    _overlaps,
    add_pick,
    analysis,
    delete_pick,
    labelling,
    list_picks,
    list_sessions,
    load_bands,
    lots,
    pickable_events,
    pickable_groups,
    required_sample,
    set_event,
    set_result,
    stats,
    wilson,
    worksheet,
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


#: Ces tests montent plusieurs selections sur un **meme match** par commodite —
#: c'est le match le moins couteux a fabriquer. La note d'independance est donc
#: fournie d'office : c'est un test dedie qui verifie qu'elle est exigee, pas
#: chaque montage de fixture.
INDEP = {"independence_note": "angles indépendants (fixture)"}


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
        # Meme raison que `_propose` : ces montages reutilisent un match unique.
        independence_note="angles indépendants (fixture)",
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
    # L'heure precede l'affiche sur **tous** les matchs, shortlist comprise :
    # une session porte trente affiches sur deux jours et le rattachement se
    # fait de memoire — « le match de 18h00 ». Sans l'heure, il fallait
    # reconnaitre l'affiche pour retrouver le match, ce dont on n'est
    # justement pas sur.
    assert [event.label for event in groups["Tennis · ATP Canadian Open"]] == [
        "04/08 18:00 · Moutet – Bergs"
    ]


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


# -- Feuille de session : ce qui reste a trancher, puis ce qui l'est ---------


def test_la_feuille_separe_le_tranche_de_l_attente(migrated: Settings) -> None:
    """Melangees, il fallait relire quinze lignes pour trouver les trois qui restent."""
    session_id, event_id = _session_avec_match(migrated)
    attente = add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)
    gagne = add_pick(
        session_id, "fun", "O/U", "Under", event_id=str(event_id), settings=migrated, **INDEP
    )
    set_result(gagne, "win", migrated)

    feuille = worksheet(session_id, migrated)

    assert [pick.pick_id for _, picks in feuille.pending for pick in picks] == [attente]
    assert [pick.pick_id for _, picks in feuille.settled for pick in picks] == [gagne]
    assert (feuille.pending_count, feuille.settled_count, feuille.total) == (1, 1, 2)


def test_un_pari_annule_est_tranche(migrated: Settings) -> None:
    """Il n'y a plus rien a saisir dessus : le laisser « a trancher » ment."""
    session_id, event_id = _session_avec_match(migrated)
    pick_id = add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)
    set_result(pick_id, "void", migrated)

    feuille = worksheet(session_id, migrated)

    assert feuille.pending == []
    assert feuille.settled_count == 1


def test_la_feuille_groupe_par_competition(migrated: Settings) -> None:
    """On relit une journee tournoi par tournoi, pas dans l'ordre du tableau."""
    session_id, foot = _session_avec_match(migrated)
    tennis = save(
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
    # Saisies en alternance : sans regroupement, elles ressortiraient melangees.
    add_pick(session_id, "safe", "Vainqueur", "Moutet", event_id=str(tennis), settings=migrated)
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(foot), settings=migrated)
    add_pick(
        session_id, "fun", "Vainqueur", "Bergs", event_id=str(tennis), settings=migrated, **INDEP
    )

    groupes = worksheet(session_id, migrated).pending

    assert [nom for nom, _ in groupes] == ["Football · Amical", "Tennis · ATP Canadian Open"]
    assert [len(picks) for _, picks in groupes] == [1, 2]


def test_une_selection_sans_match_ferme_la_marche(migrated: Settings) -> None:
    """Elle n'appartient a aucun tournoi ; la mettre en tete decalerait le reste."""
    session_id, event_id = _session_avec_match(migrated)
    add_pick(session_id, "safe", "Vainqueur tournoi", "Alcaraz", settings=migrated)
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)

    groupes = worksheet(session_id, migrated).pending

    assert [nom for nom, _ in groupes] == ["Football · Amical", "Hors compétition"]


def test_la_feuille_range_par_heure_dans_un_groupe(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    tard = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "22:00",
            "Moutet 1.80",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    tot = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Tabilo",
            "Popyrin",
            "2026-08-04",
            "18:00",
            "Tabilo 1.90",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    add_pick(session_id, "safe", "Vainqueur", "Moutet", event_id=str(tard), settings=migrated)
    add_pick(session_id, "safe", "Vainqueur", "Tabilo", event_id=str(tot), settings=migrated)

    groupes = dict(worksheet(session_id, migrated).pending)

    assert [pick.selection for pick in groupes["Tennis · ATP Canadian Open"]] == [
        "Tabilo",
        "Moutet",
    ]


def test_la_feuille_rend_deux_tableaux(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=isolated_settings)
    tranche = add_pick(
        session_id,
        "fun",
        "O/U",
        "Under",
        event_id=str(event_id),
        settings=isolated_settings,
        **INDEP,
    )
    set_result(tranche, "loss", isolated_settings)

    page = client.get(f"/history/{session_id}").text

    assert "À trancher" in page
    assert "Tranchées" in page
    assert '<td class="group-cell" colspan="8">' in page
    assert "<th" not in page.split("group-cell")[1].split("</table>")[0], (
        "un titre de groupe en `th` heriterait du `position: sticky` de l'en-tete"
    )


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
    price: str = "",
    angle: str = "",
    source_level: str = "",
) -> int:
    """Une selection **non jouee** dont on connait le resultat."""
    pick_id = add_pick(
        session_id,
        tier,
        market,
        "Over",
        event_id=str(event_id),
        confidence=confidence,
        price=price,
        angle=angle,
        source_level=source_level,
        # Ces tests montent plusieurs selections sur un **meme match** par
        # commodite — c'est le match le moins couteux a fabriquer. La note
        # d'independance est donc fournie d'office : c'est un test dedie qui
        # verifie qu'elle est exigee, pas chaque montage de fixture.
        independence_note="angles indépendants (fixture)",
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


# -- Comment j'etiquette ----------------------------------------------------


def _mix(blocs: list, key: str):
    return next(bloc for bloc in blocs if bloc.key == key)


def test_l_echelle_rend_ses_niveaux_jamais_employes(migrated: Settings) -> None:
    """C'est le cran jamais mis qui decrit la facon d'etiqueter : omettre une
    confiance 5 a zero ferait lire une echelle a quatre crans."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", confidence="3")

    confiance = _mix(labelling(migrated), "confidence")

    assert [row.key for row in confiance.rows] == ["5", "4", "3", "2", "1"]
    assert confiance.used == 1, "un seul cran employe sur cinq"
    assert confiance.levels == 5


def test_la_vacance_d_un_palier_se_compte_en_sessions(migrated: Settings) -> None:
    """ULTRA FUN est a 0/7, GIGA FUN et GIGA+ n'ont jamais servi en cent
    selections. Le prompt les annonce pourtant a chaque session.

    On ne force pas leur remplissage — un quota rempli avec du vide est l'erreur
    que le prompt nomme lui-meme comme la plus couteuse. On mesure la vacance,
    et **en sessions** : une part de volume a zero dit qu'un palier ne sert
    jamais, elle ne dit pas a quel rythme. Un palier absent de cinq sessions sur
    cinq n'est pas dans le meme etat qu'un palier employe une fois sur deux, et
    c'est cette difference qui decidera un jour de raccourcir l'echelle.

    Mesuree sans rien parser : l'application ne lit pas la prose du rendu, mais
    un palier qu'aucune selection de la session ne porte **est** un palier laisse
    vide ce jour-la.
    """
    premiere, event_id = _session_avec_match(migrated)
    _propose(migrated, premiere, event_id, "safe", "win")
    _propose(migrated, premiere, event_id, "fun", "win")
    seconde = db.query_one(
        "SELECT id FROM sessions WHERE id != ? ORDER BY id DESC", (premiere,), settings=migrated
    )
    if seconde is None:
        with db.connect(migrated) as conn:
            seconde = {
                "id": int(
                    conn.execute(
                        "INSERT INTO sessions (label, created_at) "
                        "VALUES ('S2', '2026-08-05T10:00:00Z')"
                    ).lastrowid
                )
            }
    _propose(migrated, int(seconde["id"]), event_id, "safe", "loss")

    palier = _mix(labelling(migrated), "tier")
    par_cle = {row.key: row for row in palier.rows}

    assert palier.sessions == 2
    assert par_cle["safe"].absent_sessions == 0, "employe partout"
    assert par_cle["fun"].absent_sessions == 1, "employe une session sur deux"
    assert par_cle["giga_plus"].absent_sessions == 2, "jamais employe"


def test_la_vacance_vaut_aussi_pour_la_confiance(migrated: Settings) -> None:
    """Meme forme, meme calcul : 99 % du volume tenait sur deux crans, et les
    crans 1 et 5 n'ont jamais servi. Le compte le dit de la meme facon."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", confidence="3")

    confiance = _mix(labelling(migrated), "confidence")
    par_cle = {row.key: row for row in confiance.rows}

    assert par_cle["3"].absent_sessions == 0
    assert par_cle["5"].absent_sessions == 1


def test_la_page_dit_les_niveaux_sans_emploi(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Un niveau vide est un resultat, pas une anomalie : c'est sa persistance
    qui dira s'il est inatteignable — auquel cas c'est l'echelle qu'il faudra
    raccourcir, pas les selections qu'il faudra y pousser."""
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    page = " ".join(client.get("/stats").text.split())

    assert "sans emploi sur 1 session(s)" in page
    assert "c'est l'échelle qu'il faudra raccourcir" in page


def test_la_repartition_compte_les_selections_non_tranchees(migrated: Settings) -> None:
    """Une confiance annoncee est un geste pose a l'analyse : le resultat n'y
    change rien, et attendre qu'il tombe ferait taire le bloc le plus sur."""
    session_id, event_id = _session_avec_match(migrated)
    add_pick(
        session_id, "safe", "O/U", "Over", event_id=str(event_id), confidence="4", settings=migrated
    )

    confiance = _mix(labelling(migrated), "confidence")

    assert analysis(migrated).empty, "rien de tranche"
    assert confiance.total == 1, "la repartition, elle, a de quoi parler"


def test_une_echelle_repliee_sur_deux_crans_est_signalee(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(9):
        _propose(migrated, session_id, event_id, "safe", "win", confidence="3")
    for _ in range(6):
        _propose(migrated, session_id, event_id, "safe", "win", confidence="4")
    _propose(migrated, session_id, event_id, "safe", "win", confidence="2")

    confiance = _mix(labelling(migrated), "confidence")

    assert confiance.top_share == pytest.approx(15 / 16)
    assert confiance.concentrated
    assert confiance.top_labels == "confiance 3 et confiance 4"


def test_une_echelle_employee_en_entier_ne_declenche_rien(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for niveau in ("1", "2", "3", "4", "5"):
        for _ in range(4):
            _propose(migrated, session_id, event_id, "safe", "win", confidence=niveau)

    assert not _mix(labelling(migrated), "confidence").concentrated


def test_les_selections_sans_confiance_sortent_du_total(migrated: Settings) -> None:
    """Ne pas etiqueter n'est pas un niveau de l'echelle : les compter dedans
    ferait baisser toutes les parts sans decrire aucun cran."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", confidence="3")
    _propose(migrated, session_id, event_id, "safe", "win")

    confiance = _mix(labelling(migrated), "confidence")

    assert (confiance.total, confiance.unlabelled) == (1, 1)
    assert _mix(labelling(migrated), "tier").total == 2, "le palier, lui, est obligatoire"


def test_la_repartition_se_tait_sur_une_base_vide(migrated: Settings) -> None:
    assert labelling(migrated) == []


def test_la_page_signale_la_concentration(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for _ in range(9):
        _propose(isolated_settings, session_id, event_id, "safe", "win", confidence="3")
    for _ in range(6):
        _propose(isolated_settings, session_id, event_id, "fun", "win", confidence="4")

    page = " ".join(client.get("/stats").text.split())

    assert "Comment tu étiquettes" in page
    assert "du volume tiennent sur" in page
    assert "ne demande pas de recul" in page, "le bloc ne depend d'aucun resultat"


def test_la_page_parle_avant_le_premier_resultat(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Vingt selections saisies un soir : la page repondait « rien a mesurer »
    alors que la repartition des confiances, elle, etait complete."""
    session_id, event_id = _session_avec_match(isolated_settings)
    add_pick(
        session_id,
        "safe",
        "O/U",
        "Over",
        event_id=str(event_id),
        confidence="3",
        settings=isolated_settings,
    )

    page = client.get("/stats").text

    assert "Rien à mesurer" not in page
    assert "Comment tu étiquettes" in page


# -- Distance a la testabilite ----------------------------------------------


def test_l_effectif_requis_croit_quand_l_ecart_se_resserre() -> None:
    """C'est toute la logique du calcul : separer 63 % de 44 % demande moins de
    paris que separer 63 % de 60 %."""
    large = required_sample(0.63, 0.44)
    etroit = required_sample(0.63, 0.60)

    assert large is not None and etroit is not None
    assert etroit > 10 * large


def test_l_effectif_requis_sur_un_ecart_connu() -> None:
    """SAFE 63 % contre FUN 44 %, la comparaison que la page invite a faire."""
    assert required_sample(0.63, 0.44) == 105, "ceil(104.14)"


def test_un_ecart_nul_n_est_jamais_testable() -> None:
    """Aucun volume ne separe deux taux egaux, et la ligne se tait plutot que
    d'annoncer une cible infinie."""
    assert required_sample(0.5, 0.5) is None


def test_la_comparaison_prend_les_deux_plus_gros_regroupements(migrated: Settings) -> None:
    """Les deux plus gros et non deux cles fixees : c'est ce que l'oeil compare
    sur un graphique a barres."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(10):
        _propose(migrated, session_id, event_id, "safe", "win")
    for _ in range(8):
        _propose(migrated, session_id, event_id, "fun", "loss")
    _propose(migrated, session_id, event_id, "ultra_fun", "loss")

    comparaison = analysis(migrated).tier_comparison

    assert {comparaison.left.key, comparaison.right.key} == {"safe", "fun"}
    assert comparaison.left.key == "safe", "le meilleur taux en premier"
    assert comparaison.observed == 18


def test_la_comparaison_se_tait_sur_un_seul_regroupement(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win")

    assert analysis(migrated).tier_comparison is None


def test_la_page_dit_le_volume_qui_trancherait(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for index in range(10):
        _propose(isolated_settings, session_id, event_id, "safe", "win" if index < 7 else "loss")
    for index in range(10):
        _propose(isolated_settings, session_id, event_id, "fun", "win" if index < 3 else "loss")

    page = " ".join(client.get("/stats").text.split())

    assert "20 sélections tranchées sur ces deux lignes (10 et 10)" in page
    assert "devienne testable" in page
    assert "par ligne" in page
    assert "suppose les sélections indépendantes" in page


def test_la_cible_est_annoncee_plus_haute_quand_les_paris_se_groupent(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le calcul suppose l'independance : quand elle ne tient pas, il
    sous-estime, et le dire coute une phrase."""
    session_id, event_id = _session_avec_match(isolated_settings)
    for index in range(10):
        _propose(isolated_settings, session_id, event_id, "safe", "win" if index < 7 else "loss")
    for index in range(10):
        _propose(isolated_settings, session_id, event_id, "fun", "win" if index < 3 else "loss")

    comparaison = analysis(isolated_settings).tier_comparison
    page = " ".join(client.get("/stats").text.split())

    assert comparaison.clustered, "vingt paris sur un seul match"
    assert comparaison.units == 1
    assert "la vraie cible est plus haute" in page


# -- Bandes cibles par confiance --------------------------------------------


def test_les_bandes_sont_reglees_en_base_pas_en_dur(migrated: Settings) -> None:
    """« Editable sans toucher au code » : c'est une decision de l'utilisateur
    sur sa propre echelle, pas une constante du projet."""
    bandes = load_bands(migrated)

    assert [bande.level for bande in bandes.values()] == [1, 2, 3, 4, 5]
    assert (bandes[4].low, bandes[4].high) == (60.0, 70.0)
    assert bandes[5].high is None, "le dernier cran n'a pas de borne haute"
    assert bandes[5].label == "70 % et plus"
    assert bandes[4].label == "60 – 70 %"


def test_la_bande_ne_se_rattache_qu_a_la_confiance(migrated: Settings) -> None:
    """Un sport ou un marche ne se fixe pas d'objectif de taux."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", confidence="4")

    report = analysis(migrated)

    assert report.by_confidence[0].band is not None
    assert all(row.band is None for row in report.by_sport + report.by_tier + report.by_market)


def test_un_intervalle_a_cheval_ne_signale_rien(migrated: Settings) -> None:
    """44 % dont l'intervalle va de 31 a 57 traverse trois bandes : le declarer
    hors de la sienne serait affirmer plus que les donnees ne portent."""
    session_id, event_id = _session_avec_match(migrated)
    for index in range(16):
        _propose(
            migrated, session_id, event_id, "safe", "win" if index < 7 else "loss", confidence="4"
        )

    ligne = analysis(migrated).by_confidence[0]

    assert ligne.rate == pytest.approx(7 / 16)
    assert ligne.band.excludes((0.60, 0.65)) is False, "la bande contient cet intervalle"
    assert not ligne.off_band, "l'intervalle chevauche encore la bande cible"


def test_un_intervalle_entierement_sous_la_bande_est_signale(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(20):
        _propose(migrated, session_id, event_id, "safe", "loss", confidence="4")

    ligne = analysis(migrated).by_confidence[0]

    assert ligne.rate == 0.0
    assert ligne.off_band, "0/20 ne touche pas la bande 60 – 70 %"


def test_un_intervalle_entierement_au_dessus_est_signale(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(20):
        _propose(migrated, session_id, event_id, "safe", "win", confidence="2")

    ligne = analysis(migrated).by_confidence[0]

    assert ligne.off_band, "20/20 depasse la bande 40 – 50 %"


def test_le_dernier_cran_n_a_pas_de_borne_haute(migrated: Settings) -> None:
    """Rien ne peut etre « au-dessus » de « 70 % et plus »."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(20):
        _propose(migrated, session_id, event_id, "safe", "win", confidence="5")

    ligne = analysis(migrated).by_confidence[0]

    assert ligne.rate == 1.0
    assert not ligne.off_band


def test_les_bandes_se_modifient_depuis_les_reglages(
    client: TestClient, isolated_settings: Settings
) -> None:
    reponse = client.post(
        "/settings/bands",
        data={"level": ["5", "4"], "low": ["75", "65"], "high": ["", "75"]},
    )

    assert reponse.status_code == 200
    assert "Bandes de confiance enregistrées" in reponse.text
    assert not reponse.text.lstrip().startswith("<!doctype"), "une route HTMX rend le fragment"

    bandes = load_bands(isolated_settings)
    assert (bandes[4].low, bandes[4].high) == (65.0, 75.0)
    assert (bandes[5].low, bandes[5].high) == (75.0, None)


def test_une_bande_incoherente_est_refusee(client: TestClient) -> None:
    reponse = client.post("/settings/bands", data={"level": ["4"], "low": ["70"], "high": ["60"]})

    assert "doit dépasser la borne basse" in reponse.text


def test_une_bande_hors_de_zero_cent_est_refusee(client: TestClient) -> None:
    reponse = client.post("/settings/bands", data={"level": ["4"], "low": ["140"], "high": [""]})

    assert "sort de 0 à 100 %" in reponse.text


def test_la_page_affiche_la_bande_cible(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, event_id, "safe", "win", confidence="4")

    page = " ".join(client.get("/stats").text.split())

    assert "cible 60 – 70 %" in page
    assert "réglable dans les réglages" in page


# -- Effectif independant ---------------------------------------------------


def test_trois_selections_sur_un_match_font_un_evenement(migrated: Settings) -> None:
    """Vainqueur, handicap jeux et total de jeux sur la meme rencontre sont une
    seule issue comptee trois fois : le joueur qui gagne en deux sets les fait
    passer ensemble."""
    session_id, event_id = _session_avec_match(migrated)
    for marche in ("Vainqueur", "Hand. jeux", "O/U jeux"):
        _propose(migrated, session_id, event_id, "safe", "win", market=marche)

    ligne = analysis(migrated).by_tier[0]

    assert (ligne.settled, ligne.units) == (3, 1)
    assert ligne.clustered
    assert ligne.units_label == "1 événement(s)"


def test_une_selection_par_match_ne_signale_rien(migrated: Settings) -> None:
    """Le cas des donnees reelles : 90 selections pour 87 evenements. Ecrire
    « 37 paris · 37 evenements » sur chaque ligne ne dirait rien."""
    session_id, premier = _session_avec_match(migrated)
    _propose(migrated, session_id, premier, "safe", "win")
    _, second = _session_avec_match(migrated, "tennis")
    _propose(migrated, session_id, second, "safe", "win")

    ligne = analysis(migrated).by_tier[0]

    assert (ligne.settled, ligne.units) == (2, 2)
    assert not ligne.clustered
    assert ligne.units_label == ""


def test_une_selection_sans_match_compte_pour_une_unite(migrated: Settings) -> None:
    """Rien ne permet de la rapprocher d'une autre, donc rien ne permet de la
    declarer correlee. C'est l'hypothese optimiste, et la note le dit."""
    session_id, _ = _session_avec_match(migrated)
    for _ in range(2):
        pick_id = add_pick(session_id, "safe", "Vainqueur tournoi", "X", settings=migrated)
        set_result(pick_id, "win", migrated)

    ligne = analysis(migrated).by_tier[0]

    assert (ligne.settled, ligne.units, ligne.unattached) == (2, 2, 2)


def test_les_paris_en_attente_ne_comptent_pas_d_evenement(migrated: Settings) -> None:
    """L'effectif accompagne le taux, qui ne porte que sur les tranchees."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win")
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated, **INDEP)

    ligne = analysis(migrated).by_tier[0]

    assert (ligne.settled, ligne.units, ligne.pending) == (1, 1, 1)


def test_la_page_dit_l_effectif_independant(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for marche in ("Vainqueur", "Hand. jeux", "O/U jeux"):
        _propose(isolated_settings, session_id, event_id, "safe", "win", market=marche)

    page = " ".join(client.get("/stats").text.split())

    assert "3 sélection(s) tranchée(s) · 1 événement(s) distinct(s)" in page
    assert "optimistes" in page, "les intervalles supposent l'independance"
    assert "ne s'estime pas proprement" in page, "aucune correction n'est tentee"


def test_la_page_dit_aussi_quand_rien_ne_se_recoupe(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le dire evite de chercher un biais absent — c'est le cas des donnees
    reelles, 90 selections pour 87 evenements."""
    session_id, premier = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, premier, "safe", "win")
    _, second = _session_avec_match(isolated_settings, "tennis")
    _propose(isolated_settings, session_id, second, "safe", "win")

    page = " ".join(client.get("/stats").text.split())

    assert "2 sélection(s) tranchée(s) · 2 événement(s) distinct(s)" in page
    assert "rien ne se compte deux fois" in page
    assert "optimistes" not in page


# -- Colinearite entre axes -------------------------------------------------


def _axe(nom: str, *lots: list[int]) -> tuple[str, list]:
    """Un axe factice : une ligne par lot d'identifiants de selection."""
    from myassistantbet.services.history import RateRow

    return (
        nom,
        [
            RateRow(key=str(index), label=f"{nom} {index}", members=set(lot))
            for index, lot in enumerate(lots)
        ],
    )


def test_deux_ensembles_identiques_sont_signales() -> None:
    memes = list(range(20))

    trouves = _overlaps([_axe("sport", memes), _axe("niveau", memes)])

    assert len(trouves) == 1
    assert trouves[0].shared == 20
    assert trouves[0].note == "sport 0 et niveau 0 décrivent les mêmes 20 sélections"


def test_deux_ensembles_disjoints_ne_le_sont_pas() -> None:
    trouves = _overlaps([_axe("sport", list(range(20))), _axe("niveau", list(range(20, 40)))])

    assert trouves == []


def test_un_sous_ensemble_n_est_pas_le_meme_echantillon() -> None:
    """Le recouvrement se mesure **des deux cotes** : vingt selections toutes
    contenues dans quarante ne decrivent pas le meme echantillon, elles en
    decrivent une moitie."""
    trouves = _overlaps([_axe("sport", list(range(40))), _axe("niveau", list(range(20)))])

    assert trouves == []


def test_un_recouvrement_partiel_reste_sous_le_seuil() -> None:
    trouves = _overlaps([_axe("sport", list(range(20))), _axe("niveau", list(range(1, 21)))])

    assert trouves == [], "19 communes sur 20 font 95 %, il en faut plus"


def test_deux_lignes_trop_courtes_ne_sont_pas_comparees() -> None:
    """Deux regroupements d'une selection partagee se recouvrent a 100 % sans
    rien dire : le seuil de lecture de la page vaut aussi ici."""
    trouves = _overlaps([_axe("sport", [1, 2]), _axe("niveau", [1, 2])])

    assert trouves == []


def test_deux_lignes_du_meme_axe_ne_se_comparent_jamais(migrated: Settings) -> None:
    """Un axe partitionne les selections : ses lignes sont disjointes par
    construction, les comparer entre elles ne peut rien produire."""
    axe = _axe("sport", list(range(20)), list(range(20)))

    assert _overlaps([axe]) == []


def test_le_tennis_et_son_niveau_de_tournoi_sont_signales(migrated: Settings) -> None:
    """Le cas reel : 100 % des selections tennis sont sur le Canadian Open, si
    bien que « Tennis » et « Masters 1000 » sont les memes selections."""
    session_id, event_id = _session_avec_match(migrated, "tennis")
    with db.connect(migrated) as conn:
        conn.execute(
            "UPDATE competitions SET category = 'masters_1000' "
            "WHERE id = (SELECT competition_id FROM events WHERE id = ?)",
            (event_id,),
        )
    # Les marches sont varies a dessein : sur un lot ou toutes les selections
    # portent le meme marche, celui-ci est **lui aussi** colineaire au sport, et
    # le detecteur a raison de le dire. Ce test-la porte sur le sport et son
    # niveau de tournoi, pas sur un echantillon degenere.
    for index in range(2 * ANALYSIS_MIN_ROWS):
        _propose(
            migrated,
            session_id,
            event_id,
            "safe",
            "win",
            market="Vainqueur" if index % 2 else "O/U jeux",
        )

    report = analysis(migrated)

    assert [overlap.note for overlap in report.overlaps] == [
        f"Tennis et Masters 1000 décrivent les mêmes {2 * ANALYSIS_MIN_ROWS} sélections"
    ]


def test_le_bloc_colineaire_est_signale_jamais_masque(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Lequel des deux axes est le bon ne se deduit d'aucune donnee : choisir a
    la place du lecteur serait pire que le lui dire."""
    session_id, event_id = _session_avec_match(isolated_settings, "tennis")
    with db.connect(isolated_settings) as conn:
        conn.execute(
            "UPDATE competitions SET category = 'masters_1000' "
            "WHERE id = (SELECT competition_id FROM events WHERE id = ?)",
            (event_id,),
        )
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(isolated_settings, session_id, event_id, "safe", "win")

    page = " ".join(client.get("/stats").text.split())

    assert "décrivent les mêmes" in page
    assert "Par niveau de compétition" in page, "le bloc reste affiche"
    assert "compter deux fois la même chose" in page


# -- Intervalles de confiance -----------------------------------------------


def test_wilson_sur_un_petit_echantillon() -> None:
    """Une victoire sur un pari : le taux vaut 100 % et ne prouve rien.

    L'intervalle normal donnerait ici une largeur **nulle** — la borne haute
    comme la basse a 100 % — soit l'exact contraire de ce qu'il faut lire.
    """
    low, high = wilson(1, 1)

    assert low == pytest.approx(0.2065, abs=1e-4)
    assert high == pytest.approx(1.0)
    assert low <= 1.0


def test_wilson_sur_zero_reussite() -> None:
    """0/6 — le cas ULTRA FUN. La borne basse touche zero sans le franchir, et
    la borne haute dit ce que six paris ne permettent pas d'ecarter."""
    low, high = wilson(0, 6)

    assert low == pytest.approx(0.0, abs=1e-3)
    assert high == pytest.approx(0.3903, abs=1e-4)
    assert 0.0 <= low <= high <= 1.0


def test_wilson_se_resserre_quand_l_echantillon_grandit() -> None:
    court = wilson(5, 10)
    long = wilson(50, 100)

    assert court[1] - court[0] > long[1] - long[0]
    assert long[0] <= 0.5 <= long[1], "50 % sur 100 paris ne tranche toujours pas"


def test_wilson_sans_rien_de_tranche() -> None:
    assert wilson(0, 0) is None


def test_un_taux_dont_l_intervalle_contient_50_ne_tranche_pas(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for index in range(10):
        _propose(migrated, session_id, event_id, "safe", "win" if index < 6 else "loss")

    ligne = analysis(migrated).by_tier[0]

    assert ligne.rate == pytest.approx(0.6)
    assert ligne.inconclusive, "6/10 ne dit pas qu'on passe plus souvent qu'a pile ou face"
    assert ligne.interval_label == "[31 – 83]"


def test_un_taux_tranche_quand_l_intervalle_ecarte_50(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(12):
        _propose(migrated, session_id, event_id, "safe", "win")

    ligne = analysis(migrated).by_tier[0]

    assert not ligne.inconclusive
    assert ligne.interval[0] > 0.5


def test_indecis_et_trop_court_sont_deux_causes_distinctes(migrated: Settings) -> None:
    """Une ligne peut porter assez de paris et rester indecise, et une ligne
    courte peut trancher : 0/6 n'atteint pas le seuil et exclut pourtant 50 %."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(6):
        _propose(migrated, session_id, event_id, "ultra_fun", "loss")
    for index in range(12):
        _propose(migrated, session_id, event_id, "safe", "win" if index < 7 else "loss")

    par_palier = {row.key: row for row in analysis(migrated).by_tier}

    assert (par_palier["ultra_fun"].thin, par_palier["ultra_fun"].inconclusive) == (True, False)
    assert (par_palier["safe"].thin, par_palier["safe"].inconclusive) == (False, True)


def test_la_page_materialise_l_intervalle_et_dit_la_regle(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for index in range(10):
        _propose(isolated_settings, session_id, event_id, "safe", "win" if index < 6 else "loss")

    report = analysis(isolated_settings)
    page = " ".join(client.get("/stats").text.split())

    assert report.undecided_rows, "6/10 ne tranche pas"
    assert 'class="ci"' in page, "l'intervalle est materialise sur la barre, pas ecrit"
    assert "intervalle à 95 %" in page
    assert "contient 50 %" in page
    assert "[31 – 83]" in page, "le detail chiffre, lui, l'ecrit"


# -- Le taux implicite ------------------------------------------------------


def test_le_taux_implicite_est_la_moyenne_des_inverses_de_cote(migrated: Settings) -> None:
    """`1/cote` sur des selections deja tranchees : de l'arithmetique sur un
    prix connu, pas une prevision."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(migrated, session_id, event_id, "safe", "win", price="2.00")

    ligne = analysis(migrated).by_tier[0]

    assert ligne.priced == ANALYSIS_MIN_ROWS
    assert ligne.implied == pytest.approx(0.5)
    assert ligne.implied_label == "50 %"


def test_l_ecart_est_signe_et_compte_en_points(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    # Cote 2.00 partout : taux implicite 50 %. Six gagnees sur huit, soit 75 %.
    for index in range(ANALYSIS_MIN_ROWS):
        _propose(
            migrated,
            session_id,
            event_id,
            "safe",
            "win" if index < 6 else "loss",
            price="2.00",
        )

    ligne = analysis(migrated).by_tier[0]

    assert ligne.rate == pytest.approx(0.75)
    assert ligne.gap == pytest.approx(0.25)
    assert ligne.gap_label == "+25 pts"


def test_les_selections_sans_cote_sortent_du_seul_taux_implicite(migrated: Settings) -> None:
    """Une cote manquante ne retire pas la selection du taux constate : les 88
    selections anterieures a cette colonne restent comptees, elles n'entrent
    simplement pas dans la moyenne des prix."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(migrated, session_id, event_id, "safe", "win", price="2.00")
    for _ in range(4):
        _propose(migrated, session_id, event_id, "safe", "win")

    ligne = analysis(migrated).by_tier[0]

    assert ligne.settled == ANALYSIS_MIN_ROWS + 4, "toutes comptent au taux constate"
    assert ligne.priced == ANALYSIS_MIN_ROWS, "seules les cotees comptent au taux implicite"
    assert ligne.implied == pytest.approx(0.5), "les non cotees ne tirent pas la moyenne"
    assert ligne.priced_note == "8 cotée(s)", "les deux denominateurs different : le dire"


def test_sans_aucune_cote_les_deux_colonnes_se_taisent(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(migrated, session_id, event_id, "safe", "win")

    ligne = analysis(migrated).by_tier[0]

    assert ligne.implied is None
    assert (ligne.implied_label, ligne.gap_label) == ("—", "—")
    assert ligne.priced_note == "", "aucune cote : il n'y a pas deux denominateurs a opposer"


def test_le_seuil_de_lecture_s_applique_aussi_aux_cotes(migrated: Settings) -> None:
    """Meme seuil que le taux : une moyenne sur sept prix decrit sept paris."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS - 1):
        _propose(migrated, session_id, event_id, "safe", "win", price="2.00")

    ligne = analysis(migrated).by_tier[0]

    assert ligne.priced == ANALYSIS_MIN_ROWS - 1
    assert ligne.implied is None
    assert ligne.gap is None


def test_une_cote_a_un_ou_moins_est_refusee(migrated: Settings) -> None:
    """Ce serait un taux implicite d'au moins 100 %, donc pas une cote."""
    session_id, _ = _session_avec_match(migrated)

    for valeur in ("1.00", "0.80"):
        with pytest.raises(HistoryError, match="Cote"):
            add_pick(session_id, "safe", "O/U", "Over", price=valeur, settings=migrated)


def test_la_page_publie_les_deux_colonnes_et_leur_mode_d_emploi(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(isolated_settings, session_id, event_id, "safe", "win", price="2.00")

    page = " ".join(client.get("/stats").text.split())

    assert "Taux implicite" in page
    assert "négatif par construction" in page, "l'ecart ne se lit pas dans l'absolu"
    assert "les uns par rapport aux autres" in page


def test_aucun_mot_financier_dans_le_rendu_des_cotes(
    client: TestClient, isolated_settings: Settings
) -> None:
    """La colonne parle de prix ; elle ne doit pas ouvrir la porte au reste."""
    session_id, event_id = _session_avec_match(isolated_settings)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(isolated_settings, session_id, event_id, "safe", "win", price="2.00")

    html = client.get("/stats").text
    # Le balisage est retire avant de chercher : c'est le **texte lu** qui ne
    # doit porter aucun de ces mots. Sans cela le test echoue sur la classe CSS
    # `bar-value`, qui n'apparait sur aucun ecran, et se ferait « corriger » en
    # affaiblissant l'assertion.
    page = " ".join(re.sub(r"<[^>]+>", " ", html).split()).lower()

    # « espérance » et « gain » ne sont pas dans cette liste, et ce n'est pas un
    # oubli : la page les **nomme pour les refuser**, comme le template de
    # prompt. Les bannir ferait supprimer l'interdiction elle-meme — c'est
    # justement ce qu'il faut garder maintenant que la colonne existe. Ne
    # restent bannis que les mots qu'aucune phrase de refus n'emploie.
    for interdit in ("roi", "bankroll", "kelly", "clv", "value", "edge", "profit"):
        assert not re.search(rf"\b{interdit}\b", page), (
            f"« {interdit} » n'a rien a faire sur cette page"
        )

    assert "aucun indicateur financier n'est produit" in page
    assert "ne fait pas exception" in page, (
        "le taux implicite est le cas limite : la page doit le dire, pas l'omettre"
    )


def test_les_seuils_de_la_page_sont_ceux_du_prompt() -> None:
    """Sous quel compte un taux ne veut rien dire est une propriete des donnees,
    pas de la surface qui les affiche. Les copier des deux cotes les aurait fait
    diverger, et la page aurait fini par publier ce que le prompt refuse."""
    assert (ANALYSIS_MIN_TOTAL, ANALYSIS_MIN_ROWS, ANALYSIS_MIN_DAYS) == (
        FEEDBACK_MIN_TOTAL,
        FEEDBACK_MIN_ROWS,
        FEEDBACK_MIN_DAYS,
    )


def _le_jour(settings: Settings, pick_id: int, jour: int) -> None:
    """Date la **decision**, pas le match : c'est ce que compte l'etalement."""
    db.execute(
        "UPDATE picks SET created_at = ? WHERE id = ?",
        (f"2026-07-{jour:02d}T12:00:00Z", pick_id),
        settings=settings,
    )


def test_l_analyse_compte_les_journees_d_analyse(migrated: Settings) -> None:
    """Deux paris pris dans la meme seance restent une seule seance : c'est la
    journee de la decision qui compte, et deux picks du meme jour n'en font
    qu'une."""
    session_id, event_id = _session_avec_match(migrated)
    for jour in (1, 1, 2):
        _le_jour(migrated, _propose(migrated, session_id, event_id, "safe", "win"), jour)
    # En attente : sans resultat, elle ne compte ni au total ni a l'etalement.
    _le_jour(
        migrated,
        add_pick(
            session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated, **INDEP
        ),
        9,
    )

    report = analysis(migrated)

    assert (report.settled, report.days) == (3, 2)


def test_un_echantillon_concentre_est_annonce_sur_la_page(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le garde-fou d'etalement du prompt, dit au lieu d'etre applique en
    silence : 71 selections prises en quatre jours restent une semaine de paris.
    « Tennis 46 % » et « Masters 1000 46 % » y etaient les memes 35 matchs sous
    deux noms, presentes comme deux observations independantes.

    La page, elle, continue d'afficher : c'est la surface ou l'utilisateur vient
    regarder ses propres donnees."""
    session_id, event_id = _session_avec_match(isolated_settings)
    for index in range(ANALYSIS_MIN_TOTAL):
        _le_jour(
            isolated_settings,
            _propose(isolated_settings, session_id, event_id, "safe", "win"),
            1 + index % (ANALYSIS_MIN_DAYS - 1),
        )

    report = analysis(isolated_settings)
    # Le texte est mis en forme sur plusieurs lignes : chercher une phrase
    # entiere dans le HTML brut la couperait au premier retour a la ligne.
    page = " ".join(client.get("/stats").text.split())

    assert report.settled >= report.minimum, "le volume, lui, est atteint"
    assert not report.enough, "mais pas l'etalement, et il faut les deux"
    assert "journée(s) d'analyse" in page
    assert "les mêmes matchs sous deux noms" in page
    assert '<span class="bar-count">' in page, "les chiffres restent affiches"


def test_un_echantillon_etale_ne_declenche_aucun_avertissement(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    for index in range(ANALYSIS_MIN_TOTAL):
        _le_jour(
            isolated_settings,
            _propose(isolated_settings, session_id, event_id, "safe", "win"),
            1 + index % ANALYSIS_MIN_DAYS,
        )

    assert analysis(isolated_settings).enough
    assert "journée(s) d'analyse" not in client.get("/stats").text


def test_une_ligne_trop_courte_est_marquee_et_jamais_tue(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le prompt tait ces lignes — Claude ne peut pas savoir qu'il lit trois
    paris. La page les garde et les pale : lui cacher ses propres donnees
    repondrait a cote de la question qu'elle pose."""
    session_id, event_id = _session_avec_match(isolated_settings)
    for _ in range(ANALYSIS_MIN_ROWS - 1):
        _propose(isolated_settings, session_id, event_id, "safe", "win", market="O/U 2.5")

    report = analysis(isolated_settings)
    page = " ".join(client.get("/stats").text.split())

    assert [row.label for row in report.by_market] == ["O/U 2.5"], "la ligne existe toujours"
    assert report.by_market[0].thin
    # Palier, sport, marche et **famille** — ni confiance ni niveau ici. La
    # famille compte comme les autres : regrouper des libelles ne fabrique pas
    # de l'effectif, et sept paris restent sept paris sous « Total ».
    assert report.thin_rows == 4
    assert "is-thin" in page
    assert f"moins de {ANALYSIS_MIN_ROWS}" in page


def test_une_ligne_assez_fournie_n_est_plus_marquee(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(migrated, session_id, event_id, "safe", "win")

    report = analysis(migrated)

    assert not report.by_tier[0].thin
    assert report.thin_rows == 0


def test_le_taux_global_reunit_joue_et_ecarte(migrated: Settings) -> None:
    """Deduit des deux populations : deux comptages du meme ensemble divergent."""
    session_id, event_id = _session_avec_match(migrated)
    _joue(migrated, session_id, event_id, "safe", "win")
    _propose(migrated, session_id, event_id, "fun", "loss")

    report = analysis(migrated)

    assert (report.overall.won, report.overall.lost) == (1, 1)
    assert report.overall.won == report.played.won + report.skipped.won


# -- Le « pourquoi » : type d'angle et niveau de source ---------------------


def test_le_type_d_angle_produit_son_regroupement(migrated: Settings) -> None:
    """Toutes les autres dimensions sont des etiquettes de forme : un palier est
    une bande de cote, un marche un libelle. Celle-ci dit **sur quoi** la
    selection reposait."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", angle="issue")
    _propose(migrated, session_id, event_id, "safe", "loss", angle="maniere")
    _propose(migrated, session_id, event_id, "safe", "win", angle="maniere")

    report = analysis(migrated)

    assert [(row.label, row.won, row.settled) for row in report.by_angle] == [
        ("Issue", 1, 1),
        ("Manière", 1, 2),
    ]


def test_le_niveau_de_source_suit_l_echelle_et_non_l_effectif(migrated: Settings) -> None:
    """« Lecture seule » ferme la marche parce que c'est sa place dans l'echelle,
    pas parce qu'elle serait la plus ou la moins nombreuse. C'est elle qu'on veut
    comparer au reste, et la voir a sa place vaut mieux que de la voir en tete."""
    session_id, event_id = _session_avec_match(migrated)
    for niveau in ("lecture", "lecture", "lecture", "2", "4"):
        _propose(migrated, session_id, event_id, "safe", "win", source_level=niveau)

    assert [row.key for row in analysis(migrated).by_source] == ["2", "4", "lecture"]


def test_lecture_seule_n_est_pas_une_absence(migrated: Settings) -> None:
    """C'est **la** distinction que la mesure existe pour faire : « aucun fait
    date ne porte cette selection » est une reponse, « je n'ai rien renseigne »
    n'en est pas une. Un entier nullable aurait ecrase la premiere sur la seconde.
    """
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", source_level="lecture")
    _propose(migrated, session_id, event_id, "safe", "loss")

    report = analysis(migrated)

    assert [row.key for row in report.by_source] == ["lecture"]
    assert report.unlabelled_source == 1
    assert sum(row.settled for row in report.by_source) + report.unlabelled_source == report.settled


def test_les_selections_anterieures_aux_colonnes_sont_comptees(migrated: Settings) -> None:
    """Cent selections en base n'en portent aucune : les taire ferait lire les
    regroupements comme s'ils couvraient tout l'historique."""
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win", angle="issue")
    _propose(migrated, session_id, event_id, "safe", "win")

    report = analysis(migrated)

    assert report.unlabelled_angle == 1
    assert sum(row.settled for row in report.by_angle) + report.unlabelled_angle == report.settled


def test_le_pourquoi_entre_dans_le_detecteur_de_recouvrement(migrated: Settings) -> None:
    """Un lot ou toutes les manieres se traduisent en totaux ferait de « Manière »
    et « O/U » deux noms du meme echantillon, presentes comme deux constats."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(ANALYSIS_MIN_ROWS):
        _propose(migrated, session_id, event_id, "safe", "win", market="O/U", angle="maniere")

    notes = [overlap.note for overlap in analysis(migrated).overlaps]

    assert any("Manière" in note and "O/U" in note for note in notes)


def test_les_deux_cartes_du_pourquoi_sont_affichees(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(
        isolated_settings, session_id, event_id, "safe", "win", angle="issue", source_level="2"
    )

    page = " ".join(client.get("/stats").text.split())

    assert "Par type d'angle" in page
    assert "Par niveau de source" in page
    assert "2 · presse" in page


# -- Ce que j'ecarte : le lot d'une session ---------------------------------


def _prompt_sur(settings: Settings, session_id: int, event_ids: list[int], body: str = "") -> int:
    """Archive un prompt portant ces matchs-la."""
    with db.connect(settings) as conn:
        cursor = conn.execute(
            "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
            "VALUES (?, 'session_default.md.j2', ?, 1000, '2026-08-04T10:00:00Z')",
            (session_id, body),
        )
        prompt_id = int(cursor.lastrowid)
        conn.executemany(
            "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            [(prompt_id, event_id) for event_id in event_ids],
        )
    return prompt_id


def test_le_lot_est_l_union_des_matchs_entres_dans_un_prompt(migrated: Settings) -> None:
    """Deux prompts par competition ne decrivent pas deux lots, mais un seul.

    Un maximum par prompt ne verrait que le plus gros morceau : sur une session
    scindee en 12 matchs de football et 18 de tennis, il annoncerait 18 la ou
    l'analyse en a vu 30.
    """
    session_id, premier = _session_avec_match(migrated, "football")
    _, second = _session_avec_match(migrated, "tennis")
    _prompt_sur(migrated, session_id, [premier])
    _prompt_sur(migrated, session_id, [second])
    # Une regeneration a l'identique : elle ne doit rien ajouter.
    _prompt_sur(migrated, session_id, [premier, second])

    assert lots(migrated)[session_id] == Lot(size=2, reconstructed=False)


def test_une_session_sans_prompt_n_a_pas_de_lot(migrated: Settings) -> None:
    """Rien n'a ete soumis a l'analyse : lui preter un lot de zero inventerait
    un taux de selection la ou il n'y a pas de mesure."""
    session_id, _ = _session_avec_match(migrated)

    assert session_id not in lots(migrated)
    ligne = next(row for row in analysis(migrated).by_session if row.session_id == session_id)
    assert (ligne.lot, ligne.selection_rate, ligne.passed) == (None, None, None)


def test_un_lot_ancien_se_reconstruit_depuis_le_corps_archive(migrated: Settings) -> None:
    """L'information dormait deja en base : les corps de prompts sont stockes.

    Les prompts anterieurs a `prompt_events` n'ont aucune ligne de rattachement,
    et leur lot serait sinon perdu — donc tout l'historique deviendrait muet sur
    le seul chiffre que cette phase installe.
    """
    session_id, _ = _session_avec_match(migrated)
    corps = (
        "## MATCHS\n"
        "### M1 · FOOT · Eredivisie · Sparta – Feyenoord · 09/08 12:15\n"
        "### M2 · FOOT · Super League · Henan – Qingdao · 09/08 13:00\n"
    )
    suite = "### M1 · FOOT · Super League · Henan – Qingdao · 09/08 13:00\n"
    with db.connect(migrated) as conn:
        for body in (corps, suite):
            conn.execute(
                "INSERT INTO prompts (session_id, template_name, body, token_estimate, "
                "created_at) VALUES (?, 't', ?, 10, '2026-08-04T10:00:00Z')",
                (session_id, body),
            )

    # Deux matchs distincts : le second prompt renumerote Henan en M1, et c'est
    # bien l'identite du match — pas son numero de bloc — qui les rapproche.
    assert lots(migrated)[session_id] == Lot(size=2, reconstructed=True)


def test_un_lot_enregistre_prime_sur_la_reconstruction(migrated: Settings) -> None:
    """Sinon une session mi-ancienne mi-nouvelle compterait ses matchs deux fois."""
    session_id, event_id = _session_avec_match(migrated)
    _prompt_sur(migrated, session_id, [event_id], body="### M1 · FOOT · X · A – B · 09/08 12:15\n")

    assert lots(migrated)[session_id] == Lot(size=1, reconstructed=False)


def test_une_session_sans_selection_reste_visible_avec_zero(migrated: Settings) -> None:
    """C'est le cas le plus interessant du bloc : un lot entierement passe.

    Il existe dans l'historique reel — 0 selection sur 34 matchs — et le retirer
    ferait disparaitre la seule session ou le tri a vraiment trie.
    """
    session_id, event_id = _session_avec_match(migrated)
    _prompt_sur(migrated, session_id, [event_id])

    ligne = next(row for row in analysis(migrated).by_session if row.session_id == session_id)

    assert (ligne.lot, ligne.covered, ligne.passed) == (1, 0, 1)
    assert ligne.selection_rate == 0.0


def test_deux_selections_sur_un_match_ne_font_qu_un_match_retenu(migrated: Settings) -> None:
    """« Ai-je passe ce match ? » se compte en matchs, jamais en lignes.

    Sans cette regle, le taux de selection depasserait cent pour cent des qu'un
    match porte un vainqueur et un total — ce qui arrive sur un lot sur trois.
    """
    session_id, event_id = _session_avec_match(migrated)
    _, autre = _session_avec_match(migrated)
    _prompt_sur(migrated, session_id, [event_id, autre])
    _propose(migrated, session_id, event_id, "safe", "win", market="Vainqueur")
    _propose(migrated, session_id, event_id, "fun", "loss", market="O/U")

    ligne = next(row for row in analysis(migrated).by_session if row.session_id == session_id)

    assert (ligne.picks, ligne.covered, ligne.lot) == (2, 1, 2)
    assert ligne.selection_rate == 0.5


def test_une_selection_hors_lot_est_dite_jamais_rabotee(migrated: Settings) -> None:
    """Le voisinage propose au rattachement offre des matchs hors du lot.

    Les compter au numerateur donnerait un taux au-dessus de cent ; les jeter
    ferait disparaitre une selection reelle. Ils sont donc comptes a part.
    """
    session_id, dans_le_lot = _session_avec_match(migrated)
    _, dehors = _session_avec_match(migrated)
    _prompt_sur(migrated, session_id, [dans_le_lot])
    _propose(migrated, session_id, dans_le_lot, "safe", "win")
    _propose(migrated, session_id, dehors, "safe", "win")

    ligne = next(row for row in analysis(migrated).by_session if row.session_id == session_id)

    assert (ligne.lot, ligne.covered, ligne.outside) == (1, 2, 1)
    assert ligne.passed == 0, "aucun match du lot n'a ete passe"


def test_le_bloc_par_session_est_affiche(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session_avec_match(isolated_settings)
    _, passe = _session_avec_match(isolated_settings)
    _prompt_sur(isolated_settings, session_id, [event_id, passe])
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    page = " ".join(client.get("/stats").text.split())

    assert "Ce que tu écartes" in page
    assert "Par session" in page
    assert "50 %" in page
    assert "matchs entrés dans un prompt" in page


# -- Graphiques de la page statistiques -------------------------------------


def test_la_barre_a_la_largeur_du_taux(client: TestClient, isolated_settings: Settings) -> None:
    """Une barre est une part de 100 % : l'echelle est fixe, rien a normaliser."""
    session_id, event_id = _session_avec_match(isolated_settings)
    for resultat in ("win", "win", "win", "loss"):
        _propose(isolated_settings, session_id, event_id, "safe", resultat)

    response = client.get("/stats")

    assert "75 %" in response.text
    assert 'style="width: 75.0%"' in response.text


def test_le_taux_ne_s_affiche_jamais_sans_son_compte(
    client: TestClient, isolated_settings: Settings
) -> None:
    """« 100 % » sur un pari et « 100 % » sur quarante ne disent pas la meme chose."""
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    response = client.get("/stats")

    assert '<span class="bar-count">1/1</span>' in response.text


def test_le_taux_par_niveau_de_tournoi(client: TestClient, isolated_settings: Settings) -> None:
    """Un Grand Chelem et un 250 ne se jouent ni au meme format ni contre les memes."""
    session_id, event_id = _session_avec_match(isolated_settings, "tennis")
    db.execute(
        "UPDATE competitions SET category = 'grand_slam' WHERE label = 'Amical'",
        settings=isolated_settings,
    )
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    report = analysis(isolated_settings)

    assert [row.label for row in report.by_category] == ["Grand Chelem"]
    assert "Par niveau de compétition" in client.get("/stats").text


def test_un_niveau_absent_ne_produit_pas_de_ligne(
    client: TestClient, isolated_settings: Settings
) -> None:
    """« Non renseigne » ne dirait rien sur les matchs, seulement sur la saisie."""
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    assert analysis(isolated_settings).by_category == []
    assert "Par niveau de compétition" not in client.get("/stats").text


def test_reclasser_une_competition_reclasse_tout_son_historique(migrated: Settings) -> None:
    """Le niveau se resout **a la lecture**, il n'est jamais recopie sur le pick.

    C'est ce qui rend une taxonomie corrigeable : reclasser une competition
    doit reclasser tout ce qu'elle porte, sans migration et sans reprise de
    donnees. Denormalise sur la selection, il aurait fallu repasser sur cent
    lignes a chaque hesitation de decoupage.
    """
    session_id, event_id = _session_avec_match(migrated, "tennis")
    _propose(migrated, session_id, event_id, "safe", "win")
    competition = db.query_one(
        "SELECT competition_id AS id FROM events WHERE id = ?",
        (event_id,),
        settings=migrated,
    )["id"]

    set_category(competition, "grand_slam", migrated)
    assert [row.label for row in analysis(migrated).by_category] == ["Grand Chelem"]

    set_category(competition, "level_250", migrated)
    assert [row.label for row in analysis(migrated).by_category] == ["ATP/WTA 250"]


def test_aucune_selection_ne_sort_du_compte_par_niveau(
    client: TestClient, isolated_settings: Settings
) -> None:
    """L'addition se ferme : la somme des niveaux plus les non classees.

    Une barre « non renseigne » n'aurait aucune coherence sportive, mais un
    **compte** est une information juste. Sans lui, des selections quittaient le
    regroupement sans qu'une seule ligne ne le signale — exactement ce qui a
    rendu tout le football invisible.
    """
    session_id, classe = _session_avec_match(isolated_settings, "tennis")
    _, sans_niveau = _session_avec_match(isolated_settings)
    db.execute(
        "UPDATE competitions SET category = 'grand_slam' "
        "WHERE id = (SELECT competition_id FROM events WHERE id = ?)",
        (classe,),
        settings=isolated_settings,
    )
    _propose(isolated_settings, session_id, classe, "safe", "win")
    _propose(isolated_settings, session_id, sans_niveau, "safe", "loss")
    # Une selection qu'aucun match ne porte : deuxieme cause d'absence de niveau.
    orpheline = add_pick(session_id, "safe", "O/U", "Over", settings=isolated_settings)
    set_result(orpheline, "loss", isolated_settings)

    report = analysis(isolated_settings)

    assert sum(row.settled for row in report.by_category) + report.uncategorised == report.settled
    assert report.uncategorised == 2
    assert "2 sélection(s) tranchée(s) ne portent aucun niveau" in " ".join(
        client.get("/stats").text.split()
    )


def test_la_page_de_stats_porte_l_interdiction(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le garde-fou compte autant que le chiffre (SPEC.md section 9).

    La page rapproche desormais un taux d'un `1/cote` — c'est tout l'objet de la
    colonne « Taux implicite » — et l'interdiction n'a donc plus le meme
    perimetre qu'avant : ce n'est pas le rapprochement qui est interdit, c'est
    d'en tirer ce que rapporterait la suite. Le mot doit rester sur la page
    justement parce que la colonne existe : c'est maintenant qu'il sert.
    """
    session_id, event_id = _session_avec_match(isolated_settings)
    _joue(isolated_settings, session_id, event_id, "safe", "win")

    page = client.get("/stats").text

    assert "Aucun indicateur financier n'est produit" in page
    assert "espérance" in page


def test_la_recherche_leve_la_fenetre_de_temps(migrated: Settings) -> None:
    """Le voisinage de la session couvre la journee de travail, pas un match
    joue la semaine d'avant ni un report. Quand le match cherche n'est nulle
    part dans le menu, il n'y avait plus aucun recours : la selection restait
    sans evenement, donc sans sport, donc muette dans les statistiques."""
    session_id, _ = _session_avec_match(migrated, "football")
    lointain = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Alcaraz",
            "Sinner",
            "2026-06-01",  # bien au-dela de PICKABLE_BEFORE_H
            "18:00",
            "Alcaraz 1.80",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )

    sans = {e.event_id for e in pickable_events(session_id, migrated)}
    avec = {e.event_id for e in pickable_events(session_id, migrated, "Alcaraz")}

    assert lointain not in sans, "hors fenetre, le match n'est pas propose"
    assert lointain in avec, "la recherche par libelle doit le retrouver"


def test_la_recherche_porte_aussi_sur_la_competition(migrated: Settings) -> None:
    """On se souvient parfois du tournoi, pas des deux noms."""
    session_id, _ = _session_avec_match(migrated, "football")
    save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Alcaraz",
            "Sinner",
            "2026-06-01",
            "18:00",
            "Alcaraz 1.80",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )

    trouves = pickable_events(session_id, migrated, "Canadian")

    assert any(e.home == "Alcaraz" for e in trouves)


def test_une_recherche_sans_resultat_ne_rend_rien(migrated: Settings) -> None:
    """Et surtout pas la liste ordinaire : on croirait avoir trouve."""
    session_id, _ = _session_avec_match(migrated, "football")

    assert pickable_events(session_id, migrated, "zzzz-introuvable") == []


def test_les_groupes_sont_ranges_par_heure(migrated: Settings) -> None:
    """Ils l'etaient par identifiant de sport puis par nom de competition :
    « Bundesliga 2 » passait devant « Premier League » pour des raisons
    alphabetiques. Une session se relit dans l'ordre ou elle s'est jouee."""
    session_id, _ = _session_avec_match(migrated, "football")  # Amical, 20:00
    tot = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "12:00",
            "Moutet 1.80",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(tot, True, migrated)

    noms = [nom for nom, _ in pickable_groups(session_id, migrated)]

    assert noms.index("Tennis · ATP Canadian Open") < noms.index("Football · Amical")


# -- Une seconde selection sur le meme match --------------------------------


def test_une_seconde_selection_sur_un_match_exige_sa_justification(migrated: Settings) -> None:
    """Cent selections pour 97 evenements : trois matchs en portent deux.

    Le prompt l'autorise et l'encadre depuis toujours — « deux selections sur un
    meme match ne se justifient que si elles reposent sur des angles reellement
    independants, et tu le dis alors explicitement » — mais rien de cette
    justification n'arrivait en base. Elle etait ecrite dans le rendu, lue une
    fois, puis perdue.
    """
    session_id, event_id = _session_avec_match(migrated)
    add_pick(session_id, "safe", "Vainqueur", "Lyon", event_id=str(event_id), settings=migrated)

    with pytest.raises(HistoryError, match="angle réellement indépendant"):
        add_pick(session_id, "fun", "O/U", "Over", event_id=str(event_id), settings=migrated)


def test_la_justification_debloque_et_se_stocke(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    add_pick(session_id, "safe", "Vainqueur", "Lyon", event_id=str(event_id), settings=migrated)

    pick_id = add_pick(
        session_id,
        "fun",
        "O/U",
        "Over",
        event_id=str(event_id),
        independence_note="l'un porte l'issue, l'autre le rythme",
        settings=migrated,
    )

    row = db.query_one(
        "SELECT independence_note FROM picks WHERE id = ?", (pick_id,), settings=migrated
    )
    assert row["independence_note"] == "l'un porte l'issue, l'autre le rythme"


def test_la_premiere_selection_ne_demande_rien(migrated: Settings) -> None:
    """C'est **le seul controle bloquant du module** : l'imposer partout ferait
    d'une exigence de bon sens une corvee sur chaque ligne."""
    session_id, event_id = _session_avec_match(migrated)

    pick_id = add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)

    row = db.query_one(
        "SELECT independence_note FROM picks WHERE id = ?", (pick_id,), settings=migrated
    )
    assert row["independence_note"] is None


def test_le_controle_porte_sur_la_session_et_pas_sur_l_histoire(migrated: Settings) -> None:
    """Deux analyses successives du meme match sont deux decisions distinctes,
    pas une selection doublee : la regle du prompt vaut a l'interieur d'un
    rendu, et la faire porter sur tout l'historique bloquerait un match rejoue
    la semaine suivante."""
    session_id, event_id = _session_avec_match(migrated)
    add_pick(session_id, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)
    with db.connect(migrated) as conn:
        autre = int(
            conn.execute(
                "INSERT INTO sessions (label, created_at) VALUES ('S2', '2026-08-05T10:00:00Z')"
            ).lastrowid
        )

    pick_id = add_pick(autre, "safe", "O/U", "Over", event_id=str(event_id), settings=migrated)

    assert pick_id > 0


def test_une_selection_sans_match_echappe_au_controle(migrated: Settings) -> None:
    """Rien ne permet de les rapprocher : un pari sur un vainqueur de tournoi et
    une ligne dont le rattachement a echoue ne sont pas « le meme match »."""
    session_id, _ = _session_avec_match(migrated)
    add_pick(session_id, "safe", "Combiné", "3 sélections", settings=migrated)

    assert add_pick(session_id, "safe", "Combiné", "4 sélections", settings=migrated) > 0


def test_la_justification_est_relue_sur_la_feuille(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Une donnee que rien ne lit finit par se retirer — c'est ce qui est arrive
    a l'effectif collecte pendant des mois sans lecteur (migration 022).

    Celle-ci se relit sur la feuille de session : c'est la qu'on voit si deux
    angles etaient vraiment independants ou deux facons de dire la meme chose.
    """
    session_id, event_id = _session_avec_match(isolated_settings)
    add_pick(
        session_id, "safe", "Vainqueur", "Lyon", event_id=str(event_id), settings=isolated_settings
    )
    add_pick(
        session_id,
        "fun",
        "O/U",
        "Over",
        event_id=str(event_id),
        independence_note="issue contre rythme",
        settings=isolated_settings,
    )

    page = " ".join(client.get(f"/history/{session_id}").text.split())

    assert "↳ issue contre rythme" in page


# -- Le denominateur global ne peut pas baisser -----------------------------


def test_une_selection_sans_aucune_dimension_compte_quand_meme(migrated: Settings) -> None:
    """Aucune selection tranchee ne doit pouvoir sortir du denominateur global.

    Une jointure devenue stricte, une cle de competition absente de la table,
    un champ nouveau nul sur l'historique : chacun ferait disparaitre des lignes
    **en silence**, et un compte qui baisse sans que rien ne le dise est la
    panne la plus couteuse que cette page puisse avoir.

    La ligne montee ici cumule tous les cas : aucun match, donc aucun sport et
    aucune competition ; ni type, ni source, ni confiance.
    """
    session_id, _ = _session_avec_match(migrated)
    orpheline = add_pick(session_id, "safe", "Marché exotique", "Oui", settings=migrated)
    set_result(orpheline, "win", migrated)

    report = analysis(migrated)

    assert report.settled == 1
    assert report.recorded == 1, "le temoin lit picks sans jointure"
    assert report.consistent, f"axes en defaut : {[g.line for g in report.gaps]}"
    # Et elle se retrouve dans le compte des non classes de **chaque** axe.
    assert report.uncategorised == 1
    assert report.unlabelled_angle == 1
    assert report.unlabelled_source == 1
    assert report.unlabelled_confidence == 1
    assert report.unclassified_markets == 1


def test_une_competition_hors_taxonomie_ne_fait_pas_baisser_le_total(
    migrated: Settings,
) -> None:
    """Reclasser une competition change la repartition, jamais le total.

    C'est le critere d'acceptation : un rechargement apres ajout d'une cle a la
    taxonomie ne doit deplacer que des lignes entre regroupements.
    """
    session_id, event_id = _session_avec_match(migrated)
    _propose(migrated, session_id, event_id, "safe", "win")
    avant = analysis(migrated)

    set_category(
        db.query_one(
            "SELECT competition_id AS id FROM events WHERE id = ?",
            (event_id,),
            settings=migrated,
        )["id"],
        # Un niveau **de football** : la saisie valide contre la taxonomie du
        # sport, et « grand_slam » y serait refusé.
        "d1_top5",
        migrated,
    )
    apres = analysis(migrated)

    assert avant.settled == apres.settled == avant.recorded == apres.recorded
    assert (avant.uncategorised, apres.uncategorised) == (1, 0)
    assert avant.consistent and apres.consistent


def test_l_ecart_est_annonce_en_clair(client: TestClient, isolated_settings: Settings) -> None:
    """Si le controle casse un jour, la page le dit — elle n'affiche pas un
    denominateur ampute sans un mot."""
    session_id, event_id = _session_avec_match(isolated_settings)
    _propose(isolated_settings, session_id, event_id, "safe", "win")

    page = " ".join(client.get("/stats").text.split())

    assert "ne retombe pas sur ce que porte la base" not in page, "rien a signaler ici"


# -- Angle declare contre marche rendu ---------------------------------------


def _pick(
    settings: Settings,
    session_id: int,
    event_id: int,
    *,
    market: str,
    result: str,
    angle: str = "",
) -> None:
    """Une selection tranchee, avec son angle declare quand il y en a un."""
    pick_id = add_pick(
        session_id,
        tier="fun",
        market=market,
        selection="Peu importe",
        event_id=str(event_id),
        angle=angle,
        settings=settings,
        **INDEP,
    )
    set_result(pick_id, result, settings)


def test_un_angle_de_maniere_rendu_en_vainqueur_est_compte(migrated: Settings) -> None:
    """Le prompt demandait a l'analyse de compter ces lignes elle-meme. Les deux
    colonnes sont en base — l'angle depuis la migration 026, la famille du
    marche depuis la 027 — et le conflit se detecte en une requete.

    C'est une mesure de la **qualite du rendu**, jamais un blocage : la
    selection reste valable, simplement moins fidele a son propre raisonnement.
    """
    session_id, event_id = _session_avec_match(migrated)
    _pick(migrated, session_id, event_id, market="Vainqueur", angle="maniere", result="win")
    _pick(migrated, session_id, event_id, market="Jeux O/U 22.5", angle="maniere", result="loss")
    _pick(migrated, session_id, event_id, market="Vainqueur", angle="issue", result="win")

    report = analysis(migrated)

    assert report.conflicts.labelled == 2, "seules les manieres entrent au denominateur"
    assert report.conflicts.count == 1
    assert report.conflicts.rate == 0.5
    assert report.conflicts.known


def test_le_conflit_suit_le_reclassement_d_un_marche(migrated: Settings) -> None:
    """Il se calcule **a la lecture**, jamais recopie sur la selection : c'est la
    regle du module, et c'est elle qui rend la taxonomie corrigeable. Stocker le
    conflit figerait le classement du jour ou la ligne a ete saisie."""
    session_id, event_id = _session_avec_match(migrated)
    _pick(migrated, session_id, event_id, market="Vainqueur", angle="maniere", result="win")

    assert analysis(migrated).conflicts.count == 1

    market_families.set_family("vainqueur", "total", migrated)

    assert analysis(migrated).conflicts.count == 0, "le reclassement vaut pour tout l'historique"


def test_sans_angle_declare_rien_ne_se_mesure(migrated: Settings) -> None:
    """Les cent premieres selections n'en portent aucun : un taux sur zero ligne
    inventerait une mesure, et la carte ne se rend pas du tout."""
    session_id, event_id = _session_avec_match(migrated)
    _pick(migrated, session_id, event_id, market="Vainqueur", result="win")

    report = analysis(migrated)

    assert not report.conflicts.known
    assert report.conflicts.rate is None


def test_la_page_rend_le_conflit_angle_marche(client: TestClient, migrated: Settings) -> None:
    """C'est une mesure de la qualite du rendu, pas un blocage : la page la
    montre, elle ne refuse rien."""
    session_id, event_id = _session_avec_match(migrated)
    _pick(migrated, session_id, event_id, market="Vainqueur", angle="maniere", result="win")

    page = client.get("/stats").text

    assert "Angle « manière » rendu en vainqueur" in page
    assert "une usure, un déséquilibre" in page
