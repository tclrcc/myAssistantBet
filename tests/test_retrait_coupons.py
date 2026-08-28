"""Les coupons sont retires, les colonnes restent.

**Ce chantier ne supprime aucune donnee.** Le code et les surfaces partent ; les
tables et leurs migrations restent, parce qu'elles sont vides — donc elles ne
coutent rien — et parce qu'une migration de suppression est irreversible, quand
le depot n'a jamais rien supprime.

**Le precedent de `/players/squads` ne s'applique pas**, et la difference
compte : la migration 022 a supprime des **lignes** collectees des mois sans
lecteur. Ici il n'y a aucune ligne — `coupons` vide, `picks.coupon_id` nul sur
615, `picks.played` faux sur 615. Squads etait une collecte sans lecteur ; les
coupons sont une surface d'ecriture sans utilisateur, et il n'y a rien a nettoyer
derriere elle.

**Ce que le retrait rend visible.** `history.stats()` lisait `WHERE played = 1`,
donc zero ligne depuis toujours ; `coupons.rates()` rendait une liste vide ;
`Analysis.played` / `skipped` etaient gardes par `comparable`, qui exige
`played.settled > 0` — **ils n'ont jamais ete rendus une seule fois**. Ce n'est
pas une carte qui devient morte, c'est une carte qui n'a jamais vecu.

**Presque tous les bancs de ce fichier sont des assertions d'absence**, et c'est
le cas ou elles trompent le plus : un `assert x in texte` echoue tout seul quand
le montage rate, un `assert x not in texte` en profite. Chacun prouve donc
d'abord que la page est rendue.
"""

from __future__ import annotations

import importlib.util
from collections.abc import Iterator
from dataclasses import fields

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import history as history_service
from myassistantbet.services import thresholds as thresholds_service
from myassistantbet.services.history import add_pick, set_result
from myassistantbet.services.manual import build, save


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _selection(migrated: Settings, resultat: str = "win") -> tuple[int, int]:
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
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    pick_id = add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Alpha",
        event_id=str(event_id),
        price="1.45",
        confidence="4",
        settings=migrated,
    )
    set_result(pick_id, resultat, migrated)
    return session_id, pick_id


# --- Le module et ses routes ------------------------------------------------


def test_le_module_des_coupons_n_existe_plus() -> None:
    """La presence avant l'absence : si `find_spec` ne resolvait plus rien du
    tout, ce banc passerait pour la mauvaise raison."""
    assert importlib.util.find_spec("myassistantbet.services.history") is not None

    assert importlib.util.find_spec("myassistantbet.services.coupons") is None


def test_aucune_route_de_coupon(client: TestClient) -> None:
    chemins = {getattr(route, "path", "") for route in app.routes}

    assert "/history/{session_id}/picks" in chemins, "les routes de picks restent"
    assert not [chemin for chemin in chemins if "coupon" in chemin]
    assert "/picks/{pick_id}/play" not in chemins


def test_la_feuille_de_session_ne_porte_plus_de_coupon(
    client: TestClient, migrated: Settings
) -> None:
    session_id, _ = _selection(migrated)

    page = client.get(f"/history/{session_id}").text

    assert "Alpha" in page, "la feuille doit rester rendue"
    assert "coupon" not in page.lower()
    assert "Enregistrer le coupon" not in page
    assert ">jouer<" not in page


# --- Les surfaces de mesure -------------------------------------------------


def test_la_page_de_statistiques_ne_porte_plus_le_bloc_des_paris(
    client: TestClient, migrated: Settings
) -> None:
    _selection(migrated)

    page = client.get("/stats").text

    assert "Sélections tranchées" in page, "la page doit rester rendue"
    assert "Ce que valent tes paris" not in page
    assert "Aucun coupon saisi" not in page


def test_l_export_ne_porte_plus_le_bloc_des_paris(client: TestClient, migrated: Settings) -> None:
    _selection(migrated)

    corps = client.get("/api/stats/export?format=md").text

    assert "Sélections tranchées" in corps, "l'export doit rester rendu"
    assert "Ce que valent tes paris" not in corps
    assert "coupon" not in corps.lower()


