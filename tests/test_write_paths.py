"""Le registre des chemins d'ecriture est-il **complet** ?

Le lot 2 avait laisse cette limite en toutes lettres : le controle prouve que
les chemins declares journalisent, jamais qu'ils sont tous declares, « la regle
de `CONTRIBUTING.md` en tient lieu ». Elle n'en tenait pas lieu — `replay` a ete
ecrit le meme jour que la regle et l'a violee.

Ce fichier remplace la regle par un test. Il lit la **source** plutot que les
objets importes : ce qui doit etre vrai est qu'aucune fonction n'insere dans une
table gardee sans etre declaree, et cette propriete se lit dans le texte du
depot, pas dans l'etat d'un interpreteur.
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

import pytest

from myassistantbet.services import write_paths
from myassistantbet.services.ingestion import BLOCK_TYPES, SOURCE

SRC = Path(__file__).resolve().parents[1] / "src" / "myassistantbet"

#: `INSERT INTO picks (…` — la table gardee, quelle que soit la mise en forme du
#: SQL. Le litteral est concatene sur plusieurs lignes dans le code servi, donc
#: la recherche se fait sur la **valeur** de la chaine reconstituee par l'AST et
#: jamais sur le texte brut du fichier.
_INSERT = re.compile(
    r"\binsert\s+into\s+(" + "|".join(write_paths.GUARDED) + r")\b",
    re.IGNORECASE,
)


def _module_name(path: Path, root: Path = SRC) -> str:
    parts = path.relative_to(root.parent).with_suffix("").parts
    return ".".join(parts)


def _inserting_functions(root: Path = SRC) -> dict[str, str]:
    """Nom qualifie -> table, pour toute fonction du paquet qui insere.

    L'analyse porte sur les **litteraux de chaine** contenus dans le corps de la
    fonction : c'est ainsi que le projet ecrit son SQL, et c'est ce qui rend le
    critere independant de tout nommage. Une fonction imbriquee est attribuee a
    sa fonction englobante, `ast` donnant deja le `qualname` par le chemin des
    noeuds.
    """
    found: dict[str, str] = {}
    for path in sorted(root.rglob("*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        module = _module_name(path, root)
        for node in ast.walk(tree):
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Constant) or not isinstance(child.value, str):
                    continue
                match = _INSERT.search(child.value)
                if match:
                    found[f"{module}.{_qualname(tree, node)}"] = match.group(1).lower()
                    break
    return found


def _qualname(tree: ast.Module, target: ast.AST) -> str:
    """Le `qualname` d'une fonction, reconstruit depuis la racine du module."""
    chemin: list[str] = []

    def descend(node: ast.AST, prefixe: list[str]) -> bool:
        for child in ast.iter_child_nodes(node):
            if isinstance(child, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
                suite = [*prefixe, child.name]
                if child is target:
                    chemin.extend(suite)
                    return True
                if descend(child, suite):
                    return True
            elif descend(child, prefixe):
                return True
        return False

    descend(tree, [])
    return ".".join(chemin)


def test_toute_fonction_qui_insere_est_declaree_au_registre() -> None:
    """**Le test qui manquait au lot 2.**

    Ajouter une fonction d'ecriture sans la declarer fait echouer la suite. Le
    message nomme la fonction et la table, parce qu'un test qui dit seulement
    « ca ne correspond pas » se fait desactiver.
    """
    write_paths.load()
    ecrivains = _inserting_functions()
    declares = set(write_paths.REGISTRY)
    manquants = {nom: table for nom, table in ecrivains.items() if nom not in declares}
    assert not manquants, (
        "Ces fonctions insèrent dans une table gardée sans être déclarées au "
        "registre des chemins d'écriture. Pose `@writes(...)` dessus, et donne à "
        "`selfcheck-ingestion` un exemplaire malformé du format concerné :\n  "
        + "\n  ".join(f"{nom} → {table}" for nom, table in sorted(manquants.items()))
    )


def test_le_registre_ne_declare_rien_qui_n_ecrive_pas() -> None:
    """La reciproque, et elle compte autant.

    Un chemin declare mais qui n'ecrit plus est un controle qui tourne pour
    rien : il donnerait au compte de `selfcheck` un denominateur que le code ne
    porte plus, c'est-a-dire exactement le « 8 sur 8 » sans signification que ce
    chantier supprime.
    """
    write_paths.load()
    ecrivains = set(_inserting_functions())
    fantomes = sorted(set(write_paths.REGISTRY) - ecrivains)
    assert not fantomes, (
        "Ces chemins sont déclarés au registre et n'insèrent plus rien : "
        f"{fantomes}. Retire le décorateur, ou le contrôle compte une case vide."
    )


def test_les_trois_ecrivains_connus_sont_la() -> None:
    """Un garde-fou sur le garde-fou : l'analyse voit-elle encore quelque chose ?

    Les deux tests ci-dessus passent tous les deux quand `_inserting_functions`
    ne rend **rien** — deux ensembles vides sont egaux. C'est le defaut
    caracteristique du projet applique a son propre controle : une sortie
    identique pour le succes et pour la panne. Un `INSERT` reecrit en
    concatenation de variables, ou un `rglob` qui ne trouve plus le paquet, le
    rendrait muet sans qu'aucun test ne tombe.
    """
    ecrivains = _inserting_functions()
    assert ecrivains["myassistantbet.services.history.add_pick"] == "picks"
    assert ecrivains["myassistantbet.services.combos.record"] in {"combos", "combo_legs"}
    assert ecrivains["myassistantbet.services.set_scores.save"] == "set_scores"


def test_le_denominateur_du_controle_se_derive_du_registre() -> None:
    """Les familles de blocs a couvrir ne s'ecrivent pas a la main.

    `add_pick` porte la selection, son bloc de confiance et sa ligne
    exploratoire : trois colonnes d'une meme ligne, donc un seul chemin et trois
    familles. Le registre le dit, et c'est de la que `selfcheck` tire ce qu'il
    doit couvrir.
    """
    write_paths.load()
    types = write_paths.declared_block_types()
    # `SOURCE` n'est pas une famille de blocs du rendu : c'est une source amont
    # qui se fige, constatee a la collecte et non a l'import. Elle n'a donc aucun
    # chemin d'ecriture de **prediction**, et l'exiger ferait reclamer un
    # exemplaire malforme a `selfcheck` pour un format qui n'existe pas.
    attendues = set(BLOCK_TYPES) - {SOURCE}
    assert set(types) == attendues, (
        "Le registre ne couvre pas toutes les familles de blocs du rendu : "
        f"{sorted(attendues - set(types))} n'ont aucun chemin d'écriture déclaré."
    )
    # L'ordre suit `BLOCK_TYPES` : le compte-rendu du controle se lit deux fois
    # de suite, et deux ordres differents feraient chercher une difference.
    assert list(types) == [kind for kind in BLOCK_TYPES if kind in set(types)]


def test_une_fonction_d_ecriture_non_enregistree_fait_echouer_la_suite(tmp_path: Path) -> None:
    """**Le critère d'acceptation du §1, vérifié sur l'analyse elle-même.**

    Le test qui garde le dépôt ne peut pas se prouver en ajoutant une fonction
    au dépôt — il tomberait, ce qui est bien le comportement voulu mais ne
    laisserait pas la suite verte. On rejoue donc l'analyse sur un faux paquet
    portant un écrivain non déclaré, et on vérifie qu'elle le voit.

    C'est aussi ce qui distingue ce contrôle d'une convention de nommage : la
    fonction s'appelle ici `enregistrer_quelque_chose`, un nom qu'aucune règle
    n'aurait prévu, et elle est détectée quand même.
    """
    faux = tmp_path / "faux_paquet"
    faux.mkdir()
    (faux / "nouveau.py").write_text(
        "def enregistrer_quelque_chose(conn):\n"
        '    conn.execute("INSERT INTO picks (session_id) VALUES (?)", (1,))\n',
        encoding="utf-8",
    )

    trouve = _inserting_functions(faux)

    assert trouve == {"faux_paquet.nouveau.enregistrer_quelque_chose": "picks"}
    assert set(trouve) - set(write_paths.REGISTRY), (
        "l'écrivain non déclaré doit ressortir comme manquant au registre"
    )


def test_un_insert_ecrit_sur_plusieurs_lignes_est_vu(tmp_path: Path) -> None:
    """Le SQL du dépôt est concaténé ligne à ligne, et c'est le cas ordinaire.

    `add_pick` écrit `"INSERT INTO picks (…, " "…) VALUES (…)"` sur six lignes.
    Une recherche sur le texte brut du fichier verrait la première ; c'est la
    concaténation résolue par l'AST qui rend le critère fiable, et c'est
    justement la forme qu'un lecteur pressé casserait en « optimisant » le test.
    """
    faux = tmp_path / "coupe"
    faux.mkdir()
    (faux / "m.py").write_text(
        "def ecrit(conn):\n"
        "    conn.execute(\n"
        '        "INSERT INTO "\n'
        '        "set_scores (session_id) VALUES (?)",\n'
        "        (1,),\n"
        "    )\n",
        encoding="utf-8",
    )

    assert _inserting_functions(faux) == {"coupe.m.ecrit": "set_scores"}


def test_un_type_de_bloc_inconnu_est_refuse_a_la_declaration() -> None:
    """Une faute de frappe dans un `@writes` ne doit pas creer une famille.

    Sans ce refus, `@writes("confiance")` ajouterait une sixieme famille au
    denominateur, que rien ne saurait couvrir, et le controle echouerait en
    designant le mauvais coupable.
    """
    with pytest.raises(ValueError, match="Type de bloc inconnu"):
        write_paths.writes("confiance")


def test_tout_chemin_d_entree_journalise_ou_declare_pourquoi_il_ne_peut_pas() -> None:
    """**Il était muet, et c'est la deuxième fois sur le même fichier.**

    `CONTRIBUTING.md` dit de la première : « `myassistantbet-replay` a été écrit
    le même jour et par la même main que cette phrase, et il a laissé tomber ses
    échecs d'écriture sans les journaliser ». Le rattachement du lot 9 l'a refait
    — un bloc qui ne trouve pas sa sélection, un combiné dont une jambe manque,
    se disaient à l'écran et nulle part ailleurs.

    `PATHS` s'énumère à la main : rien dans la source ne distingue une route
    d'entrée d'une route quelconque. Ce test ne peut donc pas prouver que la
    liste est complète — il vérifie ce qui est prouvable : que **chaque chemin
    listé** couvre chaque famille du registre, ou déclare pourquoi il ne le peut
    pas. Une exemption se déclare, elle ne se devine pas : sans cette table, le
    premier réflexe devant un manque serait de retirer le chemin de la liste,
    c'est-à-dire de le rendre muet à nouveau.
    """
    from myassistantbet import selfcheck

    write_paths.load()
    familles = set(write_paths.declared_block_types())
    inconnues = {
        (chemin, nom)
        for chemin, exemptions in selfcheck.IMPOSSIBLE.items()
        for nom in exemptions
        if nom not in familles
    }

    assert not inconnues, (
        f"Ces exemptions visent une famille que le registre ne déclare plus : {inconnues}. "
        "Une exemption qui ne correspond à rien fait passer un contrôle pour couvert."
    )
    assert set(selfcheck.IMPOSSIBLE) <= set(selfcheck.PATHS), (
        "Une exemption sur un chemin qui n'existe pas ne garde rien."
    )
    assert "rattachement" in selfcheck.PATHS, (
        "Le rattachement écrit dans `combos` : sans son entrée ici, il ne se contrôle nulle part."
    )
