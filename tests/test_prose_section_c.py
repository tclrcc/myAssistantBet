"""La prose de la section C, et le troisieme etat de l'ecrasement.

**Le gabarit ecrit onze colonnes, `HEADERS` en declarait huit.** `Angle
(1 ligne)` et `Ce qui la tue` etaient produites a chaque session, collees a
chaque import, et jetees par trois entrees manquantes dans un dictionnaire.
Mesure du 21/08/2026 : les 41 collages archives portent tous l'en-tete complet,
et sur les lignes rapprochables la cellule `Ce qui la tue` est non vide **76
fois sur 76**. Le modele la renseignait sans exception ; c'est la captation qui
manquait.

`invalidation` porte le **controle 7** du cadre — « chaque selection porte une
condition d'invalidation ». C'est la seule colonne de ce chantier qu'un bilan
pourra relire sans precaution de date : elle est ecrite **avant** le coup
d'envoi, donc rien de ce qui vient apres ne peut la contaminer.

Le second volet tient a une ligne de `claim_columns` : une cause d'ecrasement
absente du vocabulaire devenait `None`, indiscernable d'une ligne anterieure au
typage. `is_collection_fault(None)` etant faux, 43 selections comptaient comme
des **observations sur le modele** — « elle s'est notee comme si elle avait
cherche » — alors qu'on ignore tout de ce qui s'est passe.
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
from myassistantbet.services import history as history_service
from myassistantbet.services import picks_import
from myassistantbet.services.confidence import (
    OVERRIDE_INCONNUE,
    OVERRIDE_LIGNE_ABSENTE,
    is_collection_fault,
    is_unknown_cause,
)
from myassistantbet.services.manual import build, save
from tests.helpers import repost_import_form

LOIN = "2099-01-01"

#: Le tableau **tel que le gabarit l'ecrit** : onze colonnes, `Type` pour le
#: vocabulaire ferme et `Angle (1 ligne)` pour la prose. Les deux cotoyees dans
#: le meme en-tete, ce qui est exactement le cas que la separation doit tenir.
#: En fixture et non en litteral, comme le collage complet : une ligne de
#: tableau reelle depasse la largeur de colonne du projet, et la couper pour la
#: faire tenir testerait un tableau que personne ne colle jamais.
TABLEAU = (Path(__file__).parent / "fixtures" / "tableau_section_c.md").read_text(encoding="utf-8")


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str) -> int:
    return save(
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


def _lot(settings: Settings, noms: list[str]) -> int:
    session_id = 0
    for nom in noms:
        session_id = board_service.toggle_selection(_match(settings, nom), True, settings)
    return session_id


def _importer(client: TestClient, session_id: int, page: str) -> None:
    """Reposte le formulaire rendu, **en cochant ce qu'un humain cocherait**.

    Les deux cases de confirmation ne sont pas pre-cochees — c'est tout leur
    objet — donc `repost_import_form` ne les reprend pas. Les ajouter ici, une
    fois, evite que chaque test decide de son cote s'il confirme, ce qui ferait
    de la garde une propriete du test plutot que du parcours.
    """
    envoi = repost_import_form(page)
    envoi["confirm_partial"] = "1"
    envoi["confirm_controls"] = "1"
    client.post(f"/history/{session_id}/picks/import", data=envoi)


def _picks(settings: Settings) -> list[dict[str, object]]:
    with connect(settings) as conn:
        return [
            dict(row)
            for row in conn.execute(
                "SELECT selection, angle, angle_note, invalidation, prose_source "
                "  FROM picks ORDER BY id"
            )
        ]


# -- Les deux colonnes traversent l'import ----------------------------------


def test_la_prose_du_tableau_arrive_en_base(client: TestClient, migrated: Settings) -> None:
    """**Le critère d'acceptation du lot.** Poster le formulaire rendu et relire
    la base : un test qui appellerait `add_pick` directement ne verrait pas une
    colonne restée sans champ, et c'est exactement ainsi que le motif de saisie
    tardive est resté sans surface deux jours."""
    session_id = _lot(migrated, ["Lyon", "Nice"])

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)

    lignes = _picks(migrated)
    assert len(lignes) == 2
    assert lignes[0]["angle_note"] == "Trois titulaires adverses forfait, annoncés le 20/08"
    assert lignes[0]["invalidation"] == "Un but encaissé avant la 20e referme le match"
    assert lignes[0]["prose_source"] == history_service.PROSE_FROM_IMPORT


def test_le_type_et_la_prose_de_l_angle_restent_deux_champs(
    client: TestClient, migrated: Settings
) -> None:
    """`Type` porte le vocabulaire fermé `issue` / `manière`, `Angle (1 ligne)`
    porte une phrase. Les fondre ferait entrer la phrase dans un champ à deux
    valeurs — le commentaire de `HEADERS` signalait le piège depuis le début,
    sans qu'il existe nulle part où verser la prose."""
    session_id = _lot(migrated, ["Lyon", "Nice"])

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)

    lignes = _picks(migrated)
    assert lignes[0]["angle"] == "maniere", "le vocabulaire fermé, normalisé"
    assert lignes[1]["angle"] == "issue"
    assert lignes[0]["angle_note"] != lignes[0]["angle"]


