from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import thresholds
from myassistantbet.services.prompt import (
    DEFAULT_TEMPLATE,
    CustomizationError,
    build_prompt,
    delete_template,
    list_templates,
    load_tiers,
    read_template,
    save_template,
    save_tiers,
    template_path,
)


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture(autouse=True)
def nettoie_les_templates() -> Iterator[None]:
    """Les templates vivent sur disque : on retire ceux crees par les tests."""
    avant = set(list_templates())
    try:
        yield
    finally:
        for name in set(list_templates()) - avant:
            template_path(name).unlink(missing_ok=True)


BODY = "# {{ date_fr }}\n{% for block in event_blocks %}{{ block }}{% endfor %}\n"


# -- Templates --------------------------------------------------------------


def test_lecture_du_template_par_defaut() -> None:
    assert "SESSION D'ANALYSE" in read_template(DEFAULT_TEMPLATE)


def test_lecture_d_un_template_inconnu() -> None:
    with pytest.raises(CustomizationError, match="Template inconnu"):
        read_template("inexistant.md.j2")


def test_creation_d_une_variante() -> None:
    save_template("session_court.md.j2", BODY)

    assert "session_court.md.j2" in list_templates()
    assert read_template("session_court.md.j2") == BODY


def test_le_defaut_reste_en_tete_de_liste() -> None:
    save_template("aaa_variante.md.j2", BODY)

    assert list_templates()[0] == DEFAULT_TEMPLATE


@pytest.mark.parametrize(
    "name",
    ["../evasion.md.j2", "/etc/passwd", "Majuscules.md.j2", "sans_extension", "x.j2", ""],
)
def test_nom_de_template_invalide(name: str) -> None:
    with pytest.raises(CustomizationError, match="Nom invalide"):
        save_template(name, BODY)


def test_template_vide_refuse() -> None:
    with pytest.raises(CustomizationError, match="vide"):
        save_template("vide.md.j2", "   ")


def test_template_qui_ne_compile_pas_est_refuse() -> None:
    with pytest.raises(CustomizationError, match="syntaxe Jinja"):
        save_template("casse.md.j2", "{% for block in event_blocks %}{{ block }}")

    assert "casse.md.j2" not in list_templates(), "rien n'est ecrit sur disque"


def test_le_template_par_defaut_n_est_pas_supprimable() -> None:
    with pytest.raises(CustomizationError, match="par défaut"):
        delete_template(DEFAULT_TEMPLATE)

    assert DEFAULT_TEMPLATE in list_templates()


def test_suppression_d_une_variante() -> None:
    save_template("jetable.md.j2", BODY)

    delete_template("jetable.md.j2")

    assert "jetable.md.j2" not in list_templates()


def test_template_modifie_pris_en_compte_sans_redemarrage(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)
    save_template("session_court.md.j2", "PROMPT COURT — {{ date_fr }}\n")

    body = build_prompt(session_id, "session_court.md.j2", migrated).body

    assert body.startswith("PROMPT COURT — ")


# -- Bandes de cotes --------------------------------------------------------


def _rows(migrated: Settings) -> list[dict[str, object]]:
    return [
        {
            "key": tier.key,
            "label": tier.label,
            "emoji": tier.emoji,
            "min_price": tier.min_price,
            "max_price": tier.max_price,
            "quota_min": tier.quota_min,
            "quota_max": tier.quota_max,
        }
        for tier in load_tiers(migrated)
    ]


def test_modification_d_une_bande(migrated: Settings) -> None:
    rows = _rows(migrated)
    rows[0]["min_price"] = 1.30
    rows[0]["max_price"] = 1.65
    rows[0]["label"] = "PRUDENT"

    save_tiers(rows, migrated)

    tier = load_tiers(migrated)[0]
    assert (tier.min_price, tier.max_price, tier.label) == (1.30, 1.65, "PRUDENT")
    assert tier.range_label == "1.30 – 1.65"


def test_borne_haute_vide_signifie_sans_limite(migrated: Settings) -> None:
    rows = _rows(migrated)
    rows[0]["max_price"] = None

    save_tiers(rows, migrated)

    assert load_tiers(migrated)[0].range_label.startswith("> 1.25")


def test_borne_haute_inferieure_refusee(migrated: Settings) -> None:
    rows = _rows(migrated)
    rows[0]["max_price"] = 1.10

    with pytest.raises(CustomizationError, match="borne haute"):
        save_tiers(rows, migrated)

    assert load_tiers(migrated)[0].max_price == 1.70, "rien n'est ecrit"


def test_quota_incoherent_refuse(migrated: Settings) -> None:
    rows = _rows(migrated)
    rows[0]["quota_min"] = 4
    rows[0]["quota_max"] = 2

    with pytest.raises(CustomizationError, match="quota maximum"):
        save_tiers(rows, migrated)


def test_borne_basse_manquante_refusee(migrated: Settings) -> None:
    rows = _rows(migrated)
    rows[0]["min_price"] = None

    with pytest.raises(CustomizationError, match="borne basse"):
        save_tiers(rows, migrated)


