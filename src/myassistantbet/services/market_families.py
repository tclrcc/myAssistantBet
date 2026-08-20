"""Familles de marches : regrouper des libelles qui decrivent le meme pari.

Neuf regroupements de marches sur cent selections, dont six vus **une seule
fois** : chacun mesurait le hasard, et le bloc entier ne se lisait pas. Or
`O/U` et `O/U 2.5` sont le meme pari a une ligne pres, `Vainqueur` et `1N2`
designent la meme chose sur deux sports, `Handicap` et `Hand. jeux` aussi.
Eclates, aucun n'atteint un effectif lisible ; groupes, trois familles le font.

Deux niveaux de cle, et il faut les deux :

  · la **cle fine** (`market_key`) distingue `O/U 2.5` de `O/U 3.5`. C'est elle
    qui titre une ligne du detail, et deux orthographes du meme libelle — « Éq.
    buts » et « Eq. buts » — y tombent deja ensemble.
  · la **cle de famille** (`family_key`) retire en plus la valeur de ligne, qui
    est un parametre du marche et non un autre marche. Sans elle, chaque seuil
    rencontre — `O/U 2.5`, `O/U 3.5`, `Jeux O/U 18.5` — reclamerait sa propre
    ligne de correspondance, et la liste « a classer » ne desemplirait jamais.

**Rien n'est deduit d'un libelle**, comme pour le niveau d'une competition : la
table est une decision humaine, cle par cle. Le vocabulaire est connu — c'est
celui de `render.MARKET_ORDER_BY_SPORT`, puisque le prompt impose de choisir un
marche present dans le bloc — mais une saisie a la main reste libre, et une cle
inconnue n'est **jamais** rangee d'office dans « Autre » : elle est reclamee
dans les reglages. `autre` est une decision, pas un depotoir de ce qu'on n'a pas
regarde.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from ..config import Settings, get_settings
from ..db import connect
from .render import MARKET_ORDER, MARKET_ORDER_BY_SPORT, MERGED_MARKETS

logger = logging.getLogger(__name__)

#: Les familles, dans l'ordre ou elles se lisent. Elles decrivent la **forme du
#: pari** et non son sujet — sauf `equipe`, et l'exception est justifiee : un
#: total d'equipe et un total de match ne se comportent pas pareil, quand un
#: handicap buts et un handicap jeux, eux, oui.
FAMILIES = {
    "issue": "Issue",
    "handicap": "Handicap",
    "total": "Total",
    "equipe": "Par équipe",
    "autre": "Autre",
}


def family_label(key: str | None) -> str:
    return FAMILIES.get(key or "", "")


def family_rank(key: str | None) -> int:
    order = list(FAMILIES)
    return order.index(key) if key in order else len(order)


def market_key(text: str) -> str:
    """Regroupe les libelles de marche, qui sont ecrits a la main.

    `Over 2.5 buts`, `over 2,5 buts` et `Over  2.5 Buts` sont le meme marche.
    La normalisation de `matching.py` ne convient pas ici : elle retire les
    chiffres aux extremites, et « Over 2.5 » y deviendrait « over ».
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def family_key(text: str) -> str:
    """Cle de famille : la cle fine, moins la valeur de ligne **finale**.

    `o u 2 5` et `jeux o u 18 5` deviennent `o u` et `jeux o u` — une ligne est
    un parametre du marche, pas un autre marche.

    **Seuls les nombres de fin sont retires**, et c'est ce qui rend la regle
    sure : « les 2 equipes marquent » garde son 2, qui n'est pas une ligne mais
    une partie du nom. Retirer tout nombre ou qu'il soit aurait produit une cle
    que personne ne reconnait dans les reglages.
    """
    tokens = market_key(text).split()
    while tokens and tokens[-1].isdigit():
        tokens.pop()
    return " ".join(tokens)


