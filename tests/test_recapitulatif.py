"""Le recapitulatif du jour : composer, jamais reanalyser.

Cinq risques propres a cette surface, chacun avec son banc : prononcer un
enchainement la ou aucune source ne donne la duree d'un match, faire fuiter un
taux de reussite, donner un palier a un combine, proposer une jambe sur un match
deja commence, et laisser croire qu'une proposition s'enregistre.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect, utcnow
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import changelog as changelog_service
from myassistantbet.services import combos as combos_service
from myassistantbet.services import recap as recap_service
from myassistantbet.services.history import RESULT_LABELS, add_pick, set_result
from myassistantbet.services.manual import build, save


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _today() -> str:
    return utcnow()[:10]


def _match(
    settings: Settings,
    nom: str,
    *,
    sport: str = "football",
    date: str = "2099-01-01",
    heure: str = "20:45",
) -> int:
    """Un match saisi a la main, donc a l'heure qu'on veut."""
    return save(
        build(
            sport,
            f"Coupe {sport}",
            nom,
            f"Adversaire {nom}",
            date,
            heure,
            f"{nom} 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _pick(
    settings: Settings,
    event_id: int,
    *,
    tier: str = "safe",
    price: str = "1.45",
    conf: str = "4",
    source: str = "betclic",
    exploratory: bool = False,
    note: str = "",
) -> int:
    session_id = board_service.toggle_selection(event_id, True, settings)
    return add_pick(
        session_id,
        tier=tier,
        market="1N2",
        selection="Domicile",
        event_id=str(event_id),
        price=price,
        confidence=conf,
        price_source=source,
        independence_note=note,
        exploratory=exploratory,
        settings=settings,
    )


def _etats(jour: recap_service.DayRecap) -> dict[int | None, str]:
    return {ligne.pick.event_id: ligne.overlap for ligne in jour.lines}


# --- Ce que le recapitulatif rassemble -------------------------------------


def test_le_recapitulatif_porte_la_journee_et_pas_une_session(migrated: Settings) -> None:
    """Une journee porte 3 a 19 prompts et parfois **deux sessions** — mesure du
    22/08/2026. Le recapitulatif se lit sur `picks.created_at`, jamais sur une
    session, sans quoi la moitie du jour manquerait.

    La seconde session s'ecrit directement : `board.current_session` en rend une
    par jour local, donc le parcours ordinaire ne peut pas la produire. C'est
    justement pourquoi le cas se mesure en base et pas au banc de l'interface.
    """
    settings = migrated
    premier = _match(settings, "Alpha")
    second = _match(settings, "Beta")
    _pick(settings, premier)

    with connect(settings) as conn:
        autre = int(
            conn.execute(
                "INSERT INTO sessions (label, created_at) VALUES ('seconde', ?)", (utcnow(),)
            ).lastrowid
        )
    add_pick(
        autre,
        tier="safe",
        market="1N2",
        selection="Domicile",
        event_id=str(second),
        price="1.45",
        confidence="4",
        settings=settings,
    )

    jour = recap_service.build(_today(), settings)

    assert {ligne.pick.event_id for ligne in jour.lines} == {premier, second}
    assert len({ligne.pick.session_id for ligne in jour.lines}) == 2
    assert jour.sessions == 2


def test_la_section_c_bis_est_tenue_a_part(migrated: Settings) -> None:
    """C-bis ne recoit aucune mise, par decision de principe : elle se lit, elle
    n'alimente aucune proposition."""
    settings = migrated
    principale = _match(settings, "Alpha")
    exploratoire = _match(settings, "Beta")
    _pick(settings, principale)
    _pick(settings, exploratoire, tier="giga_fun", price="4.20", conf="1", exploratory=True)

    jour = recap_service.build(_today(), settings)

    assert [ligne.pick.event_id for ligne in jour.lines] == [principale]
    assert [pick.event_id for pick in jour.exploratory] == [exploratoire]
    for proposition in jour.proposals:
        assert exploratoire not in {pick.event_id for pick in proposition.pool}


# --- L'enchainement : trois etats, jamais deux -----------------------------


def test_deux_matchs_de_football_qui_se_chevauchent_sont_dits_tels(
    migrated: Settings,
) -> None:
    settings = migrated
    tot = _match(settings, "Alpha", date="2099-01-01", heure="20:00")
    tard = _match(settings, "Beta", date="2099-01-01", heure="21:00")
    _pick(settings, tot)
    _pick(settings, tard)

    lignes = {
        ligne.pick.event_id: ligne.overlap
        for ligne in recap_service.build(_today(), settings).lines
    }

    assert lignes[tot] == recap_service.LIBRE
    assert lignes[tard] == recap_service.CHEVAUCHE


