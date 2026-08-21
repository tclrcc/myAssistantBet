"""Ce qui fait qu'un fait du bloc porte sa source, sa date et son niveau.

## Pourquoi l'attribution se pose a l'assemblage, et pas ailleurs

Mesure du 21/08/2026, sur 17 538 lignes de contexte archivees : **20 % portaient
une date, 8 % une source**. Le contrat du bloc de donnees demande les trois
attributs sur chaque fait ; ecrits dans le texte des lignes, ils auraient demande
de retoucher soixante-quatre libelles et de les tenir a jour un par un.

Ils n'ont pas a y etre ecrits. `context.fetched_at` est en base sur les
3 278 releves, et chaque producteur connait sa propre source : **l'attribution se
derive**, elle ne se saisit pas.

## Par tranche, jamais par une table libelle -> source

`session.context_block` est le seul assembleur et appelle ses producteurs un par
un. Chaque tranche connait donc sa source sans qu'on ait a la nommer ligne par
ligne, et **un libelle ajoute demain dans `tennis_history` herite de
l'attribution de sa tranche** sans que personne y pense.

Une table `libelle -> source` posee a cote aurait diverge au premier libelle
ajoute — le piege deja paye par `markets.py` et `render.py`, ou un marche ajoute
d'un seul cote sortait en cle brute dans le prompt.

## Le niveau plafonne a 3, et c'est un fait sur nos fournisseurs

L'echelle du projet classe **par editeur**. Aucun fournisseur du pipeline n'est
l'instance qui publie : ni API-Football, ni Tennis Abstract, ni tennis-data.co.uk
ne sont une federation, une ligue ou un club. Ils sont des statistiques tierces,
donc niveau 3.

**Une seule exception, et elle est deja dans le code** : une alerte officielle est
emise par le service meteo national, et `weather.py` recopie son emetteur de la
charge utile plutot que de le deviner. C'est lui qui fait le niveau 1.

Les niveaux 1 et 2 relevent donc de la **verification**, pas de la collecte. Ce
n'est pas un reglage prudent : c'est la description du regime en vigueur, ou lire
un bloc vaut `lecture` et ou c'est la recherche qui monte la confiance.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass, replace

#: Le niveau d'un fait qu'on ne sait pas attribuer. **Emis, jamais supprime.**
#:
#: La regle d'origine du contrat disait qu'un fait sans ses trois attributs etait
#: traite comme absent ; appliquee a l'existant, elle supprimait 80 % du bloc. Ce
#: qu'elle voulait empecher — qu'un fait mal etabli porte une selection — le
#: plafond l'empeche aussi, et il le **dit** au lieu de le taire : une
#: suppression est indiscernable d'une donnee jamais collectee, un `niveau 4` se
#: voit. Meme regle que partout ici.
UNKNOWN_LEVEL = 4

#: Le plafond de ce que la collecte peut porter. Voir le module.
COLLECTED_LEVEL = 3

#: Le niveau d'une source qui **est** l'instance emettrice.
AUTHORITY_LEVEL = 1


@dataclass(frozen=True)
class Fait:
    """Un fait du bloc, avec ce qui permet de juger ce qu'il vaut.

    `label` et `valeur` sont ce que le bloc affiche depuis toujours ; les trois
    autres sont ce que la lecture jetait.

    `date` est **None et jamais une date de repli** : une date inventee se lirait
    comme un releve, alors que tout l'objet de l'attribution est de pouvoir
    verifier quand la chose a ete vue.
    """

    label: str
    valeur: str
    source: str | None = None
    date: str | None = None
    niveau: int = UNKNOWN_LEVEL

    @property
    def ligne(self) -> tuple[str, str]:
        """La forme que le rendu texte consomme depuis toujours."""
        return (self.label, self.valeur)


@dataclass(frozen=True)
class Tranche:
    """Un producteur de faits, et ce que ses lignes valent.

    Une tranche est un **appel** de l'assembleur, pas un libelle : c'est ce qui
    la rend insensible a l'ajout d'une ligne.
    """

    cle: str
    source: str
    niveau: int = COLLECTED_LEVEL


#: Les producteurs de lignes de contexte, par tranche d'assemblage.
#:
#: **Nos propres scans sortent en niveau 3 avec une source qui les nomme.** Ils
#: n'ont pas d'editeur — ce sont des derives de nos relevés — mais leur inventer
#: une case dans une echelle qui n'en a que quatre serait pire : le lecteur ne
#: saurait pas quoi en faire. Niveau 3 dit la seule chose qui compte, qu'un fait
#: du bloc ne monte pas une confiance a lui seul.
TRANCHES: dict[str, Tranche] = {
    "context": Tranche("context", "api-football"),
    "dossier": Tranche("dossier", "api-football"),
    "elo": Tranche("elo", "tennis-abstract"),
    "tennis_history": Tranche("tennis_history", "tennis-data.co.uk"),
    "serve_stats": Tranche("serve_stats", "tennis-api.com"),
    "tennis_load": Tranche("tennis_load", "myassistantbet (scans)"),
    "tennis_round": Tranche("tennis_round", "myassistantbet (scans)"),
    "weather": Tranche("weather", "open-meteo"),
    #: La densite du bloc et la cause de son vide. Ce n'est pas un fait sur le
    #: match mais une mesure de ce que la collecte a rapporte : le contrat lui
    #: reserve un bloc `collecte`, et il ne descend jamais dans les attributs.
    "collecte": Tranche("collecte", "myassistantbet", UNKNOWN_LEVEL),
}


def attribue(
    lignes: Iterable[tuple[str, str]] | Iterable[Fait],
    tranche: str,
    date: str | None = None,
) -> list[Fait]:
    """Attribue une tranche entiere. Les faits deja attribues ne sont pas repris.

    Le second cas n'est pas une subtilite : `context` date ses lignes **par
    type de releve** — douze types pour vingt-neuf lignes — et cette date-la est
    plus fine que celle de la tranche. L'ecraser rendrait tout un bloc a l'heure
    du releve le plus recent.
    """
    connue = TRANCHES.get(tranche)
    source = connue.source if connue else None
    niveau = connue.niveau if connue else UNKNOWN_LEVEL
    faits: list[Fait] = []
    for ligne in lignes:
        if isinstance(ligne, Fait):
            faits.append(ligne if ligne.source else replace(ligne, source=source, niveau=niveau))
            continue
        label, valeur = ligne
        faits.append(Fait(label=label, valeur=valeur, source=source, date=date, niveau=niveau))
    return faits


def lignes(faits: Sequence[Fait]) -> list[tuple[str, str]]:
    """La forme `(libelle, valeur)` que le rendu texte consomme depuis toujours.

    **L'adaptateur, et non un second assemblage.** Le prompt et la fiche d'un
    match doivent porter le meme bloc ; deux assemblages paralleles ont deja
    diverge deux fois.
    """
    return [fait.ligne for fait in faits]