#: Correspondance de depart, verifiee libelle par libelle contre
#: `render.MARKET_ORDER_BY_SPORT` — le vocabulaire que le prompt met sous les
#: yeux de l'analyse — et contre les libelles reellement presents en base.
#: La migration 027 rejoue exactement cette table ; un test compare les deux.
FAMILY_SEED: dict[str, str] = {
    # -- Issue : sur qui gagne ------------------------------------------
    "1n2": "issue",
    "dc": "issue",
    "double chance": "issue",
    "vainqueur": "issue",
    # Qui passe le tour. C'est une **issue**, a l'echelle de la double
    # confrontation et non du match : le 1N2 d'une manche retour et « Se
    # qualifie » repondent a la meme question sur deux perimetres.
    "se qualifie": "issue",
    "set": "issue",  # « Set 1 », « Set 2 » : le vainqueur d'une manche
    "mt ft": "issue",
    "corners 1n2": "issue",
    "podium": "issue",
    # -- Handicap -------------------------------------------------------
    "handicap": "handicap",
    "hand jeux": "handicap",
    "hand s1": "handicap",
    # -- Total : combien, toutes equipes confondues ---------------------
    "o u": "total",
    "jeux o u": "total",
    "mt o u": "total",
    "jeux s1": "total",
    "total buts": "total",
    "nombre total de buts t reg": "total",
    # -- Par equipe : la production d'un camp ---------------------------
    #
    # Un total d'equipe est un total par la forme, et il est ici parce que son
    # **sujet** change tout : « plus de 1.5 but pour Lyon » et « plus de 2.5
    # buts dans le match » ne se gagnent pas dans les memes scenarios.
    "btts": "equipe",
    "btts mt": "equipe",
    "eq buts": "equipe",
    "les 2 equipes marquent t reg": "equipe",
    # -- Autre : range ici par decision, jamais par defaut ---------------
    #
    # Corners et cartons sont des totaux **d'une autre grandeur** : les melanger
    # aux buts ferait decrire deux choses par un seul taux. Les props buteurs
    # sont des marches de joueur, et « Cotes » est le libelle libre de la saisie
    # manuelle — il peut recouvrir n'importe quoi, ce qui est exactement la
    # raison de ne rien en deduire.
    "score exact": "autre",
    "score ex mt": "autre",
    "corners": "autre",
    "cartons": "autre",
    "buteur": "autre",
    "1er buteur": "autre",
    "cotes": "autre",
}


def _canonical(key: str) -> str:
    """La cle sous laquelle un marche fusionne se lit, une seule fois.

    `render.MERGED_MARKETS` fait tenir une variante « alternate » et sa base sur
    une meme ligne du bloc : `spreads` se lit sous `alternate_spreads`,
    `alternate_totals` sous `totals`. Un libelle designe donc les deux cles a la
    fois, et c'est la cible de la fusion qui les nomme.
    """
    return MERGED_MARKETS.get(key, key)


def _vocabulary(sport_key: str) -> dict[str, set[str]]:
    """Libelle normalise -> cles canoniques, pour un sport.

    Un ensemble et non une cle : rien ne garantit a priori qu'un libelle n'en
    designe qu'une, et c'est justement ce qu'il faut pouvoir constater pour
    refuser de trancher.
    """
    order = MARKET_ORDER_BY_SPORT.get(sport_key, MARKET_ORDER)
    found: dict[str, set[str]] = {}
    for key, label in order:
        found.setdefault(market_key(label), set()).add(_canonical(key))
    return found


def market_key_for(sport_key: str, label: str) -> str | None:
    """Cle de marche d'un libelle de selection, ou None si rien ne le nomme.

    Le libelle d'une selection est **recopie a la main** depuis le bloc, et le
    bloc n'ecrit que le vocabulaire de `render.MARKET_ORDER_BY_SPORT` : le lire
    n'est donc pas deduire quoi que ce soit d'un texte libre, c'est reconnaitre
    un mot qu'on a soi-meme imprime. Hors de ce vocabulaire, **aucune cle** —
    « Double chance » la ou le bloc ecrit « DC » reste non resolu et se reclame,
    meme regle que le niveau d'une competition ou la famille d'un marche.

    Le sport decide : « Vainqueur » est le `h2h` d'un match de tennis et
    l'`outright` d'une etape de cyclisme.

    **Deux passes, et l'ordre compte.** La ligne d'un total fait partie du
    libelle recopie (« O/U 2.5 ») sans faire partie du marche : elle se retire
    donc pour la seconde tentative. Mais la retirer d'emblee confondrait
    « Set 1 » et « Set 2 », deux marches distincts dont le numero **est** le
    nom — d'ou l'essai exact d'abord.

    Ambigu, c'est-a-dire deux cles non fusionnees sous un meme libelle : None.
    Le cas n'existe pas dans le vocabulaire actuel, et s'il apparaissait, en
    choisir une au hasard rattacherait la selection au mauvais marche du releve.
    """
    known = _vocabulary(sport_key)
    for candidate in (market_key(label), family_key(label)):
        keys = known.get(candidate)
        if keys and len(keys) == 1:
            return next(iter(keys))
        if keys:
            logger.warning(
                "Libelle de marche ambigu pour %s : %r designe %s",
                sport_key,
                label,
                sorted(keys),
            )
            return None
    return None


def load(settings: Settings | None = None) -> dict[str, str]:
    """Correspondance cle de famille -> famille, telle qu'elle est reglee."""
    with connect(settings) as conn:
        rows = conn.execute("SELECT market_key, family FROM market_families").fetchall()
    return {row["market_key"]: row["family"] for row in rows}


