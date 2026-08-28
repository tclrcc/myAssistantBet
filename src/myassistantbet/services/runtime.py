"""Le code **charge** et le code **sur le disque**, et de quoi voir qu'ils divergent.

## Le defaut, mesure a la minute le 28/08/2026

Le gabarit Jinja est relu sur le disque **a chaque generation** — c'est voulu,
l'editer suffit a changer le prompt sans redeploiement. Les modules Python, eux,
sont charges **une fois**, au demarrage du processus. Un `git pull` ou une
edition sans redemarrage laisse donc l'application dans un etat mixte : gabarit
du jour, code de la veille.

**Rien ne le dit.** Le prompt se genere, il est a moitie a jour, aucune erreur ne
se leve — le defaut caracteristique de ce projet, applique cette fois au
deploiement lui-meme. Mesure de l'episode : processus demarre a 13:12:16,
`render.py` ecrit a 13:51:19, commit a 14:16:16. Le fichier source est posterieur
de **39 minutes** au processus qui aurait du le charger, donc il ne pouvait pas
l'avoir fait. La moitie d'un lot livre — celle qui depend de `render.py` — n'etait
pas en service, et **seule une relecture ligne a ligne du prompt l'a montre**.

`/health` repondait « ok » pendant ce temps. Il expose `version`, qui est la
version du **paquet** : elle ne bouge pas quand le code bouge. C'est le cas ou un
indicateur existe et repond a la question sans y repondre.

## Ce que ce module compare, et ce qu'il ne compare pas

Une empreinte prise **a l'import** — donc au demarrage du processus, ce que
Python vient de charger — contre une empreinte du disque **au moment de
l'appel**. Aucun git, aucune version de paquet, aucune date : deux sommes de la
meme chose a deux instants.

`changelog.fingerprint` est **appelee** et non reecrite : c'est deja la fonction
qui repond a « ce jeu de fichiers a-t-il change », et `prompt.template_fingerprint`
l'emploie pour les gabarits. Une seconde implementation aurait diverge sur le
premier detail — le nom entrant dans la somme, par exemple, ce qui distingue deux
fichiers dont on echangerait le contenu.

**L'instantane depend de l'ordre d'import**, et c'est la seule chose fragile ici :
`main` importe ce module au niveau du module, donc a l'ouverture du processus. Un
import paresseux — a la premiere requete — capturerait le disque **apres**
l'edition, et le garde ne verrait plus rien. Un banc lit `main.py` et l'exige.

## Trois etats, jamais deux

`inconnu` n'est pas un repli : c'est le cas ou les sources ne sont pas lisibles —
un deploiement sans fichiers `.py`, une installation figee. Rendre « a jour »
alors serait indiscernable d'une verification reussie, et un garde qui se tait
quand il ne peut pas verifier est le defaut que ce module existe pour supprimer.
Il ne rend pas non plus « obsolete » : on n'accuse pas plus qu'on ne rassure
quand on ne sait pas.

## Ce que ce module ne dit pas

Il ne dit pas **quoi** a change, ni depuis quand. Le seul geste qu'il appelle est
un redemarrage, et ce geste est le meme quelle que soit la ligne modifiee : un
detail de plus serait du decor. Il ne dit rien non plus des dependances
installees — `uv run --frozen` les fige, et un `uv sync` sans redemarrage tombe
sous la meme regle sans etre visible ici. Limite connue, pas oubli.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from .changelog import fingerprint

#: La racine des sources de l'application. Toutes les sources et pas seulement
#: celles du rendu : n'importe quel module change le comportement, et une
#: empreinte partielle ferait manquer exactement le changement qu'on n'avait pas
#: prevu. Meme raisonnement que `template_fingerprint`, qui couvre tous les
#: gabarits et pas le seul qui rend.
SOURCE_ROOT = Path(__file__).resolve().parent.parent

#: Les trois etats. Une enumeration et non du texte libre : deux surfaces les
#: lisent, et deux orthographes de « obsolete » feraient deux garde-fous.
UP_TO_DATE = "a jour"
STALE = "obsolete"
UNKNOWN = "inconnu"


def sources(root: Path | None = None) -> list[Path]:
    """Les fichiers source de l'application, tries."""
    base = root or SOURCE_ROOT
    return sorted(base.rglob("*.py")) if base.is_dir() else []


def fingerprint_of(root: Path | None = None) -> str:
    """L'empreinte des sources presentes sous `root`, au moment de l'appel.

    Vide quand il n'y a rien a hacher : c'est ce qui distingue « je n'ai pas pu
    regarder » de « rien n'a bouge », et les deux ne se rendent pas pareil.
    """
    fichiers = sources(root)
    return fingerprint(fichiers) if fichiers else ""


#: L'empreinte du code **tel qu'il vient d'etre charge**. Calculee a l'import de
#: ce module, donc au demarrage — voir le docstring de tete.
LOADED = fingerprint_of()


@dataclass(frozen=True)
class RuntimeState:
    """Ce qui est charge, ce qui est sur le disque, et sur combien de fichiers."""

    loaded: str
    disk: str
    modules: int

    @property
    def known(self) -> bool:
        """Les deux empreintes ont pu etre calculees. Sinon on ne conclut pas."""
        return bool(self.loaded) and bool(self.disk)

    @property
    def stale(self) -> bool:
        return self.known and self.loaded != self.disk

    @property
    def label(self) -> str:
        if not self.known:
            return UNKNOWN
        return STALE if self.stale else UP_TO_DATE


def state(loaded: str | None = None, root: Path | None = None) -> RuntimeState:
    """L'etat du code servi, recalcule a chaque appel.

    `loaded` et `root` existent pour le banc : le defaut se simule en changeant
    l'un des deux termes, et il n'y a pas d'autre facon de le monter sans
    reecrire les sources de l'application sous ses propres pieds.
    """
    return RuntimeState(
        loaded=LOADED if loaded is None else loaded,
        disk=fingerprint_of(root),
        modules=len(sources(root)),
    )
