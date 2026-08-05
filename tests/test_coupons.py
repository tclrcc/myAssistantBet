"""Coupons joues : regroupement des jambes, resultat deduit, captures.

Trois risques propres a cette fonctionnalite, chacun avec son test : deduire
un mauvais resultat de coupon, laisser entrer un indicateur financier, et
accepter un fichier qui n'est pas l'image qu'il pretend etre.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coupons as coupons_service
from myassistantbet.services.history import (
    FEEDBACK_MIN_TOTAL,
    HistoryError,
    add_pick,
    feedback,
    list_picks,
    set_result,
    stats,
)
from myassistantbet.services.manual import build, save

#: Un PNG de 1x1 pixel, valide de bout en bout (en-tete, IHDR, IDAT, IEND).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000b49444154789c6360000200000500017a5eab3f0000000049454e44ae426082"
)
JPEG = b"\xff\xd8\xff\xe0" + b"\x00" * 64


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session(settings: Settings, sport: str = "football") -> tuple[int, int]:
    event_id = save(
        build(
            sport,
            "Amical" if sport == "football" else "ATP",
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


def _pick(settings: Settings, session_id: int, event_id: int, tier: str = "safe") -> int:
    return add_pick(
        session_id,
        tier,
        "O/U 2.5",
        "Over 2.5",
        event_id=str(event_id),
        price="1.72",
        settings=settings,
    )


# -- Regroupement -----------------------------------------------------------


def test_creation_d_un_coupon_simple(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)

    coupon_id = coupons_service.create(session_id, [pick_id], stake="5", settings=migrated)

    coupon = coupons_service.list_for_session(session_id, migrated)[0]
    assert coupon.coupon_id == coupon_id
    assert coupon.kind == "simple"
    assert not coupon.combined
    assert coupon.stake == 5.0
    assert coupon.bookmaker == "betclic"
    assert len(coupon.legs) == 1


def test_une_jambe_rattachee_passe_en_joue(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = add_pick(
        session_id, "safe", "O/U", "Over", event_id=str(event_id), played=False, settings=migrated
    )

    coupons_service.create(session_id, [pick_id], settings=migrated)

    assert list_picks(session_id, migrated)[0].played is True


def test_un_coupon_sans_jambe_est_refuse(migrated: Settings) -> None:
    session_id, _ = _session(migrated)

    with pytest.raises(HistoryError, match="au moins une jambe"):
        coupons_service.create(session_id, [], settings=migrated)


def test_une_jambe_deja_engagee_est_refusee(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)
    coupons_service.create(session_id, [pick_id], settings=migrated)

    with pytest.raises(HistoryError, match="déjà dans un coupon"):
        coupons_service.create(session_id, [pick_id], settings=migrated)


def test_une_jambe_d_une_autre_session_est_refusee(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)

    with pytest.raises(HistoryError, match="n'appartiennent pas"):
        coupons_service.create(session_id + 99, [pick_id], settings=migrated)


def test_les_jambes_engagees_sortent_des_disponibles(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    premier = _pick(migrated, session_id, event_id)
    second = _pick(migrated, session_id, event_id, "fun")

    coupons_service.create(session_id, [premier], settings=migrated)

    restants = [pick.pick_id for pick in coupons_service.available_picks(session_id, migrated)]
    assert restants == [second]


def test_une_mise_illisible_est_refusee(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)

    with pytest.raises(HistoryError, match="doit être un nombre"):
        coupons_service.create(session_id, [pick_id], stake="beaucoup", settings=migrated)


# -- Resultat deduit --------------------------------------------------------


def _combine(settings: Settings, resultats: list[str]) -> coupons_service.Coupon:
    session_id, event_id = _session(settings)
    pick_ids = []
    for result in resultats:
        pick_id = _pick(settings, session_id, event_id)
        set_result(pick_id, result, settings)
        pick_ids.append(pick_id)
    coupons_service.create(session_id, pick_ids, settings=settings)
    return coupons_service.list_for_session(session_id, settings)[0]


def test_un_combine_gagne_si_toutes_ses_jambes_passent(migrated: Settings) -> None:
    assert _combine(migrated, ["win", "win", "win"]).result == "win"


def test_une_seule_jambe_perdue_fait_tomber_le_combine(migrated: Settings) -> None:
    assert _combine(migrated, ["win", "loss", "win"]).result == "loss"


def test_une_jambe_perdue_prime_sur_une_jambe_en_attente(migrated: Settings) -> None:
    """Inutile d'attendre le reste : le coupon est deja tombe."""
    assert _combine(migrated, ["pending", "loss"]).result == "loss"


