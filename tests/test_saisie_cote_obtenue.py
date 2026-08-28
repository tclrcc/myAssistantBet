"""La saisie de la cote obtenue a son propre interrupteur.

**Un drapeau qui ouvre trois choses dont une seule part n'est plus un drapeau :
c'est trois decisions sous un nom.** `suivi_coupons` gardait le bloc des paris
poses, le journal des mises **et** la colonne « cote obtenue ». Les deux
premieres decrivent ce qu'on fait d'un prix ; la troisieme controle le prix
enregistre, et c'est le seul controle qui existe sur `picks.price` — le nombre
sur lequel repose tout le residu.

Mesure du 28/08/2026 : **184 cotes obtenues en base**, dont 35 sur 39 selections
le 27/08 et 17 sur 28 le 28/08, et **38 paliers revus** parce que le prix releve
ne tombait pas dans la meme bande. C'est la colonne la plus vivante du regime
courant.

**Les deux surfaces basculent ensemble, et c'est ce que ce banc garde.** La
saisie et le compteur de manque sont un seul fait : ferme d'un cote et ouvert de
l'autre, `coverage_line` dirait « aucun manque » au moment ou plus rien ne peut
etre saisi — un silence a deux causes, le defaut caracteristique du projet.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import thresholds as thresholds_service
from myassistantbet.services.history import add_pick, worksheet
from myassistantbet.services.manual import build, save


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _selection_de_reference(settings: Settings) -> int:
    """Une selection dont le prix vient d'un book de reference.

    C'est la seule forme qui reclame une cote obtenue : un prix du book
    principal est deja celui qu'on obtient.
    """
    event_id = save(
        build(
            "football",
            "Coupe",
            "Alpha",
            "Beta",
            "2099-01-01",
            "20:45",
            "Alpha 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    session_id = board_service.toggle_selection(event_id, True, settings)
    add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Alpha",
        event_id=str(event_id),
        price="1.45",
        confidence="4",
        price_source="reference",
        settings=settings,
    )
    return session_id


def test_l_interrupteur_existe_et_ouvre_par_defaut(migrated: Settings) -> None:
    """Il n'a aucune raison d'etre ferme : la saisie est quotidienne."""
    assert thresholds_service.REAL_PRICE_CAPTURE in thresholds_service.TOGGLES
    assert thresholds_service.TOGGLES[thresholds_service.REAL_PRICE_CAPTURE].default is True
    assert thresholds_service.toggle_of(thresholds_service.REAL_PRICE_CAPTURE, migrated) is True


def test_la_saisie_est_rendue_quand_l_interrupteur_est_ouvert(
    client: TestClient, migrated: Settings
) -> None:
    session_id = _selection_de_reference(migrated)

    page = client.get(f"/history/{session_id}").text

    # La presence avant l'absence : sans elle, le banc suivant passerait sur une
    # page vide ou en erreur.
    assert "Alpha" in page
    assert 'placeholder="obtenue"' in page


def test_ferme_la_saisie_et_le_compteur_disparaissent_ensemble(
    client: TestClient, migrated: Settings
) -> None:
    """Un silence a deux causes serait pire que les deux surfaces ouvertes."""
    session_id = _selection_de_reference(migrated)
    assert "Alpha" in client.get(f"/history/{session_id}").text

    thresholds_service.save_toggle(thresholds_service.REAL_PRICE_CAPTURE, "0", migrated)

    page = client.get(f"/history/{session_id}").text
    assert "Alpha" in page, "la page doit rester rendue"
    assert 'placeholder="obtenue"' not in page
    assert "sans cote obtenue" not in page
    assert "sans cote obtenue" not in worksheet(session_id, migrated).coverage_line


def test_ouvert_le_compteur_de_manque_se_dit(migrated: Settings) -> None:
    session_id = _selection_de_reference(migrated)

    ligne = worksheet(session_id, migrated).coverage_line

    assert "sans cote obtenue" in ligne


def test_la_note_de_l_interrupteur_ne_promet_que_ce_qu_il_ouvre(migrated: Settings) -> None:
    """Sixieme occurrence de la regle des copies : la note est rendue a l'ecran
    des reglages, donc une promesse perimee s'y lit comme un fait."""
    note = thresholds_service.TOGGLES[thresholds_service.REAL_PRICE_CAPTURE].note

    assert "cote obtenue" in note
    for absent in ("section G", "coupon", "pari"):
        assert absent not in note.lower(), absent