def test_un_tiret_n_est_pas_une_condition_d_invalidation(
    client: TestClient, migrated: Settings
) -> None:
    """**Le contrôle 7 se compte sur ce qui est écrit, pas sur ce qui est
    rempli.** Un rendu écrit `—` pour dire « rien ici » ; le recopier ferait
    passer la ligne pour couverte, et le contrôle passerait sur une sélection
    qui ne porte aucune condition — le défaut exact que ce chantier retire
    ailleurs."""
    session_id = _lot(migrated, ["Lyon", "Nice"])

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)

    seconde = _picks(migrated)[1]
    assert seconde["invalidation"] is None, "le tiret ne dit rien, donc rien n'est écrit"
    assert seconde["angle_note"], "l'autre colonne de la même ligne est bien captée"


def test_la_provenance_reste_nulle_sans_prose(client: TestClient, migrated: Settings) -> None:
    """Un `import` posé sur deux colonnes vides dirait que le collage les portait
    vides, quand il ne les portait pas du tout. Même règle que `price_source` :
    ce qui n'existe pas ne se déclare pas."""
    session_id = _lot(migrated, ["Lyon"])
    huit_colonnes = (
        "### C. Tableau des sélections\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|-------|--------|-----------|------|--------|--------|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
    )

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": huit_colonnes})
    _importer(client, session_id, apercu.text)

    assert _picks(migrated)[0]["prose_source"] is None


# -- La reprise depuis le collage archive ------------------------------------


def _sans_prose(settings: Settings) -> None:
    """Efface la prose captée, en gardant l'`import_id` et les offsets.

    Simule l'état réel de la base : les lignes y sont entrées avant que les
    colonnes existent, mais leur provenance au caractère près, elle, est écrite
    depuis la migration 060.
    """
    with connect(settings) as conn:
        conn.execute("UPDATE picks SET angle_note = NULL, invalidation = NULL, prose_source = NULL")


def test_la_reprise_marque_ce_qu_elle_reconstruit(client: TestClient, migrated: Settings) -> None:
    """**Une reprise et une captation ne se lisent pas pareil.** La seconde
    recopie une cellule que le lecteur avait sous les yeux ; la première découpe
    une ligne par ses offsets, donc par une règle qui peut échouer. Le compte
    des non retrouvées est ce qui rend la passe vérifiable."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)
    _sans_prose(migrated)

    rapport = picks_import.rebuild_prose(apply=True, settings=migrated)

    assert rapport.scanned == 2
    assert rapport.matched == 2, rapport.line
    assert not rapport.missed
    lignes = _picks(migrated)
    assert lignes[0]["invalidation"] == "Un but encaissé avant la 20e referme le match"
    assert lignes[0]["prose_source"] == history_service.PROSE_REBUILT


def test_la_reprise_n_ecrase_jamais_une_valeur_captee(
    client: TestClient, migrated: Settings
) -> None:
    """Une valeur captée au collage vaut toujours mieux qu'une valeur
    reconstruite, et une passe rejouée deux fois ne doit rien changer."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)

    premier = picks_import.rebuild_prose(apply=True, settings=migrated)
    assert premier.scanned == 0, "rien à reprendre : tout a été capté"

    _sans_prose(migrated)
    picks_import.rebuild_prose(apply=True, settings=migrated)
    second = picks_import.rebuild_prose(apply=True, settings=migrated)
    assert second.scanned == 0, "la reprise est idempotente"
    assert all(row["prose_source"] == history_service.PROSE_REBUILT for row in _picks(migrated))


