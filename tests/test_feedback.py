"""Boucle de retour : ce que l'historique des picks renvoie dans le prompt.

Le risque propre a cette fonctionnalite n'est pas de manquer un chiffre, c'est
d'en publier un qui ne veut rien dire — un 2/3 lu « 67 % » — ou de laisser
entrer un indicateur financier par la porte de derriere. Les deux ont leur test.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import competitions as competitions_service
from myassistantbet.services.history import (
    FEEDBACK_MIN_ROWS,
    FEEDBACK_MIN_TOTAL,
    FEEDBACK_WINDOW,
    Feedback,
    FeedbackRow,
    add_pick,
    feedback,
    set_result,
)
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import (
    PREFERENCE_NOTES,
    CustomizationError,
    build_prompt,
    competition_notes,
    read_preference,
    save_preference,
)

from .helpers import NOW


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session_avec_match(
    settings: Settings,
    sport: str = "football",
    competition: str = "Amical",
) -> tuple[int, int]:
    """Cree un evenement manuel cote, et renvoie (session_id, event_id)."""
    event_id = save(
        build(
            sport,
            competition,
            "Lyon" if sport == "football" else "Moutet",
            "Nice" if sport == "football" else "Bergs",
            "2026-08-04",
            "20:45",
            "Lyon 2.10\nNice 3.40",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings), event_id


def _regle(
    settings: Settings,
    session_id: int,
    event_id: int,
    tier: str,
    result: str,
    market: str = "O/U 2.5",
    confidence: str = "",
) -> None:
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


# -- Le seuil de publication ------------------------------------------------


def test_aucun_pick_ne_produit_aucun_bloc(migrated: Settings) -> None:
    report = feedback(migrated)

    assert report.empty, "sans pari tranche, le bloc disparait entierement du prompt"
    assert not report.enough
    assert report.by_tier == []


def test_sous_le_seuil_aucun_detail_n_est_publie(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL - 1):
        _regle(migrated, session_id, event_id, "safe", "win")

    report = feedback(migrated)

    assert not report.empty
    assert not report.enough
    assert report.by_tier == [], "un taux sous le seuil mesure le hasard, pas une tendance"


def test_un_regroupement_trop_maigre_est_ecarte(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_MIN_TOTAL):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(FEEDBACK_MIN_ROWS - 1):
        _regle(migrated, session_id, event_id, "giga_fun", "loss")

    keys = {row.key for row in feedback(migrated).by_tier}

    assert "safe" in keys
    assert "giga_fun" not in keys, "trois paris ne font pas un taux"


# -- Ce que le taux compte --------------------------------------------------


def test_les_annules_et_les_attentes_sortent_du_denominateur(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(6):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(6):
        _regle(migrated, session_id, event_id, "safe", "loss")
    _regle(migrated, session_id, event_id, "safe", "void")
    _regle(migrated, session_id, event_id, "safe", "pending")

    row = next(row for row in feedback(migrated).by_tier if row.key == "safe")

    assert (row.won, row.lost) == (6, 6)
    assert row.settled == 12, "ni l'annule ni l'attente n'entrent au denominateur"
    assert row.rate == 0.5


def test_la_fenetre_ne_retient_que_les_derniers(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(FEEDBACK_WINDOW + 10):
        _regle(migrated, session_id, event_id, "safe", "loss")

    assert feedback(migrated).settled == FEEDBACK_WINDOW


def test_les_libelles_de_marche_sont_regroupes(migrated: Settings) -> None:
    """`Over 2.5 buts` et `over 2,5 Buts` sont le meme marche, ecrit deux fois."""
    session_id, event_id = _session_avec_match(migrated)
    for market in ("Over 2.5 buts", "over 2,5  Buts", "OVER 2.5 BUTS", "Over 2.5 buts"):
        _regle(migrated, session_id, event_id, "safe", "win", market=market)
    for _ in range(FEEDBACK_MIN_TOTAL - 4):
        _regle(migrated, session_id, event_id, "fun", "loss", market="Score exact")

    markets = {row.key: row for row in feedback(migrated).by_market}

    assert set(markets) == {"over 2 5 buts", "score exact"}
    assert markets["over 2 5 buts"].settled == 4, "quatre orthographes, un seul marche"


def test_le_taux_par_confiance_est_expose(migrated: Settings) -> None:
    """L'ecart entre confiance annoncee et taux constate est le signal utile."""
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(6):
        _regle(migrated, session_id, event_id, "safe", "loss", confidence="5")
    for _ in range(6):
        _regle(migrated, session_id, event_id, "fun", "win", confidence="3")

    by_confidence = {row.key: row for row in feedback(migrated).by_confidence}

    assert by_confidence["5"].rate == 0.0
    assert by_confidence["3"].rate == 1.0


# -- Section 9 : aucun indicateur financier ---------------------------------


def test_aucun_champ_financier_sur_le_retour() -> None:
    """Meme garde que sur les agregats de l'historique (SPEC.md section 9)."""
    interdits = {"roi", "profit", "stake", "mise", "gain", "esperance", "ev", "value", "edge"}

    for classe in (Feedback, FeedbackRow):
        noms = {item.name for item in fields(classe)}
        noms |= {name for name in dir(classe) if not name.startswith("_")}
        assert not (noms & interdits), f"{classe.__name__} expose un indicateur financier"