def test_le_registre_des_sections_ne_declare_plus_les_paris() -> None:
    from myassistantbet.services import stats_export

    titres = {section.title for section in stats_export.SECTIONS} | {
        section.block for section in stats_export.SECTIONS
    }

    assert titres, "le registre doit rester peuple"
    assert "Ce que valent tes paris" not in titres


def test_l_analyse_ne_partage_plus_ses_selections_en_jouees_et_ecartees(
    migrated: Settings,
) -> None:
    """`comparable` exigeait `played.settled > 0` : les deux chiffres et leur
    phrase n'ont jamais ete rendus une seule fois."""
    _selection(migrated)
    rapport = history_service.analysis(migrated)

    assert rapport.overall.settled == 1, "le total doit rester compte"
    noms = {champ.name for champ in fields(rapport)}
    assert "played" not in noms
    assert "skipped" not in noms
    assert not hasattr(rapport, "comparable")


def test_le_taux_des_paris_poses_n_est_plus_calcule() -> None:
    assert hasattr(history_service, "analysis"), "les autres lectures restent"
    assert not hasattr(history_service, "stats")
    assert not hasattr(history_service, "Stats")


def test_l_interrupteur_des_coupons_a_disparu(migrated: Settings) -> None:
    assert thresholds_service.TOGGLES, "les interrupteurs restent"
    assert thresholds_service.REAL_PRICE_CAPTURE in thresholds_service.TOGGLES
    assert not hasattr(thresholds_service, "COUPON_TRACKING")
    assert "suivi_coupons" not in thresholds_service.TOGGLES


# --- Ce qui reste, et qui ne doit pas partir --------------------------------


def test_les_colonnes_restent_en_base(migrated: Settings) -> None:
    """On retire du code et des surfaces, jamais des donnees. Les colonnes sont
    vides, elles ne coutent rien, et une suppression ne se defait pas."""
    with connect(migrated) as conn:
        colonnes = {row["name"] for row in conn.execute("PRAGMA table_info(picks)")}
        tables = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }

    assert "coupon_id" in colonnes
    assert "played" in colonnes
    assert "stake" in colonnes
    assert "coupons" in tables


def test_les_migrations_des_coupons_restent_appliquees(migrated: Settings) -> None:
    with connect(migrated) as conn:
        rows = conn.execute("SELECT version FROM schema_migrations").fetchall()
    versions = {int(row["version"]) for row in rows}

    assert 10 in versions
    assert 11 in versions


def test_le_journal_des_mises_n_est_pas_touche(migrated: Settings) -> None:
    """`stakes` est en pause, pas abandonne : aucun croisement avec les coupons
    — `set_played` ecrit `mises.montant_joue` et jamais `picks.played`."""
    from myassistantbet.services import stakes

    assert stakes.UNITES_EXPLORATOIRE == 0.0
    with connect(migrated) as conn:
        tables = {
            row["name"] for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")
        }
    assert {"mises", "bankroll_journee"} <= tables


# --- `picks.stake`, orpheline independamment des coupons --------------------


def test_la_colonne_de_mise_des_picks_n_a_plus_ni_ecrivain_ni_lecteur() -> None:
    """Elle etait nourrie par un champ qui n'existe sur aucune page — exactement
    la forme que `stakes.py` a recue le 27/08. La colonne reste, annotee."""
    import inspect

    assert "tier" in inspect.signature(add_pick).parameters, "la signature reste peuplee"
    assert "stake" not in inspect.signature(add_pick).parameters
    assert "stake" not in {champ.name for champ in fields(history_service.Pick)}


def test_aucun_formulaire_ne_propose_de_mise(client: TestClient, migrated: Settings) -> None:
    session_id, _ = _selection(migrated)

    page = client.get(f"/history/{session_id}").text

    assert 'name="selection"' in page, "le formulaire de saisie reste rendu"
    assert 'name="stake"' not in page
