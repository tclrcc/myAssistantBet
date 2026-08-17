"""Le second circuit : les selections produites sans fait date.

**Mesure du 17/08/2026, sur douze sessions** : 🔴 GIGA FUN et 💥 GIGA+ a zero
selection sur 12 sessions sur 12, 🟠 ULTRA FUN a 6 % du volume. Trois niveaux sur
cinq portent tout. Une echelle dont deux niveaux ne se declenchent jamais ne note
plus rien : ces bandes ne sont pas seulement inexploitees, elles sont **non
mesurables**.

L'exigence d'un fait date **n'est pas supprimee** — la retirer perdrait la
comparaison qui donne son sens a la page. Un second circuit s'ajoute a cote,
etiquete et compte a part, et ces tests verifient les deux moities : la
separation stricte des populations, et les deux refus propres a la section.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import ingestion, picks_import
from myassistantbet.services.history import (
    add_pick,
    analysis,
    compare_populations,
    exploratory,
    labelling,
    set_result,
)
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"

#: Un rendu complet : la section C, puis la section C-bis. Les deux tableaux
#: portent le meme en-tete — c'est le titre de section qui les separe, et c'est
#: lui qui remet la lecture de l'en-tete a zero.
RENDU = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |

### C-bis. Sélections exploratoires

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Nice – Adv Nice | 1N2 | Nice | 7.50 | 🔴 GIGA FUN | 1 |
"""


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str, cote: str = "1.45") -> int:
    return save(
        build(
            "football",
            "Match amical",
            nom,
            f"Adv {nom}",
            LOIN,
            "20:45",
            f"{nom} {cote}",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _lot(settings: Settings, noms: list[str]) -> tuple[int, list[int]]:
    session_id, events = 0, []
    for nom in noms:
        event_id = _match(settings, nom)
        events.append(event_id)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, events


# -- La lecture de la section ------------------------------------------------


def test_la_section_c_bis_pose_le_drapeau_et_pas_la_section_c(migrated: Settings) -> None:
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])

    preview = picks_import.build_preview(session_id, RENDU, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]


def test_une_ligne_c_bis_en_palier_sur_est_refusee_et_journalisee(
    migrated: Settings,
) -> None:
    """« Ce tableau est réservé aux paliers hauts. » Elle n'est pas proposee du
    tout — la corriger sur place reviendrait a inventer une decision que le rendu
    n'a pas prise — mais elle laisse sa trace."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    rendu = RENDU.replace("| 7.50 | 🔴 GIGA FUN | 1 |", "| 1.60 | 🟢 SAFE | 1 |")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False]
    motifs = {(reject.block_type, reject.reason) for reject in preview.rejects}
    assert (ingestion.EXPLORATOIRE, ingestion.SCHEMA_INVALID) in motifs


def test_une_ligne_c_bis_sur_un_match_deja_pris_est_refusee(migrated: Settings) -> None:
    """« Une seule sélection par match, tous tableaux confondus » est une
    contrainte qui ne tombe pas."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    rendu = RENDU.replace("| 1 | Nice – Adv Nice", "| 1 | Lyon – Adv Lyon")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False]
    motifs = {(reject.block_type, reject.reason) for reject in preview.rejects}
    assert (ingestion.EXPLORATOIRE, ingestion.DUPLICATE) in motifs