# -- Rendu dans le prompt ---------------------------------------------------


def test_le_prompt_omet_le_bloc_sans_historique(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CE QUE L'HISTORIQUE DIT" not in body, "aucune ligne vide sur une base neuve"


def test_le_prompt_annonce_le_manque_de_recul(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    _regle(migrated, session_id, event_id, "safe", "win")

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CE QUE L'HISTORIQUE DIT" in body
    assert "Trop peu de recul" in body
    assert "%" not in body.split("CE QUE L'HISTORIQUE DIT")[1].split("## SORTIE")[0]


def test_le_prompt_publie_les_taux_et_interdit_la_comparaison(migrated: Settings) -> None:
    session_id, event_id = _session_avec_match(migrated)
    for _ in range(4):
        _regle(migrated, session_id, event_id, "safe", "win")
    for _ in range(8):
        _regle(migrated, session_id, event_id, "giga_fun", "loss")

    body = build_prompt(session_id, settings=migrated, now=NOW).body
    bloc = body.split("CE QUE L'HISTORIQUE DIT")[1].split("## SORTIE")[0]

    assert "🟢 SAFE" in bloc
    assert "100 %" in bloc
    assert "🔴 GIGA FUN" in bloc and "0 %" in bloc
    # Le garde-fou compte autant que le chiffre : sans lui, on a fabrique un
    # detecteur de value a partir de son propre historique.
    assert "jamais" in bloc and "cote" in bloc
    assert "espérance" in bloc


# -- Fiches de competition --------------------------------------------------


def _competition_id(settings: Settings, label: str) -> int:
    return next(
        row["id"] for row in competitions_service.list_all(settings) if row["label"] == label
    )


def test_la_fiche_de_competition_entre_dans_le_prompt(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    competitions_service.set_notes(
        _competition_id(migrated, "Amical"), "Match de préparation, effectifs remaniés.", migrated
    )

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Amical : Match de préparation, effectifs remaniés." in body


def test_la_fiche_n_est_rendue_qu_une_fois(migrated: Settings) -> None:
    """Deux matchs d'une meme competition ne repetent pas sa fiche."""
    session_id, _ = _session_avec_match(migrated)
    second = save(
        build(
            "football",
            "Amical",
            "Reims",
            "Brest",
            "2026-08-04",
            "21:00",
            "Reims 2.00",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(second, True, migrated)
    competitions_service.set_notes(_competition_id(migrated, "Amical"), "Hors saison.", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert body.count("Amical : Hors saison.") == 1


def test_une_fiche_vide_ne_produit_aucune_ligne(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    competitions_service.set_notes(_competition_id(migrated, "Amical"), "   ", migrated)

    assert competition_notes(session_id, migrated, NOW) == []
    assert (
        "Fiches des compétitions" not in build_prompt(session_id, settings=migrated, now=NOW).body
    )


# -- Consignes permanentes --------------------------------------------------


def test_les_consignes_entrent_en_tete_du_prompt(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    save_preference(PREFERENCE_NOTES, "Je ne joue jamais les cartons.", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CONSIGNES PERMANENTES" in body
    assert "Je ne joue jamais les cartons." in body
    assert body.index("CONSIGNES PERMANENTES") < body.index("## MATCHS")


def test_des_consignes_vides_ne_produisent_aucune_section(migrated: Settings) -> None:
    session_id, _ = _session_avec_match(migrated)
    save_preference(PREFERENCE_NOTES, "   ", migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == ""
    assert "CONSIGNES PERMANENTES" not in build_prompt(session_id, settings=migrated, now=NOW).body


def test_des_consignes_trop_longues_sont_refusees(migrated: Settings) -> None:
    with pytest.raises(CustomizationError, match="trop longues"):
        save_preference(PREFERENCE_NOTES, "a" * 5000, migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == "", "rien n'a ete ecrit"


def test_les_consignes_se_remplacent(migrated: Settings) -> None:
    save_preference(PREFERENCE_NOTES, "Première version.", migrated)
    save_preference(PREFERENCE_NOTES, "Seconde version.", migrated)

    assert read_preference(PREFERENCE_NOTES, migrated) == "Seconde version."


# -- Ecrans -----------------------------------------------------------------


def test_enregistrement_des_consignes_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post("/settings/preferences", data={"preferences": "Pas de corners."})

    assert response.status_code == 200
    assert "Consignes enregistrées." in response.text
    assert read_preference(PREFERENCE_NOTES, isolated_settings) == "Pas de corners."


def test_consignes_trop_longues_refusees_par_l_ecran(
    client: TestClient, isolated_settings: Settings
) -> None:
    response = client.post("/settings/preferences", data={"preferences": "a" * 5000})

    assert response.status_code == 200
    assert "trop longues" in response.text
    assert read_preference(PREFERENCE_NOTES, isolated_settings) == ""


def test_enregistrement_d_une_fiche_via_htmx(
    client: TestClient, isolated_settings: Settings
) -> None:
    _session_avec_match(isolated_settings)
    competition_id = _competition_id(isolated_settings, "Amical")

    response = client.post(f"/competitions/{competition_id}/notes", data={"notes": "Aller-retour."})

    assert response.status_code == 200
    assert "Aller-retour." in response.text