def test_une_jambe_en_attente_laisse_le_coupon_en_attente(migrated: Settings) -> None:
    assert _combine(migrated, ["win", "pending"]).result == "pending"


def test_une_jambe_annulee_est_neutre(migrated: Settings) -> None:
    """Le bookmaker recalcule la cote sans elle : le coupon reste gagnant."""
    assert _combine(migrated, ["win", "void"]).result == "win"


def test_un_coupon_entierement_annule_est_annule(migrated: Settings) -> None:
    assert _combine(migrated, ["void", "void"]).result == "void"


def test_le_type_se_deduit_du_nombre_de_jambes(migrated: Settings) -> None:
    assert _combine(migrated, ["win", "win", "win"]).kind == "combiné 3"


# -- L'angle mort repare ----------------------------------------------------


def test_un_combine_compte_desormais_dans_les_taux_par_sport(migrated: Settings) -> None:
    """Avant, un combine etait un pick sans evenement : sans sport, donc ignore."""
    foot_session, foot_event = _session(migrated, "football")
    tennis_event = save(
        build(
            "tennis",
            "ATP",
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

    jambes = []
    for event_id in (foot_event, tennis_event):
        pick_id = _pick(migrated, foot_session, event_id)
        set_result(pick_id, "win", migrated)
        jambes.append(pick_id)
    coupons_service.create(foot_session, jambes, settings=migrated)

    sports = {row.label: row for row in stats(migrated).by_sport}
    assert "Football" in sports and "Tennis" in sports
    assert "—" not in sports, "plus aucune jambe sans sport"


def test_taux_des_coupons_separes_par_type(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)

    simple = _pick(migrated, session_id, event_id)
    set_result(simple, "win", migrated)
    coupons_service.create(session_id, [simple], settings=migrated)

    perdants = []
    for _ in range(2):
        pick_id = _pick(migrated, session_id, event_id, "fun")
        set_result(pick_id, "loss", migrated)
        perdants.append(pick_id)
    coupons_service.create(session_id, perdants, settings=migrated)

    rates = {row.key: row for row in coupons_service.rates(migrated)}
    assert rates["simple"].rate == 1.0
    assert rates["combine"].rate == 0.0
    assert "combine" in rates and "simple" in rates, "les deux ne se melangent pas"


# -- « Joue » veut dire pose chez le bookmaker ------------------------------


def test_une_selection_non_jouee_ne_compte_pas(migrated: Settings) -> None:
    """Un pick propose par l'analyse puis ecarte n'a jamais ete confronte au terrain."""
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)
    set_result(pick_id, "win", migrated)

    assert list_picks(session_id, migrated)[0].played is False
    assert stats(migrated).empty, "aucun pari joue, donc aucun taux"


def test_le_rattachement_fait_entrer_le_pick_dans_les_taux(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)
    set_result(pick_id, "win", migrated)

    coupons_service.create(session_id, [pick_id], settings=migrated)

    assert stats(migrated).overall.won == 1


def test_supprimer_le_coupon_retire_le_pick_des_taux(migrated: Settings) -> None:
    """Un coupon saisi par erreur ne doit pas laisser un pick marque joue."""
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)
    set_result(pick_id, "win", migrated)
    coupon_id = coupons_service.create(session_id, [pick_id], settings=migrated)

    coupons_service.delete(coupon_id, migrated)

    assert list_picks(session_id, migrated)[0].played is False
    assert stats(migrated).empty