def test_trois_lignes_c_bis_produisent_trois_selections_exploratoires(
    client: TestClient, migrated: Settings
) -> None:
    """**Le critere d'acceptation du chantier.**"""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims", "Brest"])
    rendu = (
        "### C. Tableau des sélections\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
        "\n### C-bis. Sélections exploratoires\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Nice – Adv Nice | 1N2 | Nice | 3.20 | 🟠 ULTRA FUN | 1 |\n"
        "| 2 | Reims – Adv Reims | 1N2 | Reims | 7.50 | 🔴 GIGA FUN | 1 |\n"
        "| 3 | Brest – Adv Brest | 1N2 | Brest | 22.00 | 💥 GIGA+ | 1 |\n"
    )

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": rendu})
    assert apercu.status_code == 200
    donnees: dict[str, str] = {"rejects": "[]"}
    for index, (event_id, tier, price) in enumerate(
        zip(
            events,
            ("safe", "ultra_fun", "giga_fun", "giga_plus"),
            ("1.45", "3.20", "7.50", "22.00"),
            strict=True,
        ),
        start=1,
    ):
        donnees |= {
            f"keep_{index}": "1",
            f"event_{index}": str(event_id),
            f"tier_{index}": tier,
            f"market_{index}": "1N2",
            f"selection_{index}": f"choix {index}",
            f"price_{index}": price,
        }
        if index > 1:
            donnees[f"exploratory_{index}"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=donnees)

    lignes = db.query("SELECT tier, exploratoire FROM picks ORDER BY id", settings=migrated)
    assert [int(row["exploratoire"]) for row in lignes] == [0, 1, 1, 1]


# -- La separation des populations -------------------------------------------


def test_une_selection_exploratoire_n_entre_dans_aucun_regroupement_existant(
    migrated: Settings,
) -> None:
    """**Melanger les deux detruirait la comparaison que cette section existe
    pour rendre possible.** C'est la propriete centrale du chantier."""
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    principal = add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="1.45",
        confidence="4",
        settings=migrated,
    )
    second = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        event_id=str(events[1]),
        price="7.50",
        confidence="1",
        exploratory=True,
        settings=migrated,
    )
    set_result(principal, "win", migrated)
    set_result(second, "loss", migrated)

    principale = analysis(migrated)

    assert principale.settled == 1, "la population principale ignore l'exploratoire"
    assert principale.recorded == 1, "y compris le témoin, sinon l'addition ne ferme plus"
    assert principale.consistent
    par_axe = {block.key: sum(row.count for row in block.rows) for block in labelling(migrated)}
    assert par_axe["tier"] == 1, "l'étiquetage décrit la même population"


def test_le_bloc_exploratoire_ne_mesure_que_sa_propre_population(
    migrated: Settings,
) -> None:
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="1.45",
        settings=migrated,
    )
    second = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        event_id=str(events[1]),
        price="7.50",
        exploratory=True,
        settings=migrated,
    )
    set_result(second, "loss", migrated)

    lot = exploratory(migrated)

    assert (lot.settled, lot.won) == (1, 0)
    assert [row.key for row in lot.by_tier] == ["giga_fun"]


def test_la_comparaison_ne_se_rend_pas_sous_vingt_selections_de_chaque_cote() -> None:
    """**Elle opposerait deux nombres dont aucun ne veut rien dire.** C'est la
    faute exacte que la page a mis huit lots a cesser de commettre."""
    from myassistantbet.services.history import RateRow

    def _row(key: str, won: int, lost: int) -> RateRow:
        row = RateRow(key=key, label=key)
        row.won, row.lost = won, lost
        return row

    courte = compare_populations([_row("giga_fun", 5, 5)], [_row("giga_fun", 5, 5)])
    longue = compare_populations([_row("giga_fun", 15, 10)], [_row("giga_fun", 5, 20)])

    assert courte == []
    assert [cle for cle, _, _, _ in longue] == ["giga_fun"]


def test_la_page_avertit_que_le_taux_faible_est_attendu(
    client: TestClient, migrated: Settings
) -> None:
    """Sans cette phrase, le bloc se lirait comme un constat d'echec de la
    methode, alors qu'il mesure exactement ce qu'il annonce."""
    session_id, events = _lot(migrated, ["Lyon"])
    pick_id = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="7.50",
        exploratory=True,
        settings=migrated,
    )
    set_result(pick_id, "loss", migrated)

    page = client.get("/stats")

    assert "produites sans fait daté, par construction" in page.text
    assert "pas pour être bonne" in page.text
