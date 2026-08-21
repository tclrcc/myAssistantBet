"""Les controles du cadre, comptes a l'import.

**Ils etaient calcules et opposes a rien.** L'application connait les evenements
rapproches, les crans, les niveaux de source, et depuis le lot A la condition
d'invalidation — donc elle sait repondre a quatre des dix controles que le cadre
dit « a passer systematiquement ». Elle ne l'a jamais dit.

Mesure du 21/08/2026 sur les 312 selections de section C, qui a leve la reserve
« ne pas opposer un controle avant d'en connaitre le taux de base » : controle 1
a 16 lignes, controle 8 a 36, controle 9 a 39.

**Compter, jamais bloquer.** La remediation des controles 8 et 9 est le renvoi
en C-bis, et il se decide dans le rendu : refuser la ligne la ferait disparaitre
du lot sans trace. Mais la mesure d'A2 interdit de s'en tenir a un
avertissement — celui de la section manquante a parle 20 fois sur 20 et les 20
imports ont ete valides quand meme. D'ou la confirmation explicite.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import controls, picks_import
from myassistantbet.services.manual import build, save
from tests.helpers import repost_import_form

LOIN = "2099-01-01"

SAIN = (Path(__file__).parent / "fixtures" / "tableau_section_c.md").read_text(encoding="utf-8")

#: Un tableau qui casse trois controles a la fois, et **deux sur la meme
#: ligne** : c'est le recouvrement qui doit se lire, pas la somme. En fixture
#: comme le collage complet — une ligne de tableau reelle depasse la largeur de
#: colonne du projet, et la couper testerait un tableau que personne ne colle.
EN_ECART = (Path(__file__).parent / "fixtures" / "tableau_section_c_en_ecart.md").read_text(
    encoding="utf-8"
)


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _lot(settings: Settings, noms: list[str]) -> int:
    session_id = 0
    for nom in noms:
        event_id = save(
            build(
                "football",
                "Match amical",
                nom,
                f"Adv {nom}",
                LOIN,
                "20:45",
                f"{nom} 1.45",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id


def _compte(settings: Settings) -> int:
    with connect(settings) as conn:
        return int(conn.execute("SELECT COUNT(*) n FROM picks").fetchone()["n"])


# -- Le compte lui-meme ------------------------------------------------------


def test_les_ecarts_se_comptent_par_controle(client: TestClient, migrated: Settings) -> None:
    """La première ligne casse les contrôles 8 et 9, la seconde le 7."""
    session_id = _lot(migrated, ["Lyon", "Nice"])

    apercu = picks_import.build_preview(session_id, EN_ECART, migrated)

    assert apercu.controls.violations == {"c7": 1, "c8": 1, "c9": 1}
    assert apercu.controls.lines == 2


def test_le_recouvrement_se_rend_et_l_addition_ferme(
    client: TestClient, migrated: Settings
) -> None:
    """**`1 + 1 + 1` ne fait pas 3 lignes.** Une ligne qui viole deux contrôles ne
    se répare pas comme deux lignes qui en violent un, et c'est le compte de
    lignes distinctes qui ferme l'addition."""
    session_id = _lot(migrated, ["Lyon", "Nice"])

    rapport = picks_import.build_preview(session_id, EN_ECART, migrated).controls

    assert sum(rapport.violations.values()) == 3
    assert rapport.flagged == 2, "deux lignes distinctes, pas trois"
    assert rapport.overlaps == [("c8", "c9", 1)]
    assert "contrôles 8 et 9" in rapport.note


def test_un_controle_sans_sa_colonne_est_muet_et_non_tenu(
    client: TestClient, migrated: Settings
) -> None:
    """**Trois états, jamais deux.** Une colonne absente et une cellule vide
    donnent la même sortie si l'on ne compte que les violations : le contrôle 7
    dirait « aucune condition d'invalidation » sur un collage à huit colonnes,
    c'est-à-dire une violation là où la question n'a pas été posée."""
    session_id = _lot(migrated, ["Lyon"])
    huit = (
        "### C. Tableau des sélections\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
    )

    rapport = picks_import.build_preview(session_id, huit, migrated).controls

    assert rapport.violations == {}, "aucune violation démontrée"
    assert rapport.silent == {"c7": 1, "c9": 1}, "deux questions qu'on n'a pas pu poser"
    assert not rapport.blocking, "un muet ne se confirme pas : la case deviendrait du décor"
    assert "non vérifiable" in rapport.note


def test_un_collage_conforme_ne_dit_rien(client: TestClient, migrated: Settings) -> None:
    """Un compte qui parle sur chaque collage cesse d'être un signal."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    conforme = SAIN.replace("| — |", "| Un forfait tardif |")

    rapport = picks_import.build_preview(session_id, conforme, migrated).controls

    assert rapport.violations == {}
    assert not rapport.blocking
    assert rapport.note == ""


# -- La garde a l'import -----------------------------------------------------


def test_l_import_est_retenu_tant_que_le_compte_n_est_pas_confirme(
    client: TestClient, migrated: Settings
) -> None:
    """**Le service et sa surface se livrent ensemble.** Un test qui appellerait
    le service verrait le compte et pas la garde ; c'est en postant le formulaire
    rendu et en relisant la base qu'on voit si l'import a été retenu."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": EN_ECART})

    reponse = client.post(
        f"/history/{session_id}/picks/import", data=repost_import_form(apercu.text)
    )

    assert reponse.status_code == 200
    assert _compte(migrated) == 0, "rien n'est écrit tant que le compte n'a pas été vu"
    assert "contrôle" in reponse.text


def test_la_confirmation_laisse_passer_sans_rien_corriger(
    client: TestClient, migrated: Settings
) -> None:
    """**Aucun blocage.** Le renvoi en C-bis se décide dans le rendu, pas ici :
    refuser la ligne la ferait disparaître du lot sans trace."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": EN_ECART})
    envoi = repost_import_form(apercu.text)
    envoi["confirm_controls"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=envoi)

    assert _compte(migrated) == 2, "les deux lignes entrent, écarts compris"


def test_la_case_des_sections_ne_vaut_pas_pour_les_controles(
    client: TestClient, migrated: Settings
) -> None:
    """**Deux confirmations et non une.** Cocher pour une section manquante ferait
    passer au même geste des écarts au cadre qu'on n'aurait pas lus, et le compte
    cesserait d'être « ce qu'on ne franchit pas sans le voir »."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": EN_ECART})
    envoi = repost_import_form(apercu.text)
    envoi["confirm_partial"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=envoi)

    assert _compte(migrated) == 0


def test_la_garde_relit_le_collage_et_jamais_le_formulaire(
    client: TestClient, migrated: Settings
) -> None:
    """**Ce qui garde l'import ne peut pas voyager par le formulaire qu'il
    garde.** La condition se recalcule depuis `imports_raw` : un champ caché qui
    la porterait serait fourni par la page qu'elle retient."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    client.post(f"/history/{session_id}/picks/preview", data={"table": EN_ECART})
    with connect(migrated) as conn:
        import_id = int(conn.execute("SELECT id FROM imports_raw").fetchone()["id"])

    rapport = controls.for_import(session_id, import_id, migrated)

    assert rapport is not None
    assert rapport.flagged == 2


def test_sans_collage_relisable_on_ne_retient_rien(migrated: Settings) -> None:
    """La saisie à la main et le rejeu n'ont pas d'identifiant d'import : les
    refuser serait fermer deux chemins pour garder le troisième."""
    session_id = _lot(migrated, ["Lyon"])

    assert controls.for_import(session_id, "", migrated) is None
    assert controls.for_import(session_id, "9999", migrated) is None