def test_le_retour_d_experience_ignore_les_selections_non_jouees(migrated: Settings) -> None:
    """Le prompt n'apprend que du terrain, pas des selections ecartees."""
    session_id, event_id = _session(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL + 2):
        pick_id = _pick(migrated, session_id, event_id)
        set_result(pick_id, "win", migrated)

    assert feedback(migrated).empty


# -- Section 9 : aucun indicateur financier ---------------------------------


def test_aucun_champ_financier_sur_le_coupon() -> None:
    interdits = {"roi", "profit", "gain", "solde", "esperance", "ev", "value", "edge", "payout"}

    noms = {item.name for item in fields(coupons_service.Coupon)}
    noms |= {name for name in dir(coupons_service.Coupon) if not name.startswith("_")}

    assert not (noms & interdits)
    assert "combined_odds" not in noms, "la cote totale n'est pas calculee, la capture la porte"


# -- Captures ---------------------------------------------------------------


def _coupon(settings: Settings) -> int:
    session_id, event_id = _session(settings)
    return coupons_service.create(
        session_id, [_pick(settings, session_id, event_id)], settings=settings
    )


def test_capture_enregistree(migrated: Settings) -> None:
    coupon_id = _coupon(migrated)

    name = coupons_service.save_screenshot(
        coupon_id, "n'importe quoi.png", PNG, "image/png", migrated
    )

    assert name == f"coupon-{coupon_id}-" + name.split("-")[-1]
    assert coupons_service.SCREENSHOT_NAME.match(name)
    assert coupons_service.screenshot_path(coupon_id, migrated).is_file()


def test_le_nom_du_navigateur_n_est_jamais_utilise(migrated: Settings) -> None:
    """La traversee de repertoire entre par le nom de fichier, jamais ailleurs."""
    coupon_id = _coupon(migrated)

    name = coupons_service.save_screenshot(coupon_id, "../../../.env", PNG, "image/png", migrated)

    assert ".." not in name and "/" not in name
    assert (
        coupons_service.screenshot_path(coupon_id, migrated).parent == migrated.upload_dir_absolute
    )


def test_un_type_non_image_est_refuse(migrated: Settings) -> None:
    coupon_id = _coupon(migrated)

    with pytest.raises(HistoryError, match="Format non accepté"):
        coupons_service.save_screenshot(
            coupon_id, "coupon.pdf", b"%PDF-1.4", "application/pdf", migrated
        )


def test_un_fichier_qui_ment_sur_son_type_est_refuse(migrated: Settings) -> None:
    """Le navigateur annonce ce qu'il veut ; l'octet de tete ne ment pas."""
    coupon_id = _coupon(migrated)

    with pytest.raises(HistoryError, match="pas l'image qu'il prétend"):
        coupons_service.save_screenshot(
            coupon_id, "coupon.png", b"#!/bin/sh\nrm -rf /", "image/png", migrated
        )


def test_une_capture_trop_lourde_est_refusee(migrated: Settings) -> None:
    coupon_id = _coupon(migrated)
    enorme = PNG + b"\x00" * migrated.upload_max_bytes

    with pytest.raises(HistoryError, match="trop lourde"):
        coupons_service.save_screenshot(coupon_id, "gros.png", enorme, "image/png", migrated)


def test_une_capture_remplace_la_precedente(migrated: Settings) -> None:
    coupon_id = _coupon(migrated)
    premier = coupons_service.save_screenshot(coupon_id, "a.png", PNG, "image/png", migrated)

    coupons_service.save_screenshot(coupon_id, "b.jpg", JPEG, "image/jpeg", migrated)

    assert not (migrated.upload_dir_absolute / premier).exists(), "l'ancienne est retiree"
    assert coupons_service.screenshot_path(coupon_id, migrated).suffix == ".jpg"


