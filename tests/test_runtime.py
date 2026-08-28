"""Le code charge et le code sur le disque, et ce qui les separe.

## Le defaut qui fonde ce module, mesure a la minute le 28/08/2026

Le gabarit Jinja est relu **sur le disque a chaque generation** ; les modules
Python sont charges **une fois, au demarrage**. Un `git pull` ou une edition sans
redemarrage laisse donc l'application dans un etat mixte — gabarit du jour, code
de la veille — et **rien ne le dit** : le prompt se genere, il est a moitie a
jour, aucune erreur ne se leve.

Mesure de l'episode : processus demarre a 13:12:16, `render.py` ecrit a 13:51:19,
commit a 14:16:16. Le fichier source est **posterieur de 39 minutes** au
processus qui aurait du le charger. Le defaut n'a ete trouve qu'en relisant un
prompt ligne a ligne, et `/health` — qui expose une version de paquet figee —
repondait « ok » pendant ce temps : un indicateur qui repond a la question sans
y repondre.
"""

from __future__ import annotations

import ast
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import runtime


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def test_l_empreinte_bouge_avec_le_contenu(tmp_path: Path) -> None:
    """La primitive detecte une edition, sinon tout le reste est du decor."""
    (tmp_path / "a.py").write_text("x = 1\n")
    avant = runtime.fingerprint_of(tmp_path)
    (tmp_path / "a.py").write_text("x = 2\n")

    assert runtime.fingerprint_of(tmp_path) != avant


def test_un_module_ajoute_bouge_l_empreinte(tmp_path: Path) -> None:
    """Un fichier neuf est un changement de code, comme une ligne modifiee."""
    (tmp_path / "a.py").write_text("x = 1\n")
    avant = runtime.fingerprint_of(tmp_path)
    (tmp_path / "b.py").write_text("y = 1\n")

    assert runtime.fingerprint_of(tmp_path) != avant


def test_l_arbre_servi_est_celui_qui_est_charge() -> None:
    """Sur un arbre qu'on ne touche pas, l'etat est « a jour » — et il est **lu**.

    Le compte de modules est asserte parce qu'une empreinte de rien serait egale
    a une empreinte de rien : le banc passerait sans avoir lu une ligne, ce qui
    est exactement le montage aveugle que ce depot nomme au §8.
    """
    etat = runtime.state()

    assert etat.modules > 1, "le montage doit vraiment hacher les sources servies"
    assert etat.known and not etat.stale
    assert etat.label == runtime.UP_TO_DATE


def test_une_empreinte_de_chargement_differente_dit_obsolete() -> None:
    etat = runtime.state(loaded="0" * 12)

    assert etat.known and etat.stale
    assert etat.label == runtime.STALE


def test_sans_source_lisible_l_etat_est_inconnu_et_jamais_a_jour(tmp_path: Path) -> None:
    """**Le troisieme etat, et c'est celui qui compte.**

    Un garde qui ne peut pas verifier doit le dire. Rendre « a jour » quand il
    n'y a rien a hacher serait indiscernable d'une verification reussie — le
    defaut caracteristique du projet, pose sur le dispositif de verification.
    """
    etat = runtime.state(root=tmp_path)

    assert etat.modules == 0
    assert not etat.known
    assert not etat.stale, "on n'accuse pas plus qu'on ne rassure quand on ne sait pas"
    assert etat.label == runtime.UNKNOWN


def test_l_instantane_est_pris_au_demarrage() -> None:
    """`main` importe `runtime` **au niveau du module**, donc a l'import du processus.

    Si l'import etait paresseux — a la premiere requete — l'instantane
    capturerait le disque **apres** l'edition, et le garde ne pourrait plus rien
    voir. C'est la seule chose qui rend la comparaison honnete, et elle ne se
    voit pas a la lecture de `runtime` seul.
    """
    arbre = ast.parse(Path("src/myassistantbet/main.py").read_text(encoding="utf-8"))
    importes = {
        alias.name
        for node in arbre.body
        if isinstance(node, ast.ImportFrom) and node.module
        for alias in node.names
        if node.module.endswith("services")
    }

    assert "runtime" in importes, (
        "main doit importer runtime au niveau du module, sans quoi l'instantane "
        "est pris trop tard et le garde ne peut plus rien constater"
    )


def test_health_expose_l_etat_du_code(client: TestClient) -> None:
    """`/health` repond quand on l'interroge : c'est la ou un operateur regarde."""
    charge = client.get("/health").json()["code"]

    assert charge["etat"] == runtime.UP_TO_DATE
    assert charge["modules"] > 1
    assert charge["charge"] == charge["disque"]


def test_le_bandeau_se_tait_quand_le_code_est_a_jour(client: TestClient) -> None:
    """Rien quand tout va bien : une pastille permanente cesse d'etre un signal.

    Le bandeau rendu est asserte avant l'absence. Le premier jet interrogeait
    `/banner`, qui n'existe pas : la reponse etait un 404, « Code » n'y figurait
    pas, et le banc passait **sans avoir rendu un bandeau**. Une absence ne se
    verifie que sur une sortie dont on a montre qu'elle porte le reste.
    """
    texte = client.get("/").text

    assert "Crédits Odds API" in texte, "le montage doit vraiment rendre un bandeau"
    assert "obsolète" not in texte


def test_le_bandeau_nomme_le_code_obsolete(client: TestClient, monkeypatch) -> None:
    """Le bandeau se voit **quand on ne cherchait pas**, et c'est ce qui manquait.

    `/health` repond a qui l'interroge ; ce defaut-ci n'a ete trouve qu'en
    relisant un prompt ligne a ligne. La pastille ne se resorbe pas toute seule
    — seul un redemarrage l'eteint — donc elle appartient a la meme famille que
    les competitions non rattachees.
    """
    monkeypatch.setattr(
        board_service.runtime,
        "state",
        lambda **_: runtime.RuntimeState("aaaaaaaaaaaa", "bbbbbbbbbbbb", 74),
    )

    texte = client.get("/").text
    assert "obsolète" in texte
    assert "redémarrer le service" in texte


def test_le_prompt_ne_porte_rien_de_tout_cela(client: TestClient) -> None:
    """**Regle 5 : une sortie dit ce que le lecteur peut en faire, rien de plus.**

    Le modele ne peut pas redemarrer un service. L'information n'a donc rien a
    faire dans une sortie qui s'adresse a lui, si utile soit-elle a l'humain —
    et un mode d'emploi qui ne sert jamais se paierait a chaque lot.
    """
    gabarit = Path("src/myassistantbet/templates/prompts/session_default.md.j2")

    corps = gabarit.read_text(encoding="utf-8")
    for mot in ("obsolète", "redémarr", "empreinte du code"):
        assert mot not in corps, f"« {mot} » n'a pas sa place dans le prompt"
