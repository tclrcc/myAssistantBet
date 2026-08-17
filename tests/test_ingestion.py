"""Plus aucune perte silencieuse a l'import.

**C'est le defaut caracteristique du projet, pour la cinquieme fois** : une
sortie identique pour l'echec et pour le cas ordinaire. Un bloc malforme,
introuvable ou refuse a l'ecriture disparaissait sans trace — l'ecran affichait
un import reussi, et le manque se decouvrait des semaines plus tard sur la page
de statistiques, quand il ne se reparait plus.

Ces tests verifient les deux moities de la reparation, et il faut les deux : le
compte-rendu **a l'ecran**, au seul moment ou l'information est encore
recuperable, et la ligne **en base**, qui survit a la fermeture de l'onglet.
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import ingestion, picks_import

LOIN = "2099-01-01T20:45:00Z"

TABLEAU = """| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Type | Source |
|---|-------|--------|-----------|------|--------|--------|------|--------|
| 1 | Lyon – Nice | 1N2 | Lyon | 1.65 | 🟢 SAFE | 4 | issue | 1 |
"""

#: Le bloc de confiance tel que le gabarit le demande, mais **casse** : la
#: virgule finale n'est pas du JSON. C'est exactement le cas que le module
#: laissait tomber sans un mot.
BLOC_CASSE = '{"match": "M1", "confiance": 4, "source_level": 1, "faits": [],}'


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session(settings: Settings) -> int:
    """Une session, un match a venir, et le prompt archive qui les nomme."""
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Lyon', 'Nice', ?, 'oddsapi', ?)",
        (sport["id"], LOIN, db.utcnow()),
        settings=settings,
    )
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    session_id = int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)["id"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, "### M1 · Football · Ligue 1 · Lyon – Nice · 01/01 20:45\n", db.utcnow()),
        settings=settings,
    )
    return session_id


_HIDDEN = re.compile(r'<input type="hidden" name="([a-z_0-9]+)" value="([^"]*)"')


def _hidden(html: str) -> dict[str, str]:
    """Les champs caches du formulaire d'import, tels que le navigateur les
    renverrait. Passer par le rendu reel plutot que par l'objet : c'est le
    transport qui est teste, et un champ oublie cote gabarit ne se verrait pas
    autrement."""
    from html import unescape

    return {name: unescape(value) for name, value in _HIDDEN.findall(html)}


# -- Le parcours complet ----------------------------------------------------


def test_un_bloc_casse_laisse_une_ligne_en_base_et_une_ligne_a_l_ecran(
    client: TestClient, migrated: Settings
) -> None:
    """**Le critere d'acceptation du chantier.** Avant, ce bloc disparaissait :
    l'import annoncait une selection enregistree, et rien nulle part ne disait
    qu'un bloc avait ete recu et refuse."""
    session_id = _session(migrated)
    rendu = TABLEAU + f"\n```conf\n{BLOC_CASSE}\n```\n"

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": rendu})
    assert apercu.status_code == 200
    champs = _hidden(apercu.text)
    assert champs["rejects"], "les rejets doivent voyager avec le formulaire"

    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]
    reponse = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "rejects": champs["rejects"],
            "claims_read": champs.get("claims_read", "0"),
            "open_dossiers": champs.get("open_dossiers", ""),
            "open_dossiers_state": champs.get("open_dossiers_state", ""),
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "event_1": str(event_id),
            "price_1": "1.65",
            "tier_1": "safe",
            "confidence_1": "4",
        },
    )
    assert reponse.status_code == 200

    lignes = db.query("SELECT * FROM ingestion_rejects", settings=migrated)
    motifs = {(row["block_type"], row["reason"]) for row in lignes}
    assert (ingestion.CONF, ingestion.JSON_INVALID) in motifs
    # Le brut accompagne le motif : sans lui on saurait qu'un bloc a echoue et
    # jamais lequel, ce qui reproduirait le silence sous un autre nom.
    casse = next(row for row in lignes if row["reason"] == ingestion.JSON_INVALID)
    assert BLOC_CASSE in casse["raw_payload"]

    # Et le compte-rendu le dit a l'ecran, au moment ou ca se repare.
    assert "rejet(s)" in reponse.text
    assert "JSON illisible" in reponse.text


def test_le_compte_rendu_est_muet_quand_rien_n_est_perdu(
    client: TestClient, migrated: Settings
) -> None:
    """Un compte-rendu qui crie sur un import sain cesserait d'etre lu — meme
    regle que partout : ce qui manque se dit, ce qui va bien ne se decore pas."""
    session_id = _session(migrated)
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]

    reponse = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "rejects": "[]",
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "event_1": str(event_id),
            "tier_1": "safe",
        },
    )

    assert reponse.status_code == 200
    assert db.query_one("SELECT COUNT(*) AS n FROM ingestion_rejects", settings=migrated)["n"] == 0