def test_un_football_assez_tard_est_libre(migrated: Settings) -> None:
    settings = migrated
    tot = _match(settings, "Alpha", date="2099-01-01", heure="18:00")
    tard = _match(settings, "Beta", date="2099-01-01", heure="20:30")
    _pick(settings, tot)
    _pick(settings, tard)

    lignes = {
        ligne.pick.event_id: ligne.overlap
        for ligne in recap_service.build(_today(), settings).lines
    }

    assert lignes[tard] == recap_service.LIBRE


def test_apres_un_tennis_l_enchainement_est_indetermine(migrated: Settings) -> None:
    """Aucune source ne publie la duree d'un match de tennis — mesure du
    07/08/2026, et les CSV qui la portaient ont disparu. Prononcer un
    enchainement derriere lui serait un booleen bati sur un nombre invente."""
    settings = migrated
    tennis = _match(settings, "Alpha", sport="tennis", date="2099-01-01", heure="20:00")
    apres = _match(settings, "Beta", date="2099-01-01", heure="23:59")
    _pick(settings, tennis)
    _pick(settings, apres)

    lignes = {
        ligne.pick.event_id: ligne.overlap
        for ligne in recap_service.build(_today(), settings).lines
    }

    assert lignes[tennis] == recap_service.LIBRE
    assert lignes[apres] == recap_service.INDETERMINE


def test_les_trois_etats_sont_definis_dans_le_rendu(migrated: Settings) -> None:
    """Un libelle sans definition est le defaut que ce projet evite partout."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(recap_service.build(_today(), settings), settings)

    for etat in recap_service.OVERLAP_LABELS.values():
        assert etat in corps


# --- Les propositions -------------------------------------------------------


def test_une_proposition_porte_sa_regle_comme_nom_et_jamais_un_palier(
    migrated: Settings,
) -> None:
    """Le mot « palier » n'a qu'un sens dans l'application : la bande de la cote
    d'une selection. `Combo` n'en porte aucun, et il faut qu'il en reste ainsi."""
    settings = migrated
    for nom in ("Alpha", "Beta", "Gamma", "Delta"):
        _pick(settings, _match(settings, nom))

    jour = recap_service.build(_today(), settings)

    assert [proposition.label for proposition in jour.proposals] == [
        "3 jambes, cran ≥ 4",
        "3 jambes, cran ≥ 3",
        "4 jambes, cran ≥ 3",
    ]
    assert not hasattr(recap_service.Proposal, "tier")
    assert not hasattr(recap_service.Proposal, "tier_label")


def test_le_vivier_se_compte_en_matchs_distincts(migrated: Settings) -> None:
    """Une seule selection par match dans un combine : deux lignes sur la meme
    rencontre n'en font qu'une, et le compte doit le dire avant le modele."""
    settings = migrated
    seul = _match(settings, "Alpha")
    _pick(settings, seul)
    _pick(settings, seul, note="Deux angles independants.")
    _pick(settings, _match(settings, "Beta"))
    _pick(settings, _match(settings, "Gamma"))

    jour = recap_service.build(_today(), settings)
    trois = next(p for p in jour.proposals if p.legs == 3 and p.min_confidence == 4)

    assert len(trois.pool) == 4
    assert trois.matches == 3
    assert trois.enough is True


def test_un_match_commence_sort_des_propositions(migrated: Settings) -> None:
    """`session.has_started` porte la regle du projet : un evenement dont
    l'heure est passee quitte le prompt. Une jambe qu'on ne peut plus poser
    n'est pas une proposition."""
    settings = migrated
    passe = _match(settings, "Alpha", date="2020-01-01", heure="20:00")
    venir = _match(settings, "Beta")
    _pick(settings, passe)
    _pick(settings, venir)

    jour = recap_service.build(_today(), settings)

    assert [pick.event_id for pick in jour.started] == [passe]
    for proposition in jour.proposals:
        assert passe not in {pick.event_id for pick in proposition.pool}


def test_le_produit_a_une_seule_ecriture(migrated: Settings) -> None:
    """Une jambe sans prix rend le produit incalculable plutot que faux. La
    regle est ecrite dans `combos`, et le recapitulatif l'appelle."""
    assert combos_service.product([1.5, 2.0]) == pytest.approx(3.0)
    assert combos_service.product([1.5, None]) is None
    assert combos_service.product([]) is None