def family_of(text: str, known: dict[str, str]) -> str | None:
    """Famille d'un libelle de marche, ou None s'il n'est pas classe."""
    return known.get(family_key(text))


def set_family(key: str, family: str, settings: Settings | None = None) -> None:
    """Classe un libelle de marche, ou retire son classement.

    Une famille inconnue **retire** la ligne plutot que d'ecrire une valeur que
    rien ne saurait rendre : le marche repasse alors dans la liste a classer,
    ce qui se voit, au lieu de sortir des statistiques sans un mot.
    """
    cleaned = family_key(key)
    if not cleaned:
        return
    with connect(settings) as conn:
        if family in FAMILIES:
            conn.execute(
                "INSERT INTO market_families (market_key, family) VALUES (?, ?) "
                "ON CONFLICT(market_key) DO UPDATE SET family = excluded.family",
                (cleaned, family),
            )
        else:
            conn.execute("DELETE FROM market_families WHERE market_key = ?", (cleaned,))
    logger.info("Famille du marche %s : %s", cleaned, family if family in FAMILIES else "retiree")


@dataclass
class UnclassifiedMarket:
    """Un libelle de marche present en base et rattache a aucune famille.

    Une cle inconnue ne doit jamais tomber d'office dans « Autre » : le
    regroupement se lirait comme une decision alors que ce serait un oubli, et
    la ligne la plus interessante — un marche nouveau qu'on essaie — serait la
    premiere a disparaitre dans le fourre-tout.
    """

    key: str
    #: La derniere orthographe rencontree, pour que la ligne se reconnaisse.
    label: str
    picks: int = 0
    settled: int = 0


@dataclass
class ClassifiedMarket:
    """Un libelle deja range dans une famille, **et ce qu'il porte vraiment**.

    L'ecran rendait un tiret dans la colonne « Selections » sur **toutes** les
    lignes classees, si bien que `vainqueur` — 76 selections — et `cotes` —
    aucune — s'y lisaient a l'identique. Le tiret etait cense distinguer une
    entree seedee d'une entree vue en base ; il ne distinguait rien, et la page
    ne permettait donc pas de repondre a la seule question qu'on lui pose devant
    un libelle qui surprend : est-ce que quelque chose est range la-dedans ?

    Meme forme que le defaut caracteristique du projet — la meme sortie pour
    « jamais employe » et pour « employe soixante-seize fois ».
    """

    key: str
    family: str
    picks: int = 0
    settled: int = 0

    @property
    def unused(self) -> bool:
        """Aucune selection. **Un fait, pas un defaut** : le catalogue seede des
        marches que le bloc sait ecrire et qu'on n'a jamais joues."""
        return self.picks == 0


def classified(settings: Settings | None = None) -> list[ClassifiedMarket]:
    """Les marches ranges, avec le compte de ce qu'ils portent.

    Le compte se fait sur la **cle de famille**, comme le classement lui-meme :
    « O/U 2.5 » et « O/U 3.5 » comptent tous deux sous `o u`, sans quoi la
    colonne dirait zero sur un marche que la table groupe bel et bien.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute("SELECT market, result FROM picks").fetchall()
        known = {
            row["market_key"]: row["family"]
            for row in conn.execute("SELECT market_key, family FROM market_families")
        }

    compte: dict[str, list[int]] = {key: [0, 0] for key in known}
    for row in rows:
        cle = family_key(row["market"])
        if cle in compte:
            compte[cle][0] += 1
            if row["result"] in ("win", "loss"):
                compte[cle][1] += 1

    return [
        ClassifiedMarket(key=key, family=known[key], picks=compte[key][0], settled=compte[key][1])
        for key in sorted(known)
    ]


def unclassified(settings: Settings | None = None) -> list[UnclassifiedMarket]:
    """Marches a classer : vus dans l'historique, absents de la table."""
    settings = settings or get_settings()
    known = load(settings)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT market, result FROM picks ORDER BY created_at DESC, id DESC"
        ).fetchall()

    found: dict[str, UnclassifiedMarket] = {}
    for row in rows:
        key = family_key(row["market"])
        if not key or key in known:
            continue
        # La premiere rencontree est la plus recente : c'est l'orthographe du
        # dernier rendu, celle qu'on reconnaitra dans les reglages.
        entry = found.setdefault(
            key, UnclassifiedMarket(key=key, label=(row["market"] or "").strip())
        )
        entry.picks += 1
        if row["result"] in ("win", "loss"):
            entry.settled += 1

    return sorted(found.values(), key=lambda item: (-item.picks, item.key))