def test_une_ligne_refusee_a_l_ecriture_est_journalisee(
    client: TestClient, migrated: Settings
) -> None:
    """Une seconde selection sur un match deja pris est refusee par `add_pick`.
    Le message s'affichait ; rien n'en gardait la trace, si bien qu'une garde
    qui mord souvent restait invisible."""
    session_id = _session(migrated)
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]
    commun = {
        "event_1": str(event_id),
        "tier_1": "safe",
        "market_1": "1N2",
        "selection_1": "Lyon",
        "rejects": "[]",
    }
    client.post(f"/history/{session_id}/picks/import", data={**commun, "keep_1": "1"})

    reponse = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "rejects": "[]",
            "keep_1": "1",
            "event_1": str(event_id),
            "tier_1": "fun",
            "market_1": "O/U 2.5",
            "selection_1": "Over 2.5",
        },
    )

    ligne = db.query_one(
        "SELECT * FROM ingestion_rejects WHERE block_type = ?",
        (ingestion.SELECTION,),
        settings=migrated,
    )
    assert ligne is not None
    assert ligne["reason"] == ingestion.DUPLICATE
    assert "rejet(s)" in reponse.text


# -- Les briques, prises separement -----------------------------------------


def test_un_bloc_absent_se_distingue_d_un_bloc_illisible(migrated: Settings) -> None:
    """**Deux motifs, deux gestes.** « le collage n'a pas porte le bloc » se
    repare en recollant ; « le JSON ne se relit pas » se reprend dans le
    gabarit. Les compter ensemble ferait chercher au mauvais endroit."""
    session_id = _session(migrated)

    sans = picks_import.build_preview(session_id, TABLEAU, migrated)
    casse = picks_import.build_preview(
        session_id, TABLEAU + f"\n```conf\n{BLOC_CASSE}\n```\n", migrated
    )

    assert ingestion.FENCE_NOT_FOUND in {reject.reason for reject in sans.rejects}
    assert ingestion.JSON_INVALID in {reject.reason for reject in casse.rejects}


def test_un_champ_manquant_n_est_pas_un_json_illisible(migrated: Settings) -> None:
    session_id = _session(migrated)
    bloc = json.dumps({"match": "M1", "source_level": "inconnu", "faits": []})

    preview = picks_import.build_preview(
        session_id, TABLEAU + f"\n```conf\n{bloc}\n```\n", migrated
    )

    assert ingestion.SCHEMA_INVALID in {reject.reason for reject in preview.rejects}


def test_un_rejet_hors_vocabulaire_reste_un_rejet() -> None:
    """Perdre un rejet parce que son etiquette est inconnue serait exactement le
    silence que ce module supprime."""
    lus = ingestion.from_payload(
        json.dumps([{"block_type": "n'importe quoi", "reason": "idem", "detail": "x"}])
    )

    assert len(lus) == 1
    assert (lus[0].block_type, lus[0].reason) == (ingestion.SELECTION, ingestion.OTHER)


def test_une_charge_utile_illisible_ne_fait_pas_echouer_l_import() -> None:
    assert ingestion.from_payload("{cassé}") == []
    assert ingestion.from_payload("") == []


def test_le_releve_compte_par_type_et_par_motif(migrated: Settings) -> None:
    session_id = _session(migrated)
    ingestion.record(
        session_id,
        [
            ingestion.Reject(ingestion.CONF, ingestion.JSON_INVALID, "un"),
            ingestion.Reject(ingestion.CONF, ingestion.JSON_INVALID, "deux"),
            ingestion.Reject(ingestion.COMBO, ingestion.MATCH_REF_UNRESOLVED, "trois"),
        ],
        migrated,
    )

    releve = ingestion.summary(migrated)

    assert releve.total == 3
    assert releve.sessions == 1
    assert [(row.block_type, row.reason, row.count) for row in releve.rows] == [
        (ingestion.CONF, ingestion.JSON_INVALID, 2),
        (ingestion.COMBO, ingestion.MATCH_REF_UNRESOLVED, 1),
    ]


def test_deux_collages_du_meme_rendu_casse_comptent_deux_fois(
    migrated: Settings,
) -> None:
    """**Aucune deduplication.** Les fondre ferait passer un defaut qui persiste
    pour un defaut qui a eu lieu une fois — le contraire de ce qu'un compteur
    doit dire."""
    session_id = _session(migrated)
    reject = ingestion.Reject(ingestion.CONF, ingestion.JSON_INVALID, "le même")

    ingestion.record(session_id, [reject], migrated)
    ingestion.record(session_id, [reject], migrated)

    assert ingestion.summary(migrated).total == 2
