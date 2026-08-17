"""Le registre des chemins d'ecriture, et pourquoi il ne se tient pas a la main.

Le lot 2 a laisse cette note dans `selfcheck.py` : *« le controle prouve que les
chemins **declares** journalisent, jamais qu'ils sont tous declares. La regle de
`CONTRIBUTING.md` en tient lieu. »* Elle n'en tient pas lieu, et c'est mesure :
`replay` a ete ecrit **le meme jour et par la meme main** que cette regle, et il
a laisse tomber ses echecs d'ecriture sans les journaliser. Une regle de
contribution ne se declenche pas ; un test si.

Ce module porte donc la declaration, et `tests/test_write_paths.py` verifie
qu'elle est **complete** — pas seulement qu'elle est juste.

## Pourquoi l'analyse statique, et pas les deux autres pistes

Le brief laissait trois enumerations possibles. Les deux premieres ont ete
essayees sur le depot avant d'ecrire une ligne :

- **la convention de nommage** ne tient pas : les trois fonctions d'ecriture
  s'appellent `add_pick`, `record` et `save`. Il faudrait donc en inventer une,
  c'est-a-dire remplacer une regle qu'on peut oublier par une autre regle qu'on
  peut oublier — exactement ce que ce module existe pour ne pas faire ;
- **l'inspection du module** ne voit que ce qui est importe, et ne distingue pas
  une fonction qui ecrit d'une fonction qui lit. Elle prouverait qu'un decorateur
  a ete pose, jamais qu'il manque quelque part ;
- **l'analyse statique** se pose sur la chose elle-meme — une fonction qui porte
  un `INSERT INTO` vers l'une des tables gardees. Ce critere ne depend d'aucune
  discipline : il est vrai ou faux dans la source, et il le reste le jour ou
  personne ne se souvient de ce fichier.

## Ce qui est garde, et ce qui ne l'est pas

`GUARDED` porte les tables ou se pose une **prediction** : la selection, le
combine qui la regroupe, le score en sets. Le bloc de confiance et la ligne
exploratoire n'ont pas de table a eux — ce sont des colonnes de `picks`, donc le
meme chemin d'ecriture, ce que le registre dit en attachant plusieurs types de
bloc a une seule fonction.

`ingestion_rejects` et `imports_raw` **n'y sont pas** : ce sont le journal et le
brut, c'est-a-dire ce qui rattrape les autres. Les garder ferait de la trace un
chemin a tracer, et la recursion n'apprendrait rien.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import TypeVar

from .ingestion import BLOCK_TYPES

#: Les tables dont une insertion pose une prediction dans la base. Une fonction
#: qui en porte une doit figurer au registre, et le test le verifie par lecture
#: de la source.
GUARDED = ("picks", "combos", "combo_legs", "set_scores")


@dataclass(frozen=True)
class WritePath:
    """Une fonction d'ecriture declaree, et ce qu'elle peut perdre.

    `block_types` est au pluriel parce qu'une seule fonction peut perdre
    plusieurs familles : `add_pick` ecrit la selection, son bloc de confiance et
    son drapeau exploratoire, qui sont trois colonnes de la meme ligne. Les
    separer en trois fonctions pour faire joli dans un registre serait laisser le
    registre commander le code.
    """

    qualified: str
    block_types: tuple[str, ...]

    @property
    def module(self) -> str:
        return self.qualified.rsplit(".", 1)[0]

    @property
    def name(self) -> str:
        return self.qualified.rsplit(".", 1)[1]


#: Le registre, rempli au chargement des modules de service par le decorateur
#: ci-dessous. Cle : le nom qualifie, `myassistantbet.services.history.add_pick`.
REGISTRY: dict[str, WritePath] = {}

_F = TypeVar("_F", bound=Callable[..., object])


def writes(*block_types: str) -> Callable[[_F], _F]:
    """Declare une fonction d'ecriture et les familles de blocs qu'elle porte.

    Le decorateur **ne fait rien a l'execution** : il n'enveloppe pas, ne mesure
    pas, ne journalise pas. Il declare. Envelopper l'appel ajouterait une couche
    entre le service et sa base pour un gain nul, et le projet a deja tranche que
    ce qui se lit dans la source ne se recopie pas ailleurs.
    """
    inconnus = [kind for kind in block_types if kind not in BLOCK_TYPES]
    if inconnus:
        raise ValueError(f"Type de bloc inconnu au registre d'ecriture : {inconnus!r}")

    def decorate(func: _F) -> _F:
        qualified = f"{func.__module__}.{func.__qualname__}"
        REGISTRY[qualified] = WritePath(qualified=qualified, block_types=tuple(block_types))
        return func

    return decorate


def declared_block_types() -> tuple[str, ...]:
    """Les familles de blocs qu'au moins un chemin d'ecriture declare porter.

    **C'est le denominateur du controle**, et c'est pour ca qu'il se derive :
    « 8 sur 8 » ne veut rien dire quand les deux nombres sont ecrits a la main.
    Un type de bloc ajoute ici sans exemplaire malforme fait tomber le controle,
    ce qui est le comportement voulu — le meme que le banc de transport.
    """
    found = {kind for path in REGISTRY.values() for kind in path.block_types}
    return tuple(kind for kind in BLOCK_TYPES if kind in found)


def load() -> None:
    """Importe les modules qui declarent un chemin, pour que le registre existe.

    Un registre rempli par decorateur ne contient que ce qui a ete **importe**.
    Le controle et son test doivent donc forcer l'import, sans quoi ils
    verifieraient un registre vide et passeraient — le silence sous un autre nom.
    """
    from . import combos, history, set_scores  # noqa: F401