def test_la_simulation_n_ecrit_rien(client: TestClient, migrated: Settings) -> None:
    """Même contrat que le rejeu : on regarde d'abord, on écrit ensuite."""
    session_id = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    _importer(client, session_id, apercu.text)
    _sans_prose(migrated)

    rapport = picks_import.rebuild_prose(settings=migrated)

    assert rapport.written == 2
    assert all(row["prose_source"] is None for row in _picks(migrated))


# -- Le troisieme etat de l'ecrasement ---------------------------------------


def test_une_cause_absente_devient_un_etat_nomme(migrated: Settings) -> None:
    """**`None` était le trou.** `claim_columns` écrivait `None` dès que le motif
    sortait du vocabulaire, et 43 sélections des sessions 11 et 13 sont entrées
    ainsi — comptées comme des observations sur le modèle par un
    `is_collection_fault(None)` qui vaut faux."""
    colonnes = history_service.claim_columns("", "2", opened=False, override_cause="")

    assert colonnes.cause == OVERRIDE_INCONNUE
    assert not is_collection_fault(colonnes.cause), "ce n'est pas un collage perdu identifié"
    assert is_unknown_cause(colonnes.cause), "ni une observation sur le modèle"


def test_un_motif_connu_n_est_pas_ecrase_par_l_inconnu(migrated: Settings) -> None:
    """Le repli ne se déclenche que sur ce qui n'a pas de nom."""
    colonnes = history_service.claim_columns(
        "", "2", opened=False, override_cause=OVERRIDE_LIGNE_ABSENTE
    )

    assert colonnes.cause == OVERRIDE_LIGNE_ABSENTE
    assert is_collection_fault(colonnes.cause)


def test_le_null_historique_se_lit_comme_inconnu(migrated: Settings) -> None:
    """La colonne est nommée depuis la migration 073, mais une base plus ancienne
    rendrait encore un NULL. Le lire comme « inconnu » plutôt que de le laisser
    tomber dans le compte des observations **est** la différence que ce troisième
    état existe pour tenir."""
    assert is_unknown_cause(None)
    assert not is_collection_fault(None)


def test_les_trois_etats_ne_se_melangent_pas() -> None:
    """La ligne de session porte les trois nombres séparément : une session où
    l'analyse ne cherche jamais, une dont le collage a échoué, et une dont on ne
    sait rien. Les deux premières appellent des gestes opposés ; la troisième
    n'en appelle aucun, et c'est **pour ça** qu'elle doit se voir."""
    ligne = history_service.SessionRate(
        session_id=1,
        label="",
        day="2026-08-14",
        overridden=3,
        override_faults=16,
        override_unknown=43,
    )

    assert "3" in ligne.override_line
    assert "16 non transmise(s)" in ligne.override_line
    assert "43 sans cause" in ligne.override_line


def test_une_population_sans_cause_ne_rend_pas_une_ligne_vide() -> None:
    """Sinon elle serait indiscernable d'une population où rien n'a été écrasé —
    la forme la plus coûteuse qu'un défaut prenne ici, puisqu'elle se lit comme
    une mesure."""
    assert "sans cause enregistrée" in history_service.Override(total=0, unknown=43).line
    assert history_service.Override().line == ""
