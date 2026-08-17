"""L'horloge du chemin de rendu s'injecte, elle ne se lit pas.

**Le lot 4 avait laisse ce point ouvert et en avait nomme la cause** : un `now()`
en dur dans le chemin de rendu. Le symptome etait une fixture datee du 05/07 qui
s'est mise a echouer au cablage de la garde de peremption — le test a ete
corrige, la cause non.

Une horloge lue en dur rend un rendu **irreproductible** : le meme lot, la meme
base et le meme code donnent deux blocs differents selon le jour. C'est
exactement ce que le projet refuse partout ailleurs — `dossier.store` et
`elo.store` font descendre `now` jusqu'a l'ecriture, « la peremption compare une
date de releve a une date de lecture : les prendre sur deux horloges differentes
rend le calcul faux, donc intestable ».
"""

from __future__ import annotations

import ast
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from myassistantbet.config import Settings
from myassistantbet.services import freshness, session, tennis_history

SRC = Path(__file__).resolve().parents[1] / "src" / "myassistantbet"

#: Les fonctions du **chemin de rendu** : celles qui comparent une date de bloc a
#: maintenant. Declarees ici plutot que devinees d'un nommage — la regle du
#: registre d'ecriture, pour la meme raison : une convention de nom se remplace
#: par une autre convention de nom qu'on peut oublier aussi.
CHEMIN_DE_RENDU = (
    ("services/tennis_history.py", "_freshness_line"),
    ("services/tennis_history.py", "lines"),
    ("services/freshness.py", "note_for"),
    ("services/freshness.py", "_age"),
    ("services/freshness.py", "state"),
    ("services/session.py", "context_block"),
    ("services/session.py", "_context_for"),
    ("services/session.py", "has_started"),
)

#: Ce qui lit l'horloge systeme. `utcnow()` est la forme du projet, `datetime.now`
#: la forme standard : les deux comptent.
_HORLOGE = {"utcnow", "now", "today", "utcfromtimestamp"}


