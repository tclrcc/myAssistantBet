"""Le score exact en sets : la seule mesure de la lecture de la maniere.

Le risque propre a ce module n'est pas de rater un compte, c'est d'en publier un
qui melange deux grandeurs : un score exact et un vainqueur juste ne disent pas
la meme chose, et c'est precisement leur ecart qui rend le releve utile.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import set_scores
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt, save_prompt


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _lot(settings: Settings, matchs: int = 2) -> tuple[int, list[int]]:
    """Une session de tennis dont le prompt a ete archive : c'est ca, un lot."""
    session_id, event_ids = 0, []
    for index in range(matchs):
        event_id = save(
            build(
                "tennis",
                "ATP 250 Gstaad",
                f"Moutet {index}",
                f"Bergs {index}",
                "2099-08-04",
                "20:45",
                f"Moutet {index} 1.85\nBergs {index} 1.95",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        session_id = board_service.toggle_selection(event_id, True, settings)
        event_ids.append(event_id)
    save_prompt(session_id, build_prompt(session_id, settings=settings), settings)
    return session_id, event_ids


def test_le_lot_liste_les_matchs_de_tennis_du_prompt(migrated: Settings) -> None:
    """Le **lot**, pas la shortlist : `session_events` se vide a mesure qu'on
    decoche, quand `prompt_events` enregistre ce qui est vraiment parti a
    l'analyse. C'est la meme population que le taux de selection."""
    session_id, event_ids = _lot(migrated, 2)

    rows = set_scores.lot(session_id, migrated)

    assert [row.event_id for row in rows] == event_ids
    assert all(not row.saisi for row in rows)


def test_une_session_sans_prompt_n_a_pas_de_lot(migrated: Settings) -> None:
    """Rien n'a ete soumis a l'analyse : reclamer un score y serait sans objet."""
    event_id = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Moutet",
            "Bergs",
            "2099-08-04",
            "20:45",
            "Moutet 1.85",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)

    assert set_scores.lot(session_id, migrated) == []


def test_la_saisie_est_idempotente(migrated: Settings) -> None:
    """Cle naturelle `(session, match)` : rejouer la saisie corrige au lieu de
    dupliquer, comme partout ailleurs dans le projet."""
    session_id, (event_id, _) = _lot(migrated)

    set_scores.save(session_id, event_id, "2-0", settings=migrated)
    set_scores.save(session_id, event_id, "2-1", "0-2", settings=migrated)

    rows = [row for row in set_scores.lot(session_id, migrated) if row.event_id == event_id]
    assert len(rows) == 1
    assert (rows[0].predicted, rows[0].alternate) == ("2-1", "0-2")


def test_un_score_annonce_vide_retire_la_ligne(migrated: Settings) -> None:
    """« PASSE » est une reponse attendue sur une partie du lot. Enregistrer une
    ligne vide ferait compter au denominateur un match sur lequel rien n'a ete
    annonce."""
    session_id, (event_id, _) = _lot(migrated)
    set_scores.save(session_id, event_id, "2-0", settings=migrated)

    set_scores.save(session_id, event_id, "", settings=migrated)

    assert set_scores.report(migrated).empty


def test_une_valeur_hors_vocabulaire_vaut_non_renseigne(migrated: Settings) -> None:
    """Meme regle que `angle` et `source_level` : refuser une saisie entiere pour
    un mot inattendu couterait plus que la valeur manquante."""
    session_id, (event_id, _) = _lot(migrated)

    set_scores.save(session_id, event_id, "3-0", settings=migrated)

    assert set_scores.report(migrated).empty


def test_un_second_scenario_identique_est_refuse(migrated: Settings) -> None:
    """Deux fois le meme score n'est pas un second scenario : ce serait un joker
    gratuit sur un taux que ce module existe pour mesurer."""
    session_id, (event_id, _) = _lot(migrated)

    with pytest.raises(set_scores.SetScoreError, match="différer"):
        set_scores.save(session_id, event_id, "2-0", "2-0", settings=migrated)


def test_l_issue_juste_et_la_maniere_fausse_se_separent(migrated: Settings) -> None:
    """**Le chiffre le plus interessant du bloc.** Un `2-0` annonce et un `2-1`
    constate, c'est le bon vainqueur et le mauvais nombre de sets : le
    raisonnement sur le rythme n'a pas porte, alors meme que la selection a pu
    gagner. Les confondre avec un echec, ou avec une reussite, effacerait la
    seule information que ce releve apporte."""
    session_id, (premier, second) = _lot(migrated)
    set_scores.save(session_id, premier, "2-0", actual="2-1", settings=migrated)
    set_scores.save(session_id, second, "2-0", actual="0-2", settings=migrated)

    report = set_scores.report(migrated)

    assert report.settled == 2
    assert report.exact == 0
    assert report.issue_only == 1, "le bon vainqueur, le mauvais nombre de sets"
    assert report.missed == 1
    assert report.exact_rate == 0.0
    assert report.issue_rate == 0.5


def test_le_second_scenario_est_compte_a_part(migrated: Settings) -> None:
    """Deux scores proposes ne valent pas une lecture deux fois plus juste : le
    fusionner avec le premier gonflerait le taux d'un joker facultatif."""
    session_id, (event_id, _) = _lot(migrated)

    set_scores.save(session_id, event_id, "2-0", "2-1", actual="2-1", settings=migrated)

    report = set_scores.report(migrated)
    assert report.exact == 0
    assert report.alternate == 1
    assert report.issue_only == 1, "le vainqueur, lui, etait bon"


def test_un_score_sans_resultat_reste_en_attente(migrated: Settings) -> None:
    """Il ne compte a aucun denominateur : un score dont l'issue est inconnue ne
    dit rien de la lecture."""
    session_id, (event_id, _) = _lot(migrated)

    set_scores.save(session_id, event_id, "2-0", settings=migrated)

    report = set_scores.report(migrated)
    assert (report.settled, report.pending) == (0, 1)
    assert report.exact_rate is None
    assert not report.empty, "il y a bien quelque chose a afficher"


def test_la_matrice_croise_annonce_et_constate(migrated: Settings) -> None:
    """Quatre issues, donc seize cases : c'est elle qui montre *comment* la
    lecture se trompe — un favori sous-estime ne produit pas les memes erreurs
    qu'un match lu trop serre."""
    session_id, (premier, second) = _lot(migrated)
    set_scores.save(session_id, premier, "2-0", actual="2-1", settings=migrated)
    set_scores.save(session_id, second, "2-0", actual="2-1", settings=migrated)

    lignes = {
        annonce: comptes
        for annonce, comptes, _ in set_scores.matrix_rows(set_scores.report(migrated))
    }

    assert lignes["2-0"] == [0, 2, 0, 0], "deux fois « 2-0 annoncé, 2-1 constaté »"
    assert lignes["0-2"] == [0, 0, 0, 0]


def test_aucun_champ_financier_sur_le_releve() -> None:
    """Meme garde-fou que partout ailleurs (SPEC.md section 9). Ce marche n'a
    meme pas de cote chez le fournisseur : rien ne peut s'y rapprocher d'un prix."""
    from dataclasses import fields

    noms = {champ.name for champ in fields(set_scores.Report)} | {
        champ.name for champ in fields(set_scores.Row)
    }

    assert not noms & {"price", "cote", "stake", "mise", "roi", "profit", "odds"}


# -- Routes -----------------------------------------------------------------


def test_la_saisie_rend_le_fragment_et_non_la_page(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Une route ciblee par HTMX rend le fragment : rendre `picks.html` dans un
    `outerHTML` imbriquerait un `<html>` complet dans le `<div>` remplace."""
    session_id, (event_id, _) = _lot(isolated_settings)

    response = client.post(
        f"/history/{session_id}/set-scores/{event_id}", data={"predicted": "2-1"}
    )

    assert response.status_code == 200
    assert "<html" not in response.text
    assert 'id="set-scores"' in response.text


def test_la_page_de_statistiques_rend_le_releve(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, (event_id, _) = _lot(isolated_settings)
    set_scores.save(session_id, event_id, "2-0", actual="2-1", settings=isolated_settings)

    page = client.get("/stats").text

    assert "Le score en sets annoncé" in page
    assert "Matrice annoncé / constaté" in page


def test_le_releve_disparait_quand_rien_n_est_saisi(client: TestClient) -> None:
    """Une ligne sans donnee est omise, jamais rendue vide : meme regle que les
    blocs de match."""
    assert "Le score en sets annoncé" not in client.get("/stats").text


def test_une_erreur_de_saisie_est_affichee(client: TestClient, isolated_settings: Settings) -> None:
    """Une ligne illisible n'est jamais ignoree en silence."""
    session_id, (event_id, _) = _lot(isolated_settings)

    response = client.post(
        f"/history/{session_id}/set-scores/{event_id}",
        data={"predicted": "2-0", "alternate": "2-0"},
    )

    assert "différer" in response.text


def test_la_table_garde_sa_cle_naturelle(migrated: Settings) -> None:
    """Une session ne rend qu'un score par match : la contrainte vit en base et
    pas seulement dans le code, sinon un second chemin d'ecriture la perdrait."""
    index = db.query("PRAGMA index_list(set_scores)", settings=migrated)

    assert any(row["unique"] for row in index)
