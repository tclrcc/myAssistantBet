"""Extraction des angles pour le classement a l'aveugle.

**Le dernier point manuel du protocole, et le seul qui se detruisait en
s'appliquant.** Extraire les angles a la main oblige a lire les deux sorties :
l'anonymat serait fictif avant d'avoir commence.

    uv run python -m myassistantbet.blind gabarit.md payload.md --graine 4712

rend une liste numerotee **nue** — un angle par ligne, sans section, sans palier,
sans cran, sans nature ni niveau de source — dans un ordre melange.

    uv run python -m myassistantbet.blind gabarit.md payload.md --graine 4712 --lever

rejoue le meme melange et rend l'origine de chaque numero.

## Aucune cle sur disque

La graine **est** la cle : le melange se rejoue a l'identique, donc rien n'a a
etre ecrit a cote, et il n'existe aucun fichier qu'il faille s'interdire
d'ouvrir. Consigner le nombre suffit.

## Les tableaux se reconnaissent par le lecteur qui les importe

`EXPLORATORY_HEAD`, `SECTION_HEAD`, `_cells` et `_map_columns` viennent de
`picks_import` : ce sont eux qui font foi, et une seconde expression reguliere
posee ici finirait par ne plus designer les memes lignes — le piege deja paye
deux fois par l'assembleur de contexte, une fois par le decoupage en sections.

**Ce module ne modifie pas le lecteur pour autant.** La colonne « Angle » est de
la prose que l'import ne garde pas — il n'en retient que la *nature*, `issue` ou
`maniere` — et lui ajouter un champ toucherait le canal retour pour un usage qui
lui est etranger. Elle se lit donc ici, sur le decoupage du lecteur.
"""

from __future__ import annotations

import argparse
import random
import sys
from dataclasses import dataclass
from pathlib import Path

from .services.picks_import import (
    EXPLORATORY_HEAD,
    SECTION_HEAD,
    _cells,
    _fold,
    _is_separator,
    _map_columns,
)

#: Les en-tetes sous lesquels la colonne de prose se presente. **Distincte de
#: `angle` au sens de l'import**, qui vaut `issue` ou `maniere` et se lit sous
#: « Type » : les confondre ferait entrer une phrase entiere dans un champ a deux
#: valeurs, ou l'inverse — un mot la ou l'on attend un argument.
ANGLE_HEADERS = ("angle", "pourquoi", "argument")

#: Ce qui accompagne un angle dans le tableau et **doit disparaitre** : c'est la
#: structure qui trahit la version, et le jugement porte sur les arguments.
STRIPPED = ("section", "palier", "cran de confiance", "type", "niveau de source")


@dataclass(frozen=True)
class Angle:
    """Un angle, et d'ou il vient. L'origine ne sort qu'a la levee."""

    texte: str
    origine: str


def _angle_column(cells: list[str]) -> int | None:
    """L'indice de la colonne de prose, ou None si l'en-tete ne la nomme pas."""
    for rang, cellule in enumerate(cells):
        if _fold(cellule).strip() in ANGLE_HEADERS:
            return rang
    return None


def read_angles(raw: str, origine: str) -> list[Angle]:
    """Les angles des sections C et C-bis, dans l'ordre du rendu.

    **Les deux sections, et c'est voulu** : C-bis porte la population temoin, et
    l'ecarter comparerait deux ensembles construits sous des exigences
    differentes.
    """
    colonne: int | None = None
    entete: list[str] | None = None
    angles: list[Angle] = []
    for brut in (raw or "").splitlines():
        line = brut.rstrip("\r\n")
        if EXPLORATORY_HEAD.search(_fold(line)) or SECTION_HEAD.match(line):
            entete, colonne = None, None
            continue
        cells = _cells(line)
        if cells is None or _is_separator(cells):
            continue
        if entete is None:
            if _map_columns(cells) is not None:
                entete, colonne = cells, _angle_column(cells)
            continue
        if colonne is None or colonne >= len(cells):
            continue
        texte = cells[colonne].strip()
        if texte:
            angles.append(Angle(texte=texte, origine=origine))
    return angles


def shuffled(angles: list[Angle], graine: int) -> list[Angle]:
    """Melange reproductible. **Une instance locale de `Random`**, jamais le
    generateur global : deux appels dans le meme processus doivent rendre le
    meme ordre, sans quoi la levee ne retomberait pas sur le classement."""
    ordre = list(angles)
    random.Random(graine).shuffle(ordre)
    return ordre


def _read(chemin: str) -> str:
    return Path(chemin).read_text(encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("gabarit", help="sortie produite depuis le prompt gabarit")
    parser.add_argument("payload", help="sortie produite depuis le bloc de donnees")
    parser.add_argument("--graine", type=int, required=True, help="a consigner : elle est la cle")
    parser.add_argument(
        "--lever", action="store_true", help="rejoue le melange et rend l'origine de chaque numero"
    )
    args = parser.parse_args(argv)

    angles = read_angles(_read(args.gabarit), "gabarit") + read_angles(
        _read(args.payload), "payload"
    )
    if not angles:
        print("aucun angle lu : les tableaux n'ont pas ete reconnus", file=sys.stderr)
        return 1

    ordre = shuffled(angles, args.graine)
    for rang, angle in enumerate(ordre, start=1):
        print(f"{rang:>3}. {angle.origine if args.lever else angle.texte}")
    if not args.lever:
        print(
            f"\n{len(ordre)} angles — graine {args.graine}, a consigner avec le classement",
            file=sys.stderr,
        )
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