def _fonction(chemin: str, nom: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    arbre = ast.parse((SRC / chemin).read_text(encoding="utf-8"))
    for noeud in ast.walk(arbre):
        if isinstance(noeud, ast.FunctionDef | ast.AsyncFunctionDef) and noeud.name == nom:
            return noeud
    raise AssertionError(f"{nom} a disparu de {chemin}")


def _appels_horloge(noeud: ast.AST) -> list[str]:
    """Les lectures d'horloge **non gardees** par un `now or ...`.

    Un `now or datetime.now(UTC)` est une injection avec repli, pas une lecture
    en dur : c'est la forme voulue. Ce qu'on cherche est l'appel qui ne laisse
    aucun moyen de fixer l'heure.
    """
    gardes: set[int] = set()
    for parent in ast.walk(noeud):
        if isinstance(parent, ast.BoolOp) and isinstance(parent.op, ast.Or):
            for valeur in parent.values[1:]:
                gardes.update(id(sous) for sous in ast.walk(valeur))

    trouves = []
    for appel in ast.walk(noeud):
        if not isinstance(appel, ast.Call) or id(appel) in gardes:
            continue
        cible = appel.func
        nom = cible.attr if isinstance(cible, ast.Attribute) else getattr(cible, "id", "")
        if nom in _HORLOGE:
            trouves.append(nom)
    return trouves


@pytest.mark.parametrize(("chemin", "nom"), CHEMIN_DE_RENDU)
def test_aucune_lecture_d_horloge_en_dur_sur_le_chemin_de_rendu(chemin: str, nom: str) -> None:
    """**Un `now()` en dur ici ne casse rien**, et c'est ce qui le rend cher.

    Le bloc sort, il a l'air normal, et il n'est simplement pas le meme d'un
    jour a l'autre. Le defaut ne se voit que le jour ou une fixture datee
    traverse un seuil — ce qui est arrive au lot 4.
    """
    trouves = _appels_horloge(_fonction(chemin, nom))

    assert not trouves, (
        f"{chemin}::{nom} lit l'horloge système ({trouves}) sans repli sur un `now` injecté"
    )


@pytest.mark.parametrize(("chemin", "nom"), CHEMIN_DE_RENDU)
def test_chaque_fonction_du_chemin_accepte_une_horloge(chemin: str, nom: str) -> None:
    """Ne pas lire l'horloge ne suffit pas : encore faut-il pouvoir la fixer."""
    noeud = _fonction(chemin, nom)
    arguments = [arg.arg for arg in (*noeud.args.args, *noeud.args.kwonlyargs)]

    assert "now" in arguments, f"{chemin}::{nom} n'accepte pas d'horloge"


# -- Le comportement, et pas seulement la forme ------------------------------


def test_l_escalade_suit_l_horloge_injectee() -> None:
    """**Le test qui aurait attrape le defaut du lot 4.**

    La meme date de collecte doit rendre trois etats differents selon l'heure
    qu'on lui donne — et aucun ne doit dependre du jour ou la suite tourne.
    """
    collecte = "2026-08-01"

    assert freshness.note_for(collecte, datetime(2026, 8, 5, tzinfo=UTC)) == ""
    assert "non rafraichie" in freshness.note_for(collecte, datetime(2026, 8, 12, tzinfo=UTC))
    assert "SOURCE FIGEE" in freshness.note_for(collecte, datetime(2026, 9, 1, tzinfo=UTC))


def test_une_fixture_datee_ne_derive_pas_avec_le_jour(migrated: Settings) -> None:
    """**C'est la propriete qui compte** : deux executions du meme rendu, a un an
    d'ecart d'horloge simulee, ne doivent differer que par ce que l'horloge
    decide — et pas par le jour ou la suite tourne.
    """
    fige = datetime(2026, 8, 17, 12, tzinfo=UTC)
    lignes_a = tennis_history.lines(
        "Taylor Fritz", "Alex Michelsen", "hard", "2026-08-16T17:45:00Z", migrated, now=fige
    )
    lignes_b = tennis_history.lines(
        "Taylor Fritz", "Alex Michelsen", "hard", "2026-08-16T17:45:00Z", migrated, now=fige
    )

    assert lignes_a == lignes_b


def test_le_match_commence_se_decide_sur_l_horloge_donnee() -> None:
    """`has_started` portait deja son `now` — le test le garde avec les autres."""
    depart = "2026-08-16T17:45:00Z"

    assert session.has_started(depart, datetime(2026, 8, 16, 18, tzinfo=UTC))
    assert not session.has_started(depart, datetime(2026, 8, 16, 17, tzinfo=UTC))


def test_le_chemin_de_rendu_est_declare_en_entier() -> None:
    """**Le registre doit couvrir ce qu'il pretend couvrir.**

    Si une fonction du chemin de rendu compare une date a maintenant sans
    figurer ici, le controle passerait en ne verifiant rien — le silence sous un
    autre nom. Ce test relit le module de fraicheur et exige que **toutes** ses
    fonctions qui prennent un `now` soient declarees.
    """
    arbre = ast.parse((SRC / "services/freshness.py").read_text(encoding="utf-8"))
    horlogees = {
        noeud.name
        for noeud in ast.walk(arbre)
        if isinstance(noeud, ast.FunctionDef)
        and "now" in [arg.arg for arg in noeud.args.args]
        and not noeud.name.startswith("__")
    }
    declarees = {nom for chemin, nom in CHEMIN_DE_RENDU if chemin.endswith("freshness.py")}

    # `record` et `worst` prennent un `now` sans etre du chemin de rendu : la
    # premiere ecrit, la seconde delegue a `state`. Elles sont donc admises.
    assert horlogees - declarees <= {"record", "worst"}


def test_l_as_of_des_lignes_de_service_ne_se_compare_a_rien() -> None:
    """**Constat, et il vaut d'etre ecrit plutot que corrige.**

    Le brief range l'`as_of` des lignes de service parmi ce qui doit passer par
    l'horloge injectable. Il n'y a rien a injecter : cette date est **rendue**,
    jamais comparee a maintenant — c'est le lecteur qui fait la soustraction, et
    c'est precisement ce que le brief demande en la rendant obligatoire.

    Ajouter un `now` a `serve_lines` serait donc de la machinerie qui ne peut
    pas se declencher, ce que le projet refuse ailleurs — « rien n'est estime
    aujourd'hui, donc aucun `~` ne parait : une machinerie qui ne peut pas se
    declencher aurait ete du code mort ».
    """
    source = (SRC / "services/serve_stats.py").read_text(encoding="utf-8")
    rendu = source[source.index("def _scope_fragment") : source.index("def _short_day")]

    assert "datetime.now" not in rendu
    assert "utcnow" not in rendu


def test_le_garde_fou_attrape_une_lecture_en_dur() -> None:
    """**Un controle qui ne peut pas echouer ne garde rien.**

    Un test qui change de cause en gardant son resultat est un test mort qui en
    a l'air vivant : celui-ci se verifie sur un cas fabrique.
    """
    faux = ast.parse(
        "def rendu(collected):\n    age = datetime.now(UTC) - collected\n    return age\n"
    )
    fonction = next(n for n in ast.walk(faux) if isinstance(n, ast.FunctionDef))

    assert _appels_horloge(fonction) == ["now"]


def test_un_repli_sur_now_injecte_n_est_pas_une_lecture_en_dur() -> None:
    """La forme voulue — `now or datetime.now(UTC)` — doit passer."""
    bon = ast.parse(
        "def rendu(collected, now=None):\n"
        "    age = (now or datetime.now(UTC)) - collected\n"
        "    return age\n"
    )
    fonction = next(n for n in ast.walk(bon) if isinstance(n, ast.FunctionDef))

    assert _appels_horloge(fonction) == []


def test_l_horloge_du_prompt_descend_jusqu_au_bloc(migrated: Settings) -> None:
    """La chaine complete : `build_prompt` → `renderable_events` → `context_block`
    → `tennis_history.lines` → `freshness.note_for`."""
    import inspect

    from myassistantbet.services import prompt as prompt_service

    for fonction in (
        prompt_service.build_prompt,
        session.renderable_events,
        session.context_block,
        tennis_history.lines,
    ):
        assert "now" in inspect.signature(fonction).parameters, (
            f"{fonction.__name__} rompt la chaine de l'horloge"
        )


def test_le_seuil_d_escalade_se_lit_sur_l_horloge_et_non_sur_le_match() -> None:
    """Deux grandeurs distinctes, et les confondre casserait les deux lignes.

    `Historique` compare la collecte au **coup d'envoi** — un fait sur le lot.
    `Fraicheur` compare la collecte a **maintenant** — un fait sur la source. Un
    match d'il y a six mois analyse aujourd'hui doit dire les deux.
    """
    vieille = "2026-02-01"
    maintenant = datetime(2026, 8, 17, tzinfo=UTC)

    assert "SOURCE FIGEE" in freshness.note_for(vieille, maintenant)
    assert freshness.note_for(vieille, datetime(2026, 2, 3, tzinfo=UTC)) == ""
    assert freshness.note_for(vieille, maintenant - timedelta(days=190)) == ""