# --- Les trois interdits du rendu ------------------------------------------


def test_le_rendu_dit_qu_il_ne_reanalyse_pas(migrated: Settings) -> None:
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(recap_service.build(_today(), settings), settings)

    assert "ne réanalyse" in corps
    assert "n'invente aucune cote" in corps


def test_le_rendu_dit_que_les_propositions_ne_s_enregistrent_pas(
    migrated: Settings,
) -> None:
    """`combos.prompt_id NOT NULL` refuse une jambe venue d'un autre prompt, et
    une journee en porte 3 a 19. Le taire ferait chercher un bouton absent."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = recap_service.render(recap_service.build(_today(), settings), settings)

    assert "ne s'enregistrent pas" in corps


def test_le_rendu_ne_transmet_aucun_taux(migrated: Settings) -> None:
    """Meme raison que `FEEDBACK_SUSPENDED` : transmettre la mesure ferme la
    boucle qu'elle mesure. Un resultat deja saisi ne doit pas remonter."""
    settings = migrated
    gagnant = _match(settings, "Alpha")
    perdant = _match(settings, "Beta")
    premier = _pick(settings, gagnant)
    second = _pick(settings, perdant)
    set_result(premier, "win", settings)
    set_result(second, "loss", settings)

    corps = recap_service.render(recap_service.build(_today(), settings), settings)

    # Le vocabulaire des resultats est lu la ou il vit, jamais recopie : une
    # seconde liste aurait diverge au premier libelle ajuste.
    for libelle in RESULT_LABELS.values():
        assert libelle not in corps.lower(), libelle
    for interdit in ("win", "loss", "%"):
        assert interdit not in corps.lower(), interdit


def test_une_cote_de_reference_est_marquee(migrated: Settings) -> None:
    """Un prix qu'on n'obtiendra pas tel quel ne peut pas se multiplier en
    silence : mediane 0 selection cotee chez le book principal parmi les
    candidates, 18 journees sur 23 sous deux."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"), source="reference")

    corps = recap_service.render(recap_service.build(_today(), settings), settings)

    assert "(ref.)" in corps


# --- La mise en service -----------------------------------------------------


def test_l_entree_de_journal_s_ecrit_au_premier_rendu_et_une_seule_fois(
    migrated: Settings,
) -> None:
    """`played` est a zero depuis toujours : un journal muet dirait de la meme
    facon « rien n'a bouge » et « la surface n'a jamais servi ». Idiome de
    `note_price_coverage` — au premier rendu, pas a la livraison."""
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))
    jour = recap_service.build(_today(), settings)

    assert recap_service.note_service(_today(), settings) is not None
    assert recap_service.note_service(_today(), settings) is None

    entrees = [
        entree
        for entree in changelog_service.journal(settings).entries
        if entree.label == recap_service.SERVICE_LABEL
    ]
    assert len(entrees) == 1
    assert jour.day == _today()


# --- Le service et sa surface se livrent ensemble ---------------------------


def test_la_route_rend_le_prompt(client: TestClient, migrated: Settings) -> None:
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    reponse = client.get(f"/recap/{_today()}")

    assert reponse.status_code == 200
    assert "Alpha" in reponse.text
    assert "3 jambes, cran ≥ 4" in reponse.text


def test_la_route_markdown_rend_le_meme_corps(client: TestClient, migrated: Settings) -> None:
    settings = migrated
    _pick(settings, _match(settings, "Alpha"))

    corps = client.get(f"/recap/{_today()}.md").text

    assert corps == recap_service.render(recap_service.build(_today(), settings), settings)


def test_une_journee_vide_le_dit(client: TestClient, migrated: Settings) -> None:
    """Un jour sans selection et un jour non analyse rendraient la meme page si
    rien ne le disait."""
    reponse = client.get("/recap/2020-01-01")

    assert reponse.status_code == 200
    assert "Aucune sélection" in reponse.text


def test_la_page_historique_porte_le_point_d_entree(client: TestClient, migrated: Settings) -> None:
    """Le service et sa surface se livrent ensemble : une route qu'aucun lien
    n'atteint est un service sans surface, et on l'a deja paye une fois."""
    reponse = client.get("/history")

    assert reponse.status_code == 200
    assert f'href="/recap/{_today()}"' in reponse.text