def test_un_nom_de_capture_falsifie_en_base_est_refuse(migrated: Settings) -> None:
    """Une base modifiee a la main ne doit pas faire servir un fichier arbitraire."""
    from myassistantbet.db import connect

    coupon_id = _coupon(migrated)
    with connect(migrated) as conn:
        conn.execute("UPDATE coupons SET screenshot = ? WHERE id = ?", ("../../.env", coupon_id))

    assert coupons_service.screenshot_path(coupon_id, migrated) is None


# -- Suppression ------------------------------------------------------------


def test_la_suppression_libere_les_jambes(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    pick_id = _pick(migrated, session_id, event_id)
    coupon_id = coupons_service.create(session_id, [pick_id], settings=migrated)
    coupons_service.save_screenshot(coupon_id, "a.png", PNG, "image/png", migrated)
    path = coupons_service.screenshot_path(coupon_id, migrated)

    coupons_service.delete(coupon_id, migrated)

    assert coupons_service.list_for_session(session_id, migrated) == []
    assert [pick.pick_id for pick in coupons_service.available_picks(session_id, migrated)] == [
        pick_id
    ], "le pick a bien ete analyse, il survit au coupon"
    assert not path.exists(), "la capture part avec le coupon"


# -- Ecrans -----------------------------------------------------------------


def test_enregistrement_via_le_formulaire(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session(isolated_settings)
    pick_id = _pick(isolated_settings, session_id, event_id)

    response = client.post(
        f"/history/{session_id}/coupons",
        data={"pick_id": str(pick_id), "stake": "10", "date": "2026-08-05", "time": "19:04"},
        files={"screenshot": ("coupon.png", PNG, "image/png")},
    )

    assert response.status_code == 200
    assert "Coupon enregistré." in response.text
    coupon = coupons_service.list_for_session(session_id, isolated_settings)[0]
    assert coupon.stake == 10.0
    assert coupon.placed_local.strftime("%d/%m %H:%M") == "05/08 19:04"
    assert coupon.screenshot


def test_une_capture_refusee_n_annule_pas_le_coupon(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le pari a bien ete pose : le perdre parce que l'image ne va pas serait absurde."""
    session_id, event_id = _session(isolated_settings)
    pick_id = _pick(isolated_settings, session_id, event_id)

    response = client.post(
        f"/history/{session_id}/coupons",
        data={"pick_id": str(pick_id)},
        files={"screenshot": ("coupon.pdf", b"%PDF-1.4", "application/pdf")},
    )

    assert "Format non accepté" in response.text
    assert len(coupons_service.list_for_session(session_id, isolated_settings)) == 1


def test_la_capture_est_servie(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session(isolated_settings)
    pick_id = _pick(isolated_settings, session_id, event_id)
    coupon_id = coupons_service.create(session_id, [pick_id], settings=isolated_settings)
    coupons_service.save_screenshot(coupon_id, "a.png", PNG, "image/png", isolated_settings)

    response = client.get(f"/coupons/{coupon_id}/screenshot")

    assert response.status_code == 200
    assert response.content == PNG


def test_capture_absente_renvoie_404(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session(isolated_settings)
    pick_id = _pick(isolated_settings, session_id, event_id)
    coupon_id = coupons_service.create(session_id, [pick_id], settings=isolated_settings)

    assert client.get(f"/coupons/{coupon_id}/screenshot").status_code == 404


def test_coupon_inconnu_renvoie_404(client: TestClient) -> None:
    assert client.post("/coupons/999/delete").status_code == 404


def test_les_taux_de_coupons_apparaissent_dans_l_historique(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session(isolated_settings)
    pick_id = _pick(isolated_settings, session_id, event_id)
    set_result(pick_id, "win", isolated_settings)
    coupons_service.create(session_id, [pick_id], settings=isolated_settings)

    response = client.get("/history")

    assert "Coupons joués" in response.text
    assert "Paris simples" in response.text