def test_les_bandes_modifiees_apparaissent_dans_le_prompt(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)
    rows = _rows(migrated)
    rows[0]["label"] = "PRUDENT"
    rows[0]["quota_min"] = 1
    rows[0]["quota_max"] = 2
    save_tiers(rows, migrated)

    body = build_prompt(session_id, settings=migrated).body

    assert "🟢 PRUDENT" in body
    assert "1-2 🟢" in body


# -- Routes -----------------------------------------------------------------


def test_page_reglages(client: TestClient) -> None:
    response = client.get("/settings")

    assert response.status_code == 200
    assert "Templates de prompt" in response.text
    assert "Bandes de cotes" in response.text
    assert "SESSION D&#39;ANALYSE" in response.text or "SESSION D'ANALYSE" in response.text


def test_selection_d_un_autre_template(client: TestClient) -> None:
    save_template("session_court.md.j2", "PROMPT COURT")

    response = client.get("/settings", params={"template": "session_court.md.j2"})

    assert "PROMPT COURT" in response.text


def test_template_inconnu_retombe_sur_le_defaut(client: TestClient) -> None:
    response = client.get("/settings", params={"template": "inexistant.md.j2"})

    assert response.status_code == 200
    assert "SESSION D&#39;ANALYSE" in response.text or "SESSION D'ANALYSE" in response.text


def test_enregistrement_via_htmx(client: TestClient) -> None:
    response = client.post(
        "/settings/templates", data={"name": "session_court.md.j2", "body": BODY}
    )

    assert response.status_code == 200
    assert "Template enregistré" in response.text
    assert "session_court.md.j2" in list_templates()


def test_enregistrement_refuse_conserve_la_saisie(client: TestClient) -> None:
    casse = "{% for block in event_blocks %}{{ block }}"

    response = client.post("/settings/templates", data={"name": "casse.md.j2", "body": casse})

    assert response.status_code == 200
    assert "syntaxe Jinja" in response.text
    assert "casse.md.j2" not in list_templates()


def test_suppression_via_htmx(client: TestClient) -> None:
    save_template("jetable.md.j2", BODY)

    response = client.post("/settings/templates/delete", data={"name": "jetable.md.j2"})

    assert response.status_code == 200
    assert "jetable.md.j2" not in list_templates()


def test_suppression_du_defaut_refusee(client: TestClient) -> None:
    response = client.post("/settings/templates/delete", data={"name": DEFAULT_TEMPLATE})

    assert response.status_code == 200
    assert "par défaut" in response.text
    assert DEFAULT_TEMPLATE in list_templates()


def test_enregistrement_des_paliers_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    tiers = load_tiers(isolated_settings)
    # httpx serialise un champ repete quand la valeur est une liste.
    data = {
        "key": [tier.key for tier in tiers],
        "emoji": [tier.emoji for tier in tiers],
        "label": ["PRUDENT" if tier.key == "safe" else tier.label for tier in tiers],
        "min_price": [str(tier.min_price) for tier in tiers],
        "max_price": ["" if tier.max_price is None else str(tier.max_price) for tier in tiers],
        "quota_min": [str(tier.quota_min) for tier in tiers],
        "quota_max": [str(tier.quota_max) for tier in tiers],
    }

    response = client.post("/settings/tiers", data=data)

    assert response.status_code == 200
    assert "Bandes de cotes enregistrées" in response.text
    assert load_tiers(isolated_settings)[0].label == "PRUDENT"


def test_paliers_incoherents_refuses_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    tiers = load_tiers(isolated_settings)
    data = {
        "key": [tier.key for tier in tiers],
        "emoji": [tier.emoji for tier in tiers],
        "label": [tier.label for tier in tiers],
        "min_price": [str(tier.min_price) for tier in tiers],
        "max_price": ["1.10" if tier.key == "safe" else "" for tier in tiers],
        "quota_min": [str(tier.quota_min) for tier in tiers],
        "quota_max": [str(tier.quota_max) for tier in tiers],
    }

    response = client.post("/settings/tiers", data=data)

    assert "borne haute" in response.text
    assert load_tiers(isolated_settings)[0].max_price == 1.70


def test_un_seuil_se_regle_depuis_l_ecran(client: TestClient, isolated_settings: Settings) -> None:
    """« A partir de combien de matchs un lot porte-t-il deux combines » est une
    decision de l'utilisateur, pas une constante du projet : la coder en dur
    obligerait a redeployer pour changer d'avis."""
    page = " ".join(client.get("/settings").text.split())
    assert "Lot minimum pour deux combinés" in page

    response = client.post("/settings/thresholds", data={"key": "combo_min_lot", "value": "8"})

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="thresholds">')
    assert thresholds.value_of("combo_min_lot", isolated_settings) == 8


def test_un_seuil_illisible_revient_au_defaut(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le retour au defaut est **ecrit en base** et pas seulement a la lecture :
    sinon le champ afficherait le defaut quand la table porte la saisie refusee.
    """
    client.post("/settings/thresholds", data={"key": "combo_min_lot", "value": "beaucoup"})

    defaut = thresholds.THRESHOLDS["combo_min_lot"].default
    assert thresholds.value_of("combo_min_lot", isolated_settings) == defaut
    assert db.query_one(
        "SELECT value FROM preferences WHERE key = ?",
        (thresholds.PREFIX + "combo_min_lot",),
        settings=isolated_settings,
    )["value"] == str(defaut)
