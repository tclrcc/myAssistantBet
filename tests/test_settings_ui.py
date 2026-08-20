from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import thresholds
from myassistantbet.services.history import analysis, feedback, reach
from myassistantbet.services.history import load_bands as bandes_reglees
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import (
    DEFAULT_TEMPLATE,
    QUOTA_REFERENCE_LOT,
    CustomizationError,
    build_prompt,
    delete_template,
    list_templates,
    load_tiers,
    read_template,
    save_bands,
    save_template,
    save_tiers,
    template_path,
)

from .helpers import lot_avec_recul

#: Une date qu'aucune horloge ne rattrapera. Un match dont l'heure est passee
#: quitte le prompt, donc un lot date du jour rend un test vert le matin et
#: rouge le soir — sans qu'aucune regle ait bouge.
LOIN = "2099-01-01"


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
    """Le lot fait la taille de reference : la borne reglee y passe telle quelle.

    Depuis que les quotas se calculent a la generation, une session vide les
    rendrait tous a `0-0` — ce qui est juste, mais ne dirait rien de la saisie.

    **Et la cote du lot tombe dans la bande modifiee**, ce qui n'est pas un
    detail depuis que le prompt n'annonce que les paliers atteignables : a 2.10
    le lot ne portait que du FUN, et le palier renomme disparaissait du rendu
    pour une raison parfaitement juste — aucune de ses cotes ne pouvait y tomber.

    **La date des matchs est lointaine, et ce n'est pas une commodite.** Elle
    valait `2026-08-20` a 20:45 : le test etait vert le matin et rouge le soir,
    `session.has_started()` retirant du prompt tout evenement dont l'heure est
    passee — donc un lot vide, donc des quotas a `0-0`, ce qui est **juste**.
    L'assertion, elle, decrivait la sortie d'une journee et non une propriete.
    """
    session_id = 0
    for index in range(QUOTA_REFERENCE_LOT):
        event_id = save(
            build(
                "football",
                "Amical",
                f"Lyon {index}",
                f"Nice {index}",
                LOIN,
                "20:45",
                f"Lyon {index} 1.50",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)
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


def test_l_ecran_des_bandes_dit_quand_elles_atteignent_le_prompt(client: TestClient) -> None:
    """L'ecran annoncait qu'elles servent « ici et au prompt », et le bloc
    n'apparaissait nulle part dans le prompt rendu. Verification faite, le
    conditionnement etait juste : les bandes voyagent avec les taux, et les taux
    attendent assez de selections tranchees **et** assez de journees d'analyse.

    C'etait donc le libelle qui mentait par omission, pas le code.

    **Il ne cite plus les deux nombres en dur** : ils se reglent, et l'ecran
    renvoie au seuil plutot que d'en recopier une valeur qui divergerait au
    premier reglage."""
    page = client.get("/settings").text

    assert "ici et dans le prompt" in page
    assert "les taux attendent le recul réglé plus bas" in page
    assert "manque du recul" in page
    assert "sous 40 sélections tranchées" not in page, "plus aucun nombre en dur"


# -- L'etat « pas de cible » -------------------------------------------------


def test_une_bande_entierement_vide_est_acceptee(migrated: Settings) -> None:
    """Il n'existait **aucune** facon d'exprimer « ce cran n'a pas de cible » :
    vider les deux bornes rendait « borne basse manquante ». Or c'est le reglage
    juste sur les crans 1 et 2, pines par la source — `lecture` impose 1, une
    source de niveau 3-4 plafonne a 2. Ni les resserrer ni les relacher n'est un
    choix, donc une bande n'y declenche rien."""
    save_bands([{"level": 1, "low": None, "high": None}], migrated)

    bande = bandes_reglees(migrated)[1]
    assert not bande.targeted
    assert bande.label == "", "aucune cible a afficher, et surtout pas un zero"
    assert not bande.excludes((0.0, 0.1)), "rien a sortir d'une bande absente"


def test_une_borne_haute_seule_reste_refusee(migrated: Settings) -> None:
    """Saisie incomplete, et le rejet est garde exprès : c'est le dernier cas
    d'erreur que ce validateur sache attraper. Une borne effacee par megarde
    doit se voir."""
    with pytest.raises(CustomizationError, match="borne basse manquante"):
        save_bands([{"level": 3, "low": None, "high": 60.0}], migrated)


def test_le_cran_5_garde_sa_borne_basse(migrated: Settings) -> None:
    """Ce qui n'allait pas chez lui n'a jamais ete d'avoir une cible : la
    frontiere entre « un facteur dominant » et « deux facteurs independants »
    est discretionnaire, et descendre ses marginales en confiance 4 est une
    action reelle. Une borne basse sans borne haute reste sa forme."""
    save_bands([{"level": 5, "low": 12.0, "high": None}], migrated)

    bande = bandes_reglees(migrated, reference=50.0)[5]
    assert bande.targeted
    assert bande.offset_label == "global +12 et au-dessus"
    assert bande.label == "62 % et plus", "resolue contre le taux global"


def test_l_ecran_explique_l_etat_sans_cible(client: TestClient) -> None:
    page = client.get("/settings").text

    assert "Laisser les deux bornes vides veut dire" in page
    assert "C&#39;est le réglage attendu sur les crans" in page or (
        "C'est le réglage attendu sur les crans" in page
    )
    assert "borne haute seule</strong> reste refusée" in page


# -- Le gate de recul, regle et mesure ---------------------------------------


def test_les_deux_seuils_du_gate_sont_reglables(migrated: Settings) -> None:
    """Ils vivaient dans le code et l'ecran les citait en dur, alors qu'ils sont
    exactement ce que la table des seuils heberge : des nombres qui decident
    d'une regle sans etre une donnee."""
    assert reach(migrated) == (40, 10), "les defauts, tant que rien n'est saisi"

    thresholds.save("feedback_min_total", "25", migrated)
    thresholds.save("feedback_min_days", "4", migrated)

    assert reach(migrated) == (25, 4)


def test_le_seuil_regle_ouvre_reellement_le_gate(migrated: Settings) -> None:
    """Un seuil qui se regle sans rien changer au comportement ne serait qu'un
    champ de plus. Le meme lot, ferme puis ouvert par la seule saisie."""
    lot_avec_recul(migrated, confiances={3: (6, 6)})

    assert not feedback(migrated).enough, "12 tranchees sur 40"

    thresholds.save("feedback_min_total", "10", migrated)

    assert feedback(migrated).enough


def test_les_deux_surfaces_lisent_le_meme_seuil(migrated: Settings) -> None:
    """Sous quel compte un taux ne veut plus rien dire est une propriete des
    donnees, pas de la surface qui les affiche. Les copier des deux cotes les
    aurait fait diverger, et la page aurait fini par publier ce que le prompt
    refuse."""
    thresholds.save("feedback_min_total", "33", migrated)
    thresholds.save("feedback_min_days", "7", migrated)

    report, page = feedback(migrated), analysis(migrated)

    assert (report.minimum, report.minimum_days) == (33, 7)
    assert (page.minimum, page.minimum_days) == (33, 7)


def test_l_ecran_affiche_l_avancement_du_recul(client: TestClient, migrated: Settings) -> None:
    """C'est le **seul reglage dont l'effet est differe**, et le seul dont on ne
    pouvait pas mesurer la distance a l'activation."""
    lot_avec_recul(migrated, confiances={3: (10, 10)})
    db.execute("UPDATE picks SET created_at = '2026-07-01T12:00:00Z'", settings=migrated)

    page = client.get("/settings").text

    assert (
        "Recul actuel : 20 / 40 sélection(s) tranchée(s) · 1 / 10 journée(s) distincte(s)" in page
    )
    assert "Il manque 20 sélection(s) tranchée(s) et 9 journée(s) d" in page
    assert "ne sont pas transmis au prompt" in page


def test_l_ecran_dit_quand_les_taux_passent(client: TestClient, migrated: Settings) -> None:
    """Le pendant du precedent : franchi, l'avancement doit cesser d'alarmer."""
    lot_avec_recul(migrated)

    page = client.get("/settings").text

    assert "Les taux sont transmis au prompt." in page
    assert "Recul actuel : 60 / 40" in page
