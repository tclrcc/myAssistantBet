"""Statistiques de service au tennis : le garde-fou de quota, d'abord.

Ce module grossira — identite des joueurs, agregats, couverture. Il commence par
ce que le brief impose de poser **avant** de depenser quoi que ce soit : de quoi
savoir ou l'on en est du quota, et de quoi s'arreter proprement.

## Pourquoi ce plancher ne ressemble a aucun des deux autres

`ODDS_API_CREDIT_FLOOR` et `APIFOOTBALL_CALL_FLOOR` gardent des quotas
**journaliers** : un plancher franchi se rouvre tout seul le lendemain, et le
cout d'une erreur est une journee. Celui-ci garde un quota **mensuel** —
150 000 appels, remis a zero tous les 31 jours, mesure dans les en-tetes le
17/08/2026. Une reprise d'historique qui l'epuiserait le 8 du mois laisserait
l'application sans donnees de service jusqu'au renouvellement.

D'ou une difference de comportement, et elle est voulue : **on s'arrete, on ne
degrade pas.** Le dossier d'equipe, lui, se suspend en laissant passer le
contexte, parce que le contexte est la fonction premiere. Ici il n'y a rien a
laisser passer : ces lignes sont un profil de fond, et un profil de fond
incomplet est precisement ce que le lot 3 a refuse.

## Ce que le compteur ne dit pas, et qu'il faut lire ailleurs

`last_known_quota` rend le **dernier** compteur vu, tous endpoints confondus.
C'est suffisant pour decider d'un depart, et insuffisant pour dimensionner : un
compteur a 149 000 ne dit pas si l'on brule 200 appels par jour ou 20 000.
`consumption()` lit `api_usage` par jour et par endpoint, ce que le fournisseur
n'expose nulle part.
"""

from __future__ import annotations

import json
import logging
import re
import unicodedata
from dataclasses import dataclass, replace
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError, last_known_quota
from ..providers.tennisapi import PREFIX, PROVIDER, TennisAPIClient
from . import freshness
from .ingestion import (
    MATCH_REF_UNRESOLVED,
    OTHER,
    SCHEMA_INVALID,
    SOURCE,
    SOURCE_VIDE,
    Reject,
)
from .ingestion import record as record_rejects
from .labels import sort_key

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Budget:
    """Ce que le quota autorise a l'instant du depart."""

    remaining: int | None
    floor: int
    checked_at: str = ""

    @property
    def known(self) -> bool:
        """Faux tant qu'aucun appel n'a jamais ete emis."""
        return self.remaining is not None

    @property
    def allowed(self) -> bool:
        """**Un quota inconnu laisse partir**, comme chez le dossier d'equipe.

        C'est l'etat d'une installation qui n'a jamais appele le fournisseur :
        refuser le premier appel rendrait le compteur impossible a etablir, donc
        le plancher auto-bloquant pour toujours.
        """
        return self.remaining is None or self.remaining > self.floor

    @property
    def spendable(self) -> int | None:
        """Appels emissibles avant de toucher le plancher. None si inconnu."""
        if self.remaining is None:
            return None
        return max(0, self.remaining - self.floor)

    @property
    def note(self) -> str:
        """La mention a rendre quand le plancher bloque. Vide sinon.

        **Elle dit le mois**, contrairement aux deux autres planchers : celui-ci
        ne se rouvre pas demain, et laisser croire l'inverse ferait attendre une
        reprise qui n'arrivera pas.
        """
        if self.allowed:
            return ""
        return (
            f"collecte des statistiques de service suspendue — il reste "
            f"{self.remaining} appels tennis-api.com, sous le plancher de {self.floor}. "
            "Le quota est mensuel : il se rouvre au renouvellement, pas demain."
        )


def budget(settings: Settings | None = None) -> Budget:
    """Etat du quota, tel qu'il decide d'un depart de collecte."""
    settings = settings or get_settings()
    quota = last_known_quota(PROVIDER, settings)
    return Budget(
        remaining=None if quota is None else int(quota["remaining"]),
        floor=settings.rapidapi_call_floor,
        checked_at="" if quota is None else str(quota["called_at"]),
    )


@dataclass(frozen=True)
class DayUse:
    """Appels d'un jour, pour un endpoint."""

    day: str
    endpoint: str
    calls: int


def consumption(days: int = 30, settings: Settings | None = None) -> tuple[DayUse, ...]:
    """Appels par jour et par famille d'endpoint, sur la fenetre demandee.

    **La famille est celle que l'appelant a declaree**, jamais une derivee du
    chemin — voir `providers.tennisapi.FAMILIES`. `api_usage.endpoint` porte
    donc deja la famille ; il n'y a rien a redecouper ici.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT substr(called_at, 1, 10) AS day, endpoint, COUNT(*) AS calls "
            "  FROM api_usage WHERE provider = ? "
            "   AND called_at >= date('now', ?) "
            " GROUP BY day, endpoint ORDER BY day DESC, calls DESC",
            (PROVIDER, f"-{max(1, int(days))} days"),
        ).fetchall()
    return tuple(
        DayUse(day=str(row["day"]), endpoint=str(row["endpoint"]), calls=int(row["calls"]))
        for row in rows
    )


# -- Lire une reponse `matches-played` ---------------------------------------
#
# **Le joueur n'est pas toujours du meme cote.** `player1` est celui qui recoit
# le match dans la nomenclature du fournisseur, pas celui qu'on a demande : sur
# les dix matchs de la page 1 de Zverev, il est `player2` trois fois. Chercher
# ses statistiques a une position fixe donnerait celles de son adversaire une
# fois sur trois, en silence — c'est le genre d'erreur qui ne casse rien et se
# lit comme un profil.
#
# Le rapprochement se fait donc sur le **nom**, replie par `labels.sort_key`,
# qui ignore casse et accents. Le lot 4 a paye ce piege exactement : l'API ecrit
# « Mccartney Kessler » quand la base ecrit « McCartney Kessler », et une
# comparaison stricte a rendu « 0 point de service » — un faux negatif de notre
# rapprochement, pas de la source.


@dataclass(frozen=True)
class ServeLine:
    """La table de service d'un joueur sur **un** match, denominateurs compris.

    Les colonnes de l'adversaire voyagent avec (`opp_*`), et ce n'est pas du
    confort : ce sont elles qui portent le **retour**. Un point de retour est un
    point de service adverse que l'adversaire n'a pas gagne, et la reponse sert
    les deux camps — donc aucun second appel n'est necessaire, ce que le brief
    demandait d'etablir plutot que de supposer.
    """

    played_on: str
    surface: str
    opponent: str
    tournament: str = ""
    #: Service, cote joueur.
    first_serve: int = 0
    first_serve_of: int = 0
    aces: int = 0
    double_faults: int = 0
    won_first: int = 0
    won_first_of: int = 0
    won_second: int = 0
    won_second_of: int = 0
    bp_converted: int = 0
    bp_converted_of: int = 0
    total_points_won: int = 0
    #: Service, cote adversaire — d'ou se derive le retour.
    opp_first_serve_of: int = 0
    opp_won_first: int = 0
    opp_won_second: int = 0
    opp_total_points_won: int = 0
    #: L'archive dont cette ligne sort. Zero quand elle vient d'ailleurs.
    archive_id: int = 0

    @property
    def service_points(self) -> int:
        """Points joues sur son service.

        **C'est `firstServeOf` et non `firstServe`**, verifie sur la charge
        utile : `winningOnSecondServeOf` vaut exactement
        `firstServeOf - firstServe`, donc `firstServeOf` compte bien **tous** les
        points de service et non les seules premieres balles rentrees.
        """
        return self.first_serve_of

    @property
    def second_serves(self) -> int:
        """Secondes balles jouees. C'est le denominateur des doubles fautes.

        Un joueur qui rentre 75 % de premieres a mecaniquement moins d'occasions
        d'en commettre : rapporter les doubles fautes aux points de service
        melangerait deux grandeurs.
        """
        return max(0, self.first_serve_of - self.first_serve)

    @property
    def return_points(self) -> int:
        """Points joues en retour = points de service de l'adversaire."""
        return self.opp_first_serve_of

    @property
    def return_points_won(self) -> int:
        """Points de retour gagnes, derives des colonnes adverses."""
        return max(0, self.opp_first_serve_of - self.opp_won_first - self.opp_won_second)

    @property
    def consistent(self) -> bool:
        """`totalPointsWon` doit valoir service gagne + retour gagne.

        **Un invariant de la source, verifie sur les donnees reelles** : sur le
        Zverev – Norrie du 16/08, 53 points de service gagnes sur 74 et 46 points
        de retour gagnes sur 111 font exactement les 99 annonces, et le calcul
        ferme aussi de l'autre cote (65 + 21 = 86).

        Il vaut plus qu'une coherence de facade : c'est la seule chose qui
        rattache les colonnes adverses aux notres. S'il se rompt, la reponse
        melange deux matchs ou deux camps, et les taux de retour seraient faux
        **sans que rien ne le montre**.
        """
        if not self.total_points_won:
            return True
        gagnes = self.won_first + self.won_second + self.return_points_won
        return gagnes == self.total_points_won


def _int(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _side(match: dict[str, Any], name: str) -> tuple[str, str] | None:
    """Les deux cles `playerN`, la notre d'abord. None si le joueur n'y est pas."""
    cible = sort_key(name)
    for mine, theirs in (("player1", "player2"), ("player2", "player1")):
        joueur = match.get(mine) or {}
        if sort_key(str(joueur.get("name") or "")) == cible:
            return mine, theirs
    return None


def parse_matches_played(
    payload: Any, name: str, archive_id: int = 0
) -> tuple[tuple[ServeLine, ...], int]:
    """Les lignes de service d'un joueur dans une reponse `matches-played`.

    Rend les lignes **et** le nombre de matchs ecartes faute de table de
    statistiques : un match sans `stats` n'est pas une erreur — la source ne les
    sert pas sur tout — mais le taire ferait passer une couverture partielle pour
    une couverture pleine. Meme regle que partout : ce qui manque se compte.

    Les matchs ou l'invariant `totalPointsWon` se rompt sont **ecartes**, pas
    corriges : une ligne dont on sait qu'elle melange deux camps n'a pas de
    version reparable.
    """
    lignes: list[ServeLine] = []
    ecartes = 0
    matchs = (payload or {}).get("singles") if isinstance(payload, dict) else None
    for match in matchs or []:
        if not isinstance(match, dict):
            continue
        cotes = _side(match, name)
        if cotes is None:
            ecartes += 1
            continue
        mine, theirs = cotes
        moi = (match.get(mine) or {}).get("stats") or {}
        lui = (match.get(theirs) or {}).get("stats") or {}
        if not moi or not lui:
            ecartes += 1
            continue
        tournoi = match.get("tournament") or {}
        ligne = ServeLine(
            played_on=str(match.get("date") or "")[:10],
            surface=str((tournoi.get("court") or {}).get("name") or ""),
            opponent=str((match.get(theirs) or {}).get("name") or ""),
            tournament=str(tournoi.get("name") or ""),
            first_serve=_int(moi.get("firstServe")),
            first_serve_of=_int(moi.get("firstServeOf")),
            aces=_int(moi.get("aces")),
            double_faults=_int(moi.get("doubleFaults")),
            won_first=_int(moi.get("winningOnFirstServe")),
            won_first_of=_int(moi.get("winningOnFirstServeOf")),
            won_second=_int(moi.get("winningOnSecondServe")),
            won_second_of=_int(moi.get("winningOnSecondServeOf")),
            bp_converted=_int(moi.get("breakPointsConverted")),
            bp_converted_of=_int(moi.get("breakPointsConvertedOf")),
            total_points_won=_int(moi.get("totalPointsWon")),
            opp_first_serve_of=_int(lui.get("firstServeOf")),
            opp_won_first=_int(lui.get("winningOnFirstServe")),
            opp_won_second=_int(lui.get("winningOnSecondServe")),
            opp_total_points_won=_int(lui.get("totalPointsWon")),
            archive_id=archive_id,
        )
        if not ligne.first_serve_of or not ligne.consistent:
            ecartes += 1
            continue
        lignes.append(ligne)
    return tuple(lignes), ecartes


# -- L'identite d'un joueur chez le fournisseur ------------------------------
#
# **Piege numero un de cette source**, paye deux fois au lot 4 : l'API ecrit
# « Mccartney Kessler » quand la base ecrit « McCartney Kessler », et une
# comparaison stricte a rendu « 0 point de service » — un faux negatif de notre
# rapprochement, pas de la source.
#
# La sonde du 17/08/2026 ajoute ce qui change la regle, et qui contredit l'ordre
# de repli du brief. L'endpoint de recherche est **insensible a la casse en
# entree** — `mccartney kessler` trouve `Mccartney Kessler`, le serveur s'en
# charge — mais il n'est **pas** tolerant aux accents : `Karolína Muchová` rend
# une liste vide quand `Karolina Muchova` repond. Le repli d'accents se fait donc
# **avant l'appel**, sur l'entree, et pas seulement sur les candidats rendus.
#
# Deux joueuses de la base sont concernees — « Anna Bondár » et « Iva Jović » —
# et sans ce repli elles seraient restees introuvables sans qu'une ligne le dise.

#: Les niveaux de repli, du plus sur au moins sur. **Une enumeration** : le
#: compte se fait dessus, et deux orthographes du meme niveau feraient deux
#: lignes qui ne se rapprochent plus. Meme regle que les motifs de rejet.
EXACT = "exact"
CASSE = "casse"
ACCENTS = "accents"
#: Le nom de famille seul, **et seulement quand il ne rend qu'un candidat**.
#: C'est le niveau que le brief appelait « recherche via les endpoints Players »,
#: et il existe pour un cas mesure : « Coco Gauff » rend une liste **vide**, la
#: source l'ecrivant « Cori Gauff ». Aucun repli de casse ou d'accent ne
#: rattrape un prenom different ; « Gauff » seul rend exactement un candidat.
#: La condition d'unicite est ce qui le rend sur — « Fernandez » en rend 94.
#: Tous les mots de notre nom figurent dans le candidat, et **un seul** candidat
#: restant le satisfait. C'est le niveau qui rattrape « Leylah Fernandez » contre
#: « Leylah Annie Fernandez » — le fournisseur porte les deux, et c'est le second
#: qui sert les 452 matchs.
#:
#: Le precedent est dans le depot : le nom d'un entraineur se compare **en
#: suffixe**, « J. Machado Sacramento » contre « João Pedro Machado Sacramento »,
#: parce qu'exiger la meme longueur y inventerait une divergence.
MOTS = "mots"
#: Le nom de famille seul, **et seulement quand il ne rend qu'un candidat**.
#: C'est le niveau que le brief appelait « recherche via les endpoints Players »,
#: et il existe pour un cas mesure : « Coco Gauff » rend une liste **vide**, la
#: source l'ecrivant « Cori Gauff ». Aucun repli de casse ou d'accent ne
#: rattrape un prenom different ; « Gauff » seul rend exactement un candidat.
#: La condition d'unicite est ce qui le rend sur — « Fernandez » en rend 94.
NOM = "nom"
INTROUVABLE = "introuvable"
FALLBACKS = (EXACT, CASSE, ACCENTS, MOTS, NOM, INTROUVABLE)


@dataclass(frozen=True)
class Identity:
    """Ce qu'on sait du nom d'un joueur chez le fournisseur."""

    local_name: str
    tour: str
    canonical: str = ""
    provider_id: int | None = None
    fallback: str = INTROUVABLE
    response_id: int | None = None

    @property
    def resolved(self) -> bool:
        return bool(self.canonical)


def _fold_case(text: str) -> str:
    return str(text or "").strip().casefold()


def _fold_accents(text: str) -> str:
    """Casse, accents **et typographie** replies : le niveau le plus tolerant.

    Le tiret en fait partie, et c'est **mesure** : la source ecrit
    `Pablo Carreno-Busta` quand la base ecrit `Pablo Carreno Busta`, et
    `Felix Auger Aliassime` quand la base ecrit `Felix Auger-Aliassime` — dans
    les deux sens, donc aucune convention ne se devine. Le profil de Carreno
    porte 1 028 matchs : sans ce repli il restait introuvable.

    C'est exactement ce que fait deja `picks_import._fold` pour apparier une
    affiche a un en-tete — « absorbe la typographie : casse, accents, tirets,
    espaces, et rien d'autre » — et la raison est la meme des deux cotes.
    """
    replie = sort_key(str(text or "").strip())
    for signe in "-–—'’.":
        replie = replie.replace(signe, " ")
    return " ".join(replie.split())


def rank_candidates(name: str, candidates: list[str]) -> list[tuple[str, str]]:
    """Les candidats plausibles, du plus sur au moins sur, avec leur niveau.

    **Rend une liste et non un choix, et c'est une correction mesuree.** Le
    premier jet tranchait ici meme, en prenant la correspondance exacte : sur
    « Leylah Fernandez » il choisissait `Leylah Fernandez`, qui existe chez le
    fournisseur et dont le profil porte **zero match**. Le vrai profil s'appelle
    `Leylah Annie Fernandez` et en porte 452, et la recherche rend **les deux**.

    Un nom n'est donc pas une resolution : ce qui tranche est le profil qui sert
    des donnees, et cette fonction ne fait qu'ordonner les essais. Meme regle que
    partout ici — quand un identifiant existe, c'est lui, et un libelle qui ne
    designe rien n'en est pas un.

    Un niveau qui rend **plusieurs** candidats indiscernables n'en propose aucun :
    il n'existe ici aucune resolution manuelle pour departager, et attribuer a un
    joueur les statistiques d'un autre serait pire qu'une ligne absente.
    """
    propres = [str(c).strip() for c in candidates if str(c).strip()]
    ordonnes: list[tuple[str, str]] = []
    vus: set[str] = set()
    for niveau, fold in ((EXACT, str.strip), (CASSE, _fold_case), (ACCENTS, _fold_accents)):
        cible = fold(str(name))
        trouves = [candidat for candidat in propres if fold(candidat) == cible]
        if len(trouves) != 1:
            # Zero : ce niveau ne dit rien. Plusieurs : il ne departage pas, et
            # les niveaux suivants sont **plus** tolerants — ils en trouveraient
            # au moins autant. Dans les deux cas on passe au suivant.
            continue
        if trouves[0] not in vus:
            ordonnes.append((trouves[0], niveau))
            vus.add(trouves[0])

    # **Dernier niveau, et il ne se declenche que sur ce qui reste.** Tous les
    # mots de notre nom figurent dans le candidat, et un seul candidat non
    # encore propose le satisfait.
    #
    # Limite connue et nommee plutot que tue : sur « Alexander Zverev », le
    # candidat restant serait « Alexander Zverev Sr », c'est-a-dire le pere. Le
    # niveau ne s'atteint que si le profil exact s'est revele **vide**, ce qui
    # n'arrive pas la — 975 matchs — mais la parade n'est pas dans la regle de
    # nom : elle est dans la validation par le contenu, qui est le seul juge.
    mots = {_fold_accents(mot) for mot in str(name).split() if mot}
    restants = [
        candidat
        for candidat in propres
        if candidat not in vus and mots <= {_fold_accents(mot) for mot in candidat.split() if mot}
    ]
    if len(restants) == 1:
        ordonnes.append((restants[0], MOTS))
    return ordonnes


def surname(name: str) -> str:
    """Le dernier mot d'un nom. Vide s'il n'y en a qu'un.

    Sert au seul repli `NOM`, et rend vide sur un nom d'un seul mot : chercher
    « Gauff » quand on cherchait deja « Gauff » ne serait qu'un appel de plus
    pour la meme reponse.
    """
    mots = str(name or "").split()
    return mots[-1] if len(mots) > 1 else ""


def load_identity(local_name: str, tour: str, settings: Settings | None = None) -> Identity | None:
    """L'identite deja resolue, ou None si la question n'a jamais ete posee.

    **`None` et « non resolue » sont deux etats distincts**, et les confondre
    ferait redemander tous les jours un nom que la source ne connait pas — un
    appel par joueur et par passe, pour un constat qui ne bougera pas. Une ligne
    presente avec `canonical` vide dit « cherche, pas trouve ».
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT local_name, tour, canonical, provider_id, fallback, response_id "
            "  FROM player_alias WHERE local_name = ? AND tour = ?",
            (local_name, tour),
        ).fetchone()
    if row is None:
        return None
    return Identity(
        local_name=str(row["local_name"]),
        tour=str(row["tour"]),
        canonical=str(row["canonical"] or ""),
        provider_id=row["provider_id"],
        fallback=str(row["fallback"]),
        response_id=row["response_id"],
    )


def store_identity(identity: Identity, settings: Settings | None = None) -> None:
    """Memorise une resolution. **Une fois par joueur, puis cache.**"""
    settings = settings or get_settings()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO player_alias (local_name, tour, canonical, provider_id, "
            "  fallback, response_id, resolved_at) VALUES (?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(local_name, tour) DO UPDATE SET "
            "  canonical = excluded.canonical, provider_id = excluded.provider_id, "
            "  fallback = excluded.fallback, response_id = excluded.response_id, "
            "  resolved_at = excluded.resolved_at",
            (
                identity.local_name,
                identity.tour,
                identity.canonical or None,
                identity.provider_id,
                identity.fallback,
                identity.response_id,
                utcnow(),
            ),
        )


def note_provider_id(
    local_name: str, tour: str, provider_id: int, settings: Settings | None = None
) -> None:
    """Complete une resolution avec l'identifiant numerique du fournisseur.

    **C'est le vrai identifiant, et il n'arrive qu'apres.** La recherche ne rend
    que des noms ; l'identifiant apparait dans la premiere reponse
    `matches-played`. On l'ecrit des qu'il est servi — regle de revue du projet :
    quand un identifiant existe, c'est lui, et l'ecrire est ce qui permettra un
    jour de ne plus dependre d'une chaine.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE player_alias SET provider_id = ? WHERE local_name = ? AND tour = ?",
            (int(provider_id), local_name, tour),
        )


def fallback_tally(settings: Settings | None = None) -> dict[str, int]:
    """Combien de resolutions par niveau de repli.

    **C'est ce que le brief demande de journaliser, et sous la forme qui sert** :
    si `accents` devient majoritaire, la normalisation en amont est mauvaise. Un
    compte le dit ; une ligne de journal par resolution ne le dirait jamais,
    personne ne relisant un mois de logs.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT fallback, COUNT(*) AS n FROM player_alias GROUP BY fallback"
        ).fetchall()
    return {str(row["fallback"]): int(row["n"]) for row in rows}


async def _search(
    client: TennisAPIClient, local_name: str, tour: str
) -> tuple[list[tuple[str, str]], list[str], int | None]:
    """Les candidats ordonnes par la **recherche sur le nom**, et le brut rendu.

    Deux appels au plus, et le second est mesure plutot que suppose :

    1. le nom tel quel — la casse est prise en charge par le **serveur**,
       `mccartney kessler` trouve `Mccartney Kessler` ;
    2. si la liste est **vide** et que le nom porte des accents, le nom replie :
       `Iva Jović` rend `[]` la ou `Iva Jovic` repond. Un nom sans accent ne
       declenche jamais cet appel.
    """
    response = await client.search_raw(local_name, tour)
    bruts = [str(item) for item in response.data] if isinstance(response.data, list) else []
    ordonnes = rank_candidates(local_name, bruts)

    if not bruts and _fold_accents(local_name) != _fold_case(local_name):
        replie = _sans_accents(local_name)
        response = await client.search_raw(replie, tour)
        bruts = [str(item) for item in response.data] if isinstance(response.data, list) else []
        ordonnes = [(nom, ACCENTS) for nom, _ in rank_candidates(replie, bruts)]

    return ordonnes, bruts, response.archive_id or None


async def _by_surname(
    client: TennisAPIClient, local_name: str, tour: str
) -> tuple[list[tuple[str, str]], list[str], int | None]:
    """Le dernier recours : le **nom de famille seul**.

    Il existe pour deux cas mesures, et il ne part qu'apres que tous les
    candidats du nom complet ont echoue **a la validation** :

    - « Coco Gauff » rend une liste vide, la source l'ecrivant « Cori Gauff » :
      aucun repli de typographie ne rattrape un prenom different ;
    - « Leylah Fernandez » rend exactement un candidat, `Leylah Fernandez`, dont
      le profil porte **zero match**. La recherche n'etait donc pas vide, et une
      garde posee sur « recherche vide » ne partait jamais — ce qu'on cherche
      est un profil qui **sert**, pas une reponse non vide.

    Deux conditions d'acceptation, dans cet ordre : un candidat unique tranche ;
    sinon le seul candidat qui **contient tous nos mots**. « Fernandez » en rend
    quatre-vingt-quatorze, dont un seul porte aussi « Leylah ».
    """
    nom_seul = surname(local_name)
    if not nom_seul:
        return [], [], None
    response = await client.search_raw(nom_seul, tour)
    larges = [str(item) for item in response.data] if isinstance(response.data, list) else []
    if len(larges) == 1:
        return [(larges[0], NOM)], larges, response.archive_id or None
    mots = {mot for mot in _fold_accents(local_name).split() if mot}
    tenus = [
        candidat
        for candidat in larges
        if mots <= {mot for mot in _fold_accents(candidat).split() if mot}
    ]
    ordonnes = [(tenus[0], NOM)] if len(tenus) == 1 else []
    return ordonnes, larges, response.archive_id or None


async def _validate(
    client: TennisAPIClient,
    local_name: str,
    tour: str,
    ordonnes: list[tuple[str, str]],
    archive_id: int | None,
    settings: Settings,
) -> tuple[Identity, Any] | None:
    """Essaie les candidats dans l'ordre, et retient **le premier qui sert**."""
    for candidat, niveau in ordonnes:
        reponse = await client.matches_played(candidat)
        charge = reponse.data if isinstance(reponse.data, dict) else {}
        if not (charge.get("singles") or []):
            logger.info(
                "tennisapi : %r resolu en %r, profil vide — candidat suivant",
                local_name,
                candidat,
            )
            continue
        identity = Identity(
            local_name=local_name,
            tour=tour,
            canonical=candidat,
            provider_id=_provider_id(charge, candidat),
            fallback=niveau,
            response_id=archive_id,
        )
        store_identity(identity, settings)
        return identity, reponse
    return None


async def resolve(
    client: TennisAPIClient,
    local_name: str,
    tour: str,
    settings: Settings | None = None,
) -> tuple[Identity, Any, Reject | None]:
    """Resout la graphie d'un joueur, **et la valide sur le contenu**.

    Rend l'identite, la charge utile `matches-played` qui l'a validee, et le
    rejet a journaliser quand rien n'aboutit.

    **Un nom n'est pas une resolution, et c'est mesure.** Le premier jet
    s'arretait a la correspondance exacte : sur « Leylah Fernandez » il retenait
    `Leylah Fernandez`, qui existe bien chez le fournisseur et dont le profil
    porte **zero match**. Le vrai profil est `Leylah Annie Fernandez`, 452
    matchs, et la recherche rend **les deux**. Une correspondance exacte qui ne
    sert rien n'est pas une resolution — regle de revue du projet : l'identifiant
    est celui qui designe quelque chose.

    **La validation ne coute aucun appel de plus** dans le cas ordinaire : la
    reponse `matches-played` est celle qu'on allait demander de toute facon, et
    elle est rendue a l'appelant pour qu'il ne la repaie pas.
    """
    settings = settings or get_settings()
    connue = load_identity(local_name, tour, settings)
    if connue is not None:
        # **« Cherche, pas trouve » et « jamais cherche » sont deux etats**, et
        # les confondre ferait redemander tous les jours un nom que la source ne
        # connait pas. La charge utile n'est pas rendue : elle n'a pas ete
        # relue, et l'appelant la demandera s'il en a besoin.
        return connue, None, None

    ordonnes, bruts, archive_id = await _search(client, local_name, tour)
    trouve = await _validate(client, local_name, tour, ordonnes, archive_id, settings)

    if trouve is None:
        # **Le repli par nom de famille ne part qu'ici**, quand tous les
        # candidats du nom complet ont echoue *a la validation* et non a la
        # recherche. C'est la correction que « Leylah Fernandez » a imposee.
        larges, bruts_nom, archive_nom = await _by_surname(client, local_name, tour)
        bruts = bruts or bruts_nom
        trouve = await _validate(
            client, local_name, tour, larges, archive_nom or archive_id, settings
        )

    if trouve is not None:
        identity, reponse = trouve
        return identity, reponse, None

    identity = Identity(
        local_name=local_name, tour=tour, fallback=INTROUVABLE, response_id=archive_id
    )
    store_identity(identity, settings)
    essayes = ", ".join(nom for nom, _ in ordonnes)
    return (
        identity,
        None,
        Reject(
            block_type=SOURCE,
            reason=MATCH_REF_UNRESOLVED,
            detail=(
                f"{local_name} ({tour}) : aucun profil tennis-api.com ne sert de matchs. "
                f"Candidats rendus : {', '.join(bruts) if bruts else 'aucun'}"
                + (f" ; essayes sans resultat : {essayes}" if essayes else "")
                + ". Rien n'est devine — les statistiques d'un autre joueur seraient "
                "pires qu'une ligne absente."
            ),
            payload=f"{local_name}/{tour} -> {bruts!r}"[:400],
        ),
    )


def _provider_id(charge: dict[str, Any], canonical: str) -> int | None:
    """L'identifiant numerique du joueur, lu dans le premier match qui le nomme.

    **C'est le vrai identifiant** : la recherche ne rend que des noms, et il
    n'apparait que dans une reponse `matches-played`. On l'ecrit des qu'il est
    servi — regle de revue du projet, avant d'ecrire une comparaison de chaines,
    chercher l'identifiant.
    """
    cible = _fold_accents(canonical)
    for match in charge.get("singles") or []:
        for cle in ("player1", "player2"):
            joueur = (match or {}).get(cle) or {}
            if _fold_accents(str(joueur.get("name") or "")) == cible:
                try:
                    return int(joueur.get("id"))
                except (TypeError, ValueError):
                    return None
    return None


def _sans_accents(text: str) -> str:
    """Le nom sans ses accents, **casse conservee**.

    `sort_key` replie les deux d'un coup, ce qui convient a une comparaison et
    pas a une requete : on veut envoyer « Karolina Muchova » et non « karolina
    muchova ». La casse n'est pourtant pas le probleme — le serveur s'en
    charge — mais envoyer un nom deforme sans raison rendrait la sonde
    irreproductible.
    """
    decompose = unicodedata.normalize("NFKD", str(text or ""))
    return "".join(char for char in decompose if not unicodedata.combining(char))


# -- La timeline, et les trois formes du silence -----------------------------
#
# **`"success": true` sur un `result` vide.** C'est le defaut caracteristique du
# projet, dans la source candidate cette fois : le vide y a la meme sortie que la
# donnee. Aucun appelant ne doit le lire comme une absence de fait.
#
# Trois choses ont ete mesurees le 17/08/2026, et chacune impose un essai :
#
# - **l'endpoint est positionnel** — `event/get/{j1}/{j2}/{date}` — et l'ordre ne
#   correspond pas toujours a celui de la base ;
# - **la date du fournisseur peut differer d'un jour** : le Fernandez – Wang
#   programme chez nous le 16/08 a 19h10 UTC est date du **17** par la source.
#   D'ou la fenetre de +/-1 jour, et non une date exacte ;
# - **la couverture reste partielle malgre tout** : sur huit rencontres de la
#   veille, graphies canoniques resolues et fenetre epuisee, **cinq repondent et
#   trois restent vides**. C'est un fait sur la source, pas un defaut de notre
#   collecte, et c'est `SOURCE_VIDE` qui le dit.

#: Les decalages tentes, dans l'ordre. Le jour annonce d'abord — **106 des 113
#: rencontres qui aboutissent repondent des le premier essai**, donc l'ordinaire
#: ne paie qu'un appel.
#:
#: **`J+1` a ete retire, et ce n'est pas un arbitrage.** Mesure du 18/08/2026 sur
#: les 2 767 appels `event/get` de l'archive, 564 rencontres tentees : `J+0`
#: aboutit 106 fois, `J-1` **7** fois, `J+1` **zero**. Il etait essaye sur chaque
#: rencontre en echec — deux appels a chaque fois, sur 451 rencontres — et n'a
#: jamais rien rapporte. Un essai qui n'aboutit jamais n'a pas de compromis a
#: arbitrer : il se supprime.
#:
#: `J-1` **reste**. Sept sur 113 est peu, mais ce n'est pas zero, et le
#: compromis — perdre 6 % de couverture pour deux appels de moins par echec — se
#: pose a froid, pas dans la meme minute que la suppression d'un essai mort.
DAY_SHIFTS = (0, -1)

#: Les jeux d'une timeline se lisent sur ces deux mots. Ils sont **du
#: fournisseur** et recopies tels quels : « Game 3 - Taylor Fritz - holds to 15 ».
HOLD = "holds"
BREAK = "breaks"


@dataclass(frozen=True)
class GameLine:
    """Les jeux d'un match, vus du cote d'un joueur."""

    played_on: str
    served: int = 0
    held: int = 0
    returned: int = 0
    broke: int = 0
    archive_id: int = 0
    #: La surface, **recopiee de la ligne de service qui a demande cette
    #: timeline** et jamais deduite de la reponse. Le rapprochement est connu au
    #: moment de la collecte : le redecouvrir par la date ferait dependre les
    #: agregats par surface d'un accord de dates entre deux endpoints, et cet
    #: accord n'existe pas — la timeline se trouve parfois a J-1.
    surface: str = ""

    @property
    def consistent(self) -> bool:
        return self.held <= self.served and self.broke <= self.returned


def parse_timeline(payload: Any, name: str) -> tuple[GameLine | None, str]:
    """Les jeux tenus et les breaks d'un joueur, depuis la timeline d'un match.

    Rend `(None, motif)` quand rien n'est exploitable, et le motif dit **quoi** :
    `vide` pour un `result` absent — le `"success": true` sur contenu vide de la
    source — et `alternance` pour une timeline a trous.

    **Le serveur se deduit, il n'est pas ecrit.** Une ligne nomme l'acteur du
    jeu : « Game 3 - Taylor Fritz - holds to 15 » dit que Fritz servait, « Game 1
    - Alex Michelsen - breaks to 40 » dit que Fritz servait aussi — on ne breake
    que le service adverse. Les deux camps viennent de `participant1` et
    `participant2`, servis dans la meme reponse.

    **L'invariant d'alternance est le controle, et il est plus fort qu'une
    coherence de facade.** Les jeux de service alternent par construction : si la
    suite deduite ne le fait pas, c'est que la timeline saute un jeu, et une
    moyenne calculee dessus serait fausse **sans que rien ne le montre**. Verifie
    sur les trois fixtures du lot 4 et sur le Fritz – Michelsen du 16/08 : 19
    jeux, alternance parfaite, et 6-3 6-4 fait bien 19.
    """
    result = (payload or {}).get("result") if isinstance(payload, dict) else None
    if not isinstance(result, dict) or not result:
        return None, "vide"
    timeline = result.get("timeline") or []
    if not timeline:
        return None, "vide"

    camps = [str(result.get("participant1") or ""), str(result.get("participant2") or "")]
    if not all(camps):
        return None, "vide"
    replies = [_fold_accents(camp) for camp in camps]
    cible = _fold_accents(name)
    if cible not in replies:
        return None, "vide"

    serveurs: list[str] = []
    served = held = returned = broke = 0
    for entree in timeline:
        acteur, action = _read_game(str((entree or {}).get("text") or ""))
        if not acteur:
            continue
        acteur = _fold_accents(acteur)
        if acteur not in replies:
            continue
        # Celui qui tient servait ; celui qui breake **recevait**.
        serveur = acteur if action == HOLD else _autre(acteur, replies)
        serveurs.append(serveur)
        if serveur == cible:
            served += 1
            held += action == HOLD
        else:
            returned += 1
            broke += action == BREAK and acteur == cible

    if not served and not returned:
        return None, "vide"
    if any(a == b for a, b in zip(serveurs, serveurs[1:], strict=False)):
        return None, "alternance"
    return (
        GameLine(
            played_on=_from_epoch(result.get("startTimestamp")),
            served=served,
            held=held,
            returned=returned,
            broke=broke,
        ),
        "",
    )


def _from_epoch(value: Any) -> str:
    """`1780565400` devient `2026-06-04`. **Ce champ est un entier, pas une date.**

    Il etait lu `str(value)[:10]`, ce qui rend les dix premiers chiffres de
    l'horodatage — une chaine qui ressemble a une date par sa longueur et n'en
    est pas une. Le degat etait invisible et total : `_store_player` rapproche
    les jeux de leur surface par cette date, aucun rapprochement ne tombait, et
    **les jeux n'atteignaient que l'agregat toutes surfaces**, seul cas ou le
    filtre est court-circuite. Comme `serve_lines` est appele avec la surface du
    tournoi, la ligne `Jeux` ne pouvait sortir sur **aucun** bloc — y compris
    pour les sept joueurs qui venaient d'atteindre le seuil de 300 jeux.

    Constate en rendant pour de vrai, jamais par un test : les quatre lignes de
    service sortaient, et l'absence de la cinquieme se lisait comme un manque de
    volume, qui est son comportement normal.
    """
    try:
        return datetime.fromtimestamp(int(value), UTC).date().isoformat()
    except (TypeError, ValueError, OSError, OverflowError):
        return ""


def _read_game(texte: str) -> tuple[str, str]:
    """`Game 3 - Taylor Fritz - holds to 15` devient `("Taylor Fritz", "holds")`.

    Rend `("", "")` sur une ligne dont le vocabulaire n'est pas reconnu — ce qui
    se saute plutot que de se deviner. Zero ligne non reconnue sur les trois
    fixtures du lot 4 et sur celle du 16/08.
    """
    morceaux = [morceau.strip() for morceau in texte.split(" - ")]
    if len(morceaux) < 3 or not morceaux[0].lower().startswith("game"):
        return "", ""
    mots = morceaux[2].split()
    action = mots[0].lower() if mots else ""
    if action not in (HOLD, BREAK):
        return "", ""
    return morceaux[1], action


def _autre(replie: str, camps: list[str]) -> str:
    """L'autre camp du match. La timeline ne nomme que l'acteur du jeu."""
    for camp in camps:
        if camp != replie:
            return camp
    return ""


@dataclass(frozen=True)
class TimelineHit:
    """Une timeline trouvee, et **ce qu'il a fallu pour la trouver**.

    `shift` et `swapped` ne sont pas du decor : le brief demande de journaliser
    les cas trouves au jour decale, parce que si c'est **systematique** c'est un
    decalage de fuseau a corriger en amont plutot qu'a absorber a chaque appel.
    """

    line: GameLine
    shift: int
    swapped: bool
    archive_id: int = 0


async def fetch_timeline(
    client: TennisAPIClient,
    first: str,
    second: str,
    day: str,
    subject: str,
) -> tuple[TimelineHit | None, Reject | None]:
    """La timeline d'une rencontre, en epuisant les essais avant de conclure.

    **Trois faits mesures, et chacun impose un essai** :

    - l'endpoint est **positionnel**, et l'ordre des joueurs ne correspond pas
      toujours a celui de la base ;
    - la date du fournisseur peut differer d'un jour — d'ou `DAY_SHIFTS` et non
      une date exacte. **Seule la veille est tentee** : mesure sur 564
      rencontres, `J-1` aboutit 7 fois et `J+1` jamais ;
    - la couverture reste **partielle** apres tout cela : sur huit rencontres de
      la veille, cinq repondent et trois restent vides. C'est un fait sur la
      source, et `SOURCE_VIDE` le dit au lieu de le taire.

    Le jour annonce est essaye **en premier** : cinq des huit rencontres
    mesurees repondent des le premier appel, donc le cas ordinaire ne paie qu'un
    appel sur les six possibles.
    """
    from datetime import date, timedelta

    try:
        depart = date.fromisoformat(str(day)[:10])
    except ValueError:
        depart = None

    dernier = 0
    for shift in DAY_SHIFTS:
        if depart is None and shift:
            break
        jour = str(day)[:10] if depart is None else (depart + timedelta(days=shift)).isoformat()
        for swapped, (a, b) in enumerate(((first, second), (second, first))):
            reponse = await client.event(a, b, jour)
            dernier = reponse.archive_id or dernier
            ligne, motif = parse_timeline(reponse.data, subject)
            if ligne is not None:
                if shift:
                    # Journalise, parce que c'est la frequence qui decide : un cas
                    # isole s'absorbe, un cas systematique est un decalage de
                    # fuseau a corriger en amont.
                    logger.info(
                        "tennisapi timeline : %s vs %s trouvee a J%+d (base %s)",
                        first,
                        second,
                        shift,
                        jour,
                    )
                return TimelineHit(
                    line=ligne, shift=shift, swapped=bool(swapped), archive_id=dernier
                ), None
            if motif == "alternance":
                # **Une timeline a trous n'est pas un silence**, et retenter les
                # autres ordres ne la reparerait pas : c'est la meme reponse.
                return None, Reject(
                    block_type=SOURCE,
                    reason=SCHEMA_INVALID,
                    detail=(
                        f"{first} vs {second} le {jour} : les jeux de service n'alternent "
                        "pas, la timeline saute un jeu. Une moyenne calculee dessus serait "
                        "fausse sans que rien ne le montre."
                    ),
                    payload=f"{first}/{second}/{jour}",
                )

    return None, Reject(
        block_type=SOURCE,
        reason=SOURCE_VIDE,
        detail=(
            f"{first} vs {second} le {day} : la source repond et ne sert aucune timeline, "
            f"les deux ordres et {len(DAY_SHIFTS)} dates essayes. Ce n'est pas une absence "
            "de match — c'est une couverture partielle, mesuree a trois sur huit."
        ),
        payload=f"{first}/{second}/{day}",
    )


# -- Les agregats ------------------------------------------------------------
#
# **Sommer les comptes, jamais moyenner les pourcentages par match.** L'API sert
# les denominateurs precisement pour rendre ca possible ; ne pas s'en servir
# reviendrait a heriter de ses choix d'agregation sans les voir. Moyenner par
# match donnerait le meme poids a un abandon de trois jeux et a un cinq sets, et
# fausserait le profil des joueurs a abandons — ceux que la ligne « Abandons »
# du bloc signale deja.

#: Plancher de points de service sur la fenetre et la surface demandees. **En
#: points et non en matchs** : c'est le volume qui rend un taux lisible, et deux
#: matchs de cinq sets en portent plus que six abandons.
MIN_SERVE_POINTS = 400

#: Le meme plancher pour les jeux. Une tenue de service sur douze jeux est une
#: soiree, pas un profil. Trois cents jeux valent une trentaine de matchs.
MIN_GAMES = 300

WINDOW_52W = "52w"
ALL_SURFACES = ""


@dataclass(frozen=True)
class ServeAggregate:
    """Les comptes sommes d'un joueur sur une fenetre. **Aucun taux stocke.**

    Un taux se calcule a la lecture, ce qui a deux effets : deux fenetres se
    recomposent par addition, et un taux mal defini ne peut pas se figer dans
    l'historique.
    """

    player: str
    circuit: str
    surface: str = ALL_SURFACES
    window: str = WINDOW_52W
    matches: int = 0
    first_serve: int = 0
    first_serve_of: int = 0
    aces: int = 0
    double_faults: int = 0
    won_first: int = 0
    won_first_of: int = 0
    won_second: int = 0
    won_second_of: int = 0
    bp_converted: int = 0
    bp_converted_of: int = 0
    return_points: int = 0
    return_won: int = 0
    games_matches: int = 0
    served: int = 0
    held: int = 0
    returned: int = 0
    broke: int = 0
    as_of: str = ""
    response_id: int | None = None
    #: Vrai quand cet agregat est un **repli** : la surface demandee n'avait pas
    #: le volume, et ce sont toutes surfaces qui repondent. Il se rend, jamais ne
    #: se tait — un taux de dur presente comme tel alors qu'il melange trois
    #: surfaces serait une affirmation fausse.
    fell_back: bool = False

    # -- Les six indicateurs, chacun avec **son** denominateur ---------------

    @property
    def first_serve_pct(self) -> float | None:
        return _rate(self.first_serve, self.first_serve_of)

    @property
    def won_first_pct(self) -> float | None:
        return _rate(self.won_first, self.won_first_of)

    @property
    def won_second_pct(self) -> float | None:
        return _rate(self.won_second, self.won_second_of)

    @property
    def ace_pct(self) -> float | None:
        """Rapporte aux **points de service**, jamais aux matchs — sinon on
        mesure la longueur des rencontres."""
        return _rate(self.aces, self.first_serve_of)

    @property
    def double_fault_pct(self) -> float | None:
        """Rapporte aux **secondes balles** : un joueur qui rentre 75 % de
        premieres a mecaniquement moins d'occasions d'en commettre."""
        return _rate(self.double_faults, self.second_serves)

    @property
    def bp_pct(self) -> float | None:
        return _rate(self.bp_converted, self.bp_converted_of)

    @property
    def return_pct(self) -> float | None:
        return _rate(self.return_won, self.return_points)

    @property
    def hold_pct(self) -> float | None:
        return _rate(self.held, self.served)

    @property
    def break_pct(self) -> float | None:
        return _rate(self.broke, self.returned)

    @property
    def second_serves(self) -> int:
        return max(0, self.first_serve_of - self.first_serve)

    @property
    def service_points(self) -> int:
        return self.first_serve_of

    @property
    def enough(self) -> bool:
        """Assez de volume pour que les taux de service se lisent."""
        return self.first_serve_of >= MIN_SERVE_POINTS

    @property
    def enough_games(self) -> bool:
        """Assez de jeux pour que tenue et break se lisent. **Independant du
        precedent** : la timeline a sa propre couverture, partielle."""
        return self.served + self.returned >= MIN_GAMES


def _rate(numerator: int, denominator: int) -> float | None:
    """Un taux, ou None quand le denominateur est nul.

    **None et zero ne se confondent pas** : « aucune balle de break jouee » et
    « aucune convertie » sont deux faits differents, et rendre 0 % sur le premier
    decrirait un joueur qui rate tout.
    """
    return None if denominator <= 0 else numerator / denominator


def aggregate(
    lines: tuple[ServeLine, ...],
    player: str,
    circuit: str,
    surface: str = ALL_SURFACES,
    games: tuple[GameLine, ...] = (),
) -> ServeAggregate:
    """Somme des lignes de match en un agregat. **Aucune moyenne de taux.**

    Le test piege du brief est celui-ci : un abandon de trois jeux et un cinq
    sets. En sommant les comptes, le cinq sets pese ce qu'il vaut ; en moyennant
    les taux match par match, les deux pesent pareil.
    """
    retenues = [
        ligne for ligne in lines if not surface or _fold_case(ligne.surface) == _fold_case(surface)
    ]
    dates = sorted(ligne.played_on for ligne in retenues if ligne.played_on)
    return ServeAggregate(
        player=player,
        circuit=circuit,
        surface=surface,
        matches=len(retenues),
        first_serve=sum(ligne.first_serve for ligne in retenues),
        first_serve_of=sum(ligne.first_serve_of for ligne in retenues),
        aces=sum(ligne.aces for ligne in retenues),
        double_faults=sum(ligne.double_faults for ligne in retenues),
        won_first=sum(ligne.won_first for ligne in retenues),
        won_first_of=sum(ligne.won_first_of for ligne in retenues),
        won_second=sum(ligne.won_second for ligne in retenues),
        won_second_of=sum(ligne.won_second_of for ligne in retenues),
        bp_converted=sum(ligne.bp_converted for ligne in retenues),
        bp_converted_of=sum(ligne.bp_converted_of for ligne in retenues),
        return_points=sum(ligne.return_points for ligne in retenues),
        return_won=sum(ligne.return_points_won for ligne in retenues),
        games_matches=len(games),
        served=sum(jeu.served for jeu in games),
        held=sum(jeu.held for jeu in games),
        returned=sum(jeu.returned for jeu in games),
        broke=sum(jeu.broke for jeu in games),
        as_of=dates[-1] if dates else "",
        response_id=next((ligne.archive_id for ligne in retenues if ligne.archive_id), None),
    )


_AGG_COLUMNS = (
    "player, circuit, surface, window, matches, first_serve, first_serve_of, aces, "
    "double_faults, won_first, won_first_of, won_second, won_second_of, bp_converted, "
    "bp_converted_of, return_points, return_won, games_matches, served, held, returned, "
    "broke, as_of, response_id"
)


def store_aggregate(agg: ServeAggregate, settings: Settings | None = None) -> None:
    """Ecrit ou remplace un agregat. **`as_of` est toujours ecrit.**

    Sans lui, une donnee vieille de six jours se lirait comme actuelle — le
    defaut que `Fraicheur` existe pour corriger ailleurs.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        conn.execute(
            f"INSERT INTO player_serve_agg ({_AGG_COLUMNS}, computed_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(player, circuit, surface, window) DO UPDATE SET "
            "  matches = excluded.matches, first_serve = excluded.first_serve, "
            "  first_serve_of = excluded.first_serve_of, aces = excluded.aces, "
            "  double_faults = excluded.double_faults, won_first = excluded.won_first, "
            "  won_first_of = excluded.won_first_of, won_second = excluded.won_second, "
            "  won_second_of = excluded.won_second_of, bp_converted = excluded.bp_converted, "
            "  bp_converted_of = excluded.bp_converted_of, "
            "  return_points = excluded.return_points, return_won = excluded.return_won, "
            "  games_matches = excluded.games_matches, served = excluded.served, "
            "  held = excluded.held, returned = excluded.returned, broke = excluded.broke, "
            "  as_of = excluded.as_of, response_id = excluded.response_id, "
            "  computed_at = excluded.computed_at",
            (
                agg.player,
                agg.circuit,
                agg.surface,
                agg.window,
                agg.matches,
                agg.first_serve,
                agg.first_serve_of,
                agg.aces,
                agg.double_faults,
                agg.won_first,
                agg.won_first_of,
                agg.won_second,
                agg.won_second_of,
                agg.bp_converted,
                agg.bp_converted_of,
                agg.return_points,
                agg.return_won,
                agg.games_matches,
                agg.served,
                agg.held,
                agg.returned,
                agg.broke,
                agg.as_of,
                agg.response_id,
                utcnow(),
            ),
        )


def _agg_row(row: Any, fell_back: bool = False) -> ServeAggregate:
    return ServeAggregate(
        player=str(row["player"]),
        circuit=str(row["circuit"]),
        surface=str(row["surface"] or ""),
        window=str(row["window"]),
        matches=int(row["matches"]),
        first_serve=int(row["first_serve"]),
        first_serve_of=int(row["first_serve_of"]),
        aces=int(row["aces"]),
        double_faults=int(row["double_faults"]),
        won_first=int(row["won_first"]),
        won_first_of=int(row["won_first_of"]),
        won_second=int(row["won_second"]),
        won_second_of=int(row["won_second_of"]),
        bp_converted=int(row["bp_converted"]),
        bp_converted_of=int(row["bp_converted_of"]),
        return_points=int(row["return_points"]),
        return_won=int(row["return_won"]),
        games_matches=int(row["games_matches"]),
        served=int(row["served"]),
        held=int(row["held"]),
        returned=int(row["returned"]),
        broke=int(row["broke"]),
        as_of=str(row["as_of"] or ""),
        response_id=row["response_id"],
        fell_back=fell_back,
    )


def load_aggregate(
    player: str,
    circuit: str,
    surface: str = ALL_SURFACES,
    settings: Settings | None = None,
) -> ServeAggregate | None:
    """L'agregat a rendre, **avec son repli quand il y en a un**.

    Trois etats, et le troisieme est celui qui compte :

    1. la surface demandee porte au moins `MIN_SERVE_POINTS` points — on la rend ;
    2. elle ne les porte pas, mais toutes surfaces les portent — on rend le repli
       **en le disant** (`fell_back`). Un taux de dur presente comme tel alors
       qu'il melange trois surfaces serait une affirmation fausse ;
    3. ni l'une ni l'autre — **on ne rend rien**. Une ligne partielle sous le
       seuil serait lue comme un fait, et c'est exactement ce que le lot 3 a
       refuse au Match Charting Project.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        demande = conn.execute(
            f"SELECT {_AGG_COLUMNS} FROM player_serve_agg "
            " WHERE player = ? AND circuit = ? AND surface = ? AND window = ?",
            (player, circuit, surface, WINDOW_52W),
        ).fetchone()
        if demande is not None and int(demande["first_serve_of"]) >= MIN_SERVE_POINTS:
            return _agg_row(demande)
        if not surface:
            # Deja toutes surfaces : il n'y a pas de repli plus large.
            return None
        large = conn.execute(
            f"SELECT {_AGG_COLUMNS} FROM player_serve_agg "
            " WHERE player = ? AND circuit = ? AND surface = '' AND window = ?",
            (player, circuit, WINDOW_52W),
        ).fetchone()
    if large is None or int(large["first_serve_of"]) < MIN_SERVE_POINTS:
        return None
    return _agg_row(large, fell_back=True)


# -- Le rendu dans le bloc ---------------------------------------------------
#
# **Quatre lignes, et elles prolongent `Profil` plutot que de le doubler.**
# `Profil` decrit la **forme** d'un match — mediane de jeux, tie-breaks, sets
# secs ; celles-ci decrivent **comment un point se gagne**. Les deux se lisent
# ensemble, et c'est ce que dit le gabarit.
#
# Elles vivent derriere `SERVE_LINES_ENABLED`, **defaut a faux**. Le budget de
# recherche et ces lignes modifient tous deux ce que le modele produit ; livres
# le meme jour, leurs effets seraient indissociables et le `changelog_mesure` ne
# servirait a rien.

#: La surface se dit en deux vocabulaires : `competitions.surface` porte
#: `hard`/`clay`/`grass`, la source `Hard`/`Clay`/`Grass`. Le rapprochement se
#: fait ici, une fois — le recopier ailleurs le ferait diverger.
SURFACE_TO_SOURCE = {"hard": "Hard", "clay": "Clay", "grass": "Grass", "carpet": "Carpet"}

#: Ce qu'une ligne ecrit quand un joueur n'atteint pas le seuil. **Jamais une
#: ligne muette** : le bloc porte l'autre joueur, et une moitie absente sans
#: mention se lirait comme un oubli de collecte.
UNAVAILABLE = "non disponible"

#: Nombre de decimales des taux rendus. Un pourcentage a deux decimales suggere
#: une precision que 400 points ne portent pas.
_PCT = 1


def circuit_of(oddsapi_key: str) -> str:
    """`atp` ou `wta`, lu dans la cle de competition. Vide si ni l'un ni l'autre.

    Le circuit se lit **dans la cle** et non dans un libelle, meme regle que
    partout : `tennis_atp_cincinnati_open` le porte, « Cincinnati Open » non —
    et les epreuves masculine et feminine y ont des noms differents.
    """
    cle = str(oddsapi_key or "").lower()
    for tour in ("atp", "wta"):
        if f"_{tour}_" in cle or cle.startswith(f"tennis_{tour}"):
            return tour
    return ""


def _pct(value: float | None) -> str:
    return "" if value is None else f"{100 * value:.{_PCT}f}%"


def _serve_fragment(player: str, agg: ServeAggregate | None) -> str:
    """« Taylor Fritz 64.8% 1re · 78.9% s/1re · 54.1% s/2e · 8.1% aces · 4.2% df »."""
    if agg is None:
        return f"{player} {UNAVAILABLE}"
    morceaux = [
        f"{_pct(agg.first_serve_pct)} 1re",
        f"{_pct(agg.won_first_pct)} s/1re",
        f"{_pct(agg.won_second_pct)} s/2e",
        f"{_pct(agg.ace_pct)} aces",
        f"{_pct(agg.double_fault_pct)} df",
    ]
    return f"{player} " + " · ".join(m for m in morceaux if not m.startswith(" "))


def _return_fragment(player: str, agg: ServeAggregate | None) -> str:
    if agg is None:
        return f"{player} {UNAVAILABLE}"
    bp = _pct(agg.bp_pct)
    detail = f" · {bp} BP converties" if bp else ""
    return f"{player} {_pct(agg.return_pct)} pts{detail} ({agg.return_points} pts recus)"


def _games_fragment_serve(player: str, agg: ServeAggregate | None) -> str:
    if agg is None or not agg.enough_games:
        return f"{player} {UNAVAILABLE}"
    return (
        f"{player} tenue {_pct(agg.hold_pct)} · break {_pct(agg.break_pct)} "
        f"({agg.served} jeux servis)"
    )


def _scope_fragment(
    home_agg: ServeAggregate | None, away_agg: ServeAggregate | None, surface: str
) -> str:
    """La ligne de portee : surface, fenetre, denominateurs, **et `as_of`**.

    L'`as_of` est **obligatoire**. Sans lui, une donnee vieille de six jours se
    lirait comme actuelle — le defaut que `Fraicheur` existe pour corriger
    ailleurs. Et le repli se dit : un taux de dur qui melange trois surfaces
    sans le signaler serait une affirmation fausse.
    """
    connus = [agg for agg in (home_agg, away_agg) if agg is not None]
    if not connus:
        return ""
    replie = any(agg.fell_back for agg in connus)
    portee = "toutes surfaces" if replie or not surface else surface
    points = " et ".join(str(agg.service_points) for agg in connus)
    dates = sorted(agg.as_of for agg in connus if agg.as_of)
    arret = f", arretees au {_short_day(dates[-1])}" if dates else ""
    mention = " — surface repliee" if replie and surface else ""
    return f"({portee}, 52 sem., {points} pts de service{arret}){mention} [tennis-api.com]"


def _short_day(iso: str) -> str:
    parts = str(iso)[:10].split("-")
    return f"{parts[2]}/{parts[1]}" if len(parts) == 3 else str(iso)[:10]


#: Les grandeurs que la ligne `Ecart` confronte, **dans cet ordre**, avec leurs
#: comptes et le sens de l'avantage.
#:
#: `moins_vaut_mieux` porte le seul piege de la ligne : sur les doubles fautes,
#: l'avantage est au **plus bas taux**. Un booleen le dit une fois ; l'oublier
#: designerait le mauvais joueur, et un ecart lu a l'envers est l'erreur la plus
#: couteuse que ce bloc puisse produire.
GAP_MEASURES: tuple[tuple[str, str, bool], ...] = (
    ("s/1re", "won_first", False),
    ("df", "double_faults", True),
    ("retour", "retour", False),
)

#: Comment lire les comptes d'une grandeur sur un agregat. Ecrit ici plutot qu'en
#: propriete : la ligne a besoin du **numerateur et du denominateur**, la ou les
#: proprietes de `ServeAggregate` ne rendent que le taux.
_GAP_COUNTS = {
    "won_first": lambda agg: (agg.won_first, agg.won_first_of),
    "double_faults": lambda agg: (agg.double_faults, agg.second_serves),
    "retour": lambda agg: (agg.return_won, agg.return_points),
}


def _gap_fragment(
    home: str,
    away: str,
    home_agg: ServeAggregate | None,
    away_agg: ServeAggregate | None,
) -> str:
    """L'ecart, **calcule par l'application** : une soustraction deterministe.

    Ce que l'application peut trancher ne se delegue pas au modele — meme regle
    que le cran de confiance et le comptage de la section C.

    **Elle nomme un desequilibre, elle ne predit rien.** La mention « taux non
    ajustes du niveau d'adversaire » n'est pas une precaution de style : un
    pourcentage obtenu contre des qualifies ne vaut pas le meme contre le haut
    du tableau, et c'est `Niveau adv.` qui le dit deux lignes plus haut.

    ## Le taux de premieres balles disait qui sert, pas qui en tire quelque chose

    La ligne confrontait `first_serve_pct` — la part de premieres balles **mises
    en jeu**. Sur le bloc du 20/08 elle rendait `service +0.1 pts sur la 1re
    balle pour Sara Bejlek`, les deux joueuses etant a 63,2 %, pendant que la
    ligne `Service` juste au-dessus portait **6,1 points** d'ecart sur les points
    gagnes derriere la premiere et **7,5 points** sur les doubles fautes.

    La grandeur confrontee est donc celle de l'**efficacite** et non celle du
    style : un joueur qui rentre 76 % de premieres n'en tire pas forcement plus
    qu'un joueur a 55 %, et c'est cette seconde question que la ligne pose. Le
    taux de mise en jeu reste rendu par `Service`, ou il decrit.

    **Contre-mesure a connaitre : ce n'est pas la grandeur la moins dispersee.**
    Sur les 174 paires de joueurs des blocs soumis, l'ecart absolu median vaut
    **4,3 points** sur la mise en jeu contre **3,5** sur les points gagnes. Le
    brief l'annoncait « la moins discriminante » ; elle est la **plus** dispersee
    des cinq. Ce qui la disqualifie est ce qu'elle mesure, pas son etalement.

    ## Un ecart doit sortir du bruit de sa propre mesure

    `+0.1 pts` sur 1 400 points de service n'est pas un petit avantage : c'est
    **rien**, et le nommer en tete de ligne est une affirmation que la donnee ne
    porte pas — meme regle que `HANDICAP_ALERT_MARGIN`, ou l'on se tait quand
    l'ecart tombe sous le bruit.

    Le seuil ne s'invente pas : il se lit sur les **denominateurs de la ligne
    elle-meme**, par l'intervalle de Newcombe deja ecrit pour la difference de
    deux proportions. Un ecart n'est nomme que si son intervalle **exclut zero**.
    Sur le bloc du 20/08, il rend exactement ce qu'il faut :

    | Grandeur | Ecart | Intervalle | Nommee |
    | --- | ---: | --- | --- |
    | 1re balle en jeu | +0,1 | `[-3,5 ; +3,6]` | non |
    | points s/1re | -6,1 | `[-10,4 ; -1,7]` | **oui** |
    | doubles fautes | -7,5 | `[-11,8 ; -3,2]` | **oui** |
    | retour | +5,3 | `[+1,7 ; +8,9]` | **oui** |

    Taux de declenchement sur les 174 paires : **49 %** pour les points gagnes,
    **49 %** pour les doubles fautes, **36 %** pour le retour. Aucune ligne quand
    rien ne passe — une ligne qui nomme du bruit vaut moins qu'une ligne absente.
    """
    if home_agg is None or away_agg is None:
        return ""
    from .inference import difference_interval

    lignes = []
    for etiquette, cle, moins_vaut_mieux in GAP_MEASURES:
        gauche, total_gauche = _GAP_COUNTS[cle](home_agg)
        droite, total_droite = _GAP_COUNTS[cle](away_agg)
        if not total_gauche or not total_droite:
            continue
        intervalle = difference_interval(gauche, total_gauche, droite, total_droite)
        if intervalle is None or not (intervalle[0] > 0 or intervalle[1] < 0):
            continue
        ecart = 100 * (gauche / total_gauche - droite / total_droite)
        # **Sur les doubles fautes, l'avantage est au plus bas.** Le `!=` porte
        # l'inversion une seule fois : deux ecritures de cette regle auraient
        # diverge, et la ligne aurait designe le mauvais joueur.
        lignes.append(
            f"{etiquette} {abs(ecart):+.{_PCT}f} pts pour "
            f"{home if (ecart >= 0) != moins_vaut_mieux else away}"
        )
    if not lignes:
        return ""
    lignes[-1] += " · taux non ajustes du niveau d'adversaire"
    return " | ".join(lignes)


def serve_lines(
    home: str,
    away: str,
    circuit: str,
    surface: str | None,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Les quatre lignes de service d'un bloc tennis. Vide si le drapeau est bas.

    **Aucune ligne quand les deux joueurs manquent** : un bloc qui annoncerait
    « non disponible » des deux cotes couterait quatre lignes pour ne rien dire.
    Une seule moitie manquante, en revanche, se dit — le bloc porte l'autre, et
    un silence se lirait comme un oubli de collecte.
    """
    settings = settings or get_settings()
    if not settings.serve_lines_enabled or not circuit:
        return []

    cible = SURFACE_TO_SOURCE.get(str(surface or "").lower(), "")
    home_agg = _for_player(home, circuit, cible, settings)
    away_agg = _for_player(away, circuit, cible, settings)
    if home_agg is None and away_agg is None:
        return []
    # **Les jeux ne se comptent pas sur la meme portee que les points**, et c'est
    # structurel : `collect_games` s'arrete a `MIN_GAMES` jeux **toutes surfaces
    # confondues**, donc aucun agregat par surface ne peut atteindre ce seuil —
    # mesure sur la base servie, **zero ligne par surface au-dessus de 300**, et
    # le maximum observe est de 225. La ligne `Jeux` etait donc inatteignable sur
    # tout bloc portant une surface, c'est-a-dire sur tous.
    #
    # Le choix n'est pas entre une portee et une autre, il est entre une ligne
    # repliee **qui se declare** et pas de ligne du tout. Le module a deja ce
    # troisieme etat pour les points de service (`fell_back`), avec la meme
    # raison : un repli tu serait une affirmation fausse, un repli dit est une
    # information de plus.
    home_games = _games_source(home, circuit, cible, home_agg, settings)
    away_games = _games_source(away, circuit, cible, away_agg, settings)

    portee = _scope_fragment(home_agg, away_agg, cible)
    service = _pair_lines(_serve_fragment(home, home_agg), _serve_fragment(away, away_agg), portee)
    rendu = [
        ("Service", service),
        (
            "Retour",
            _pair_lines(_return_fragment(home, home_agg), _return_fragment(away, away_agg)),
        ),
    ]
    # **`Jeux` s'omet quand aucun des deux camps n'a le volume**, et c'est la
    # regle du bloc depuis toujours : une ligne sans donnee est omise, jamais
    # rendue vide. La timeline a sa propre couverture — partielle, trois
    # rencontres sur huit muettes — donc ce cas est frequent alors que `Service`
    # est servi. Deux « non disponible » cote a cote couteraient une ligne pour
    # ne rien dire ; une seule moitie manquante, elle, se dit.
    if (home_games and home_games.enough_games) or (away_games and away_games.enough_games):
        lignes_jeux = _pair_lines(
            _games_fragment_serve(home, home_games), _games_fragment_serve(away, away_games)
        )
        # **Le repli se dit, et une seule fois pour la ligne.** Le rappeler par
        # joueur repeterait la meme phrase sur deux lignes voisines ; le taire
        # ferait lire une tenue de service de dur sur un chiffre qui melange
        # trois surfaces.
        replies = [agg for agg in (home_games, away_games) if agg is not None and not agg.surface]
        if cible and replies:
            # **La date est sur cette ligne et pas seulement sur celle de
            # `Service`.** Les deux ne sortent plus forcement du meme agregat :
            # celle du dessus date le releve de la surface, celle-ci date le
            # releve toutes surfaces, et ils peuvent differer d'un match.
            dates = sorted(agg.as_of for agg in replies if agg.as_of)
            arret = f", arretees au {_short_day(dates[-1])}" if dates else ""
            lignes_jeux += (
                f"\n(toutes surfaces{arret} — le seuil de jeux ne s'atteint pas par surface)"
            )
        rendu.append(("Jeux", lignes_jeux))
    ecart = _gap_fragment(home, away, home_agg, away_agg)
    if ecart:
        rendu.append(("Ecart", ecart))
    return rendu


def _pair_lines(*fragments: str) -> str:
    """Un joueur par ligne. **Trois informations par fragment**, donc deux
    joueurs bout a bout ne se lisent plus d'un coup d'oeil — meme arbitrage que
    `Rest` au tennis, qui rend un joueur par ligne pour la meme raison."""
    return "\n".join(fragment for fragment in fragments if fragment)


def _games_source(
    player: str,
    circuit: str,
    surface: str,
    chosen: ServeAggregate | None,
    settings: Settings,
) -> ServeAggregate | None:
    """L'agregat qui porte les **jeux**, replie sur toutes surfaces s'il le faut.

    Distinct de celui des points de service, et il fallait qu'il le soit : les
    deux seuils ne portent pas sur la meme grandeur, et `load_aggregate` ne
    connait que le premier. Un agregat de dur peut porter 2 952 points de service
    — largement au-dessus de `MIN_SERVE_POINTS` — et 105 jeux, tres en dessous de
    `MIN_GAMES`.

    **Le seuil ne bouge pas**, seule la portee change : une ligne `Jeux` sur 155
    jeux serait lue comme un fait, et c'est precisement ce que ce repli ne fait
    pas — il rend 300 jeux vrais, sur une portee qu'il declare.
    """
    if chosen is not None and chosen.enough_games:
        return chosen
    if not surface:
        return chosen
    large = load_aggregate(
        (lambda identity: identity.canonical if identity and identity.resolved else player)(
            load_identity(player, circuit, settings)
        ),
        circuit,
        ALL_SURFACES,
        settings,
    )
    if large is not None and large.enough_games:
        return large
    return chosen


def _for_player(
    player: str, circuit: str, surface: str, settings: Settings
) -> ServeAggregate | None:
    """L'agregat d'un joueur, resolu par son alias local quand il en a un."""
    identity = load_identity(player, circuit, settings)
    canonical = identity.canonical if identity and identity.resolved else player
    return load_aggregate(canonical, circuit, surface, settings)


# -- La collecte -------------------------------------------------------------
#
# Deux regimes, et ils ne demandent pas la meme chose :
#
# - la **reprise** couvre tout le catalogue de joueurs une fois. Elle est bornee
#   par le plancher de quota et **reprenable** : elle saute ce qui est deja
#   archive, donc une interruption ne coute que ce qui restait ;
# - l'**entretien** ne touche que les joueurs des lots a venir. Il tourne tous
#   les jours et doit rester a quelques dizaines d'appels.
#
# Les deux passent par la meme fonction : ce qui change est la liste de joueurs
# et la peremption, jamais la logique. Deux parcours paralleles auraient diverge.

#: Age au-dela duquel un agregat se recalcule, en heures. Un joueur joue tous les
#: deux a trois jours en tournoi : au-dela de vingt-quatre heures, sa fenetre de
#: 52 semaines peut avoir bouge d'un match.
#:
#: **C'est aussi ce qui rend la reprise reprenable** : un agregat ecrit il y a
#: dix minutes n'est pas redemande, donc relancer une passe interrompue ne repaie
#: que ce qui manquait.
AGG_TTL_HOURS = 24


@dataclass
class SyncReport:
    """Ce qu'une passe a fait, et ce qu'elle a laisse."""

    players: int = 0
    resolved: int = 0
    refreshed: int = 0
    skipped: int = 0
    calls: int = 0
    rejects: list[Reject] = None  # type: ignore[assignment]
    #: Vrai quand la passe s'est arretee sur le plancher de quota plutot qu'a la
    #: fin de sa liste. **Distinct d'une passe complete**, et c'est tout le point
    #: d'un arret propre : sans lui, une reprise a moitie faite ressemblerait a
    #: une reprise finie.
    stopped: bool = False
    remaining: int | None = None
    #: Le releve de collecte, un triplet par joueur. **Mesure pendant la passe et
    #: non apres** : sur des milliers d'appels, une couverture partielle et une
    #: panne reseau ne se distinguent plus une fois la passe finie.
    timelines: list[tuple[str, str, TimelineTally]] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.rejects is None:
            self.rejects = []
        if self.timelines is None:
            self.timelines = []

    @property
    def line(self) -> str:
        etat = "arretee sur le plancher de quota" if self.stopped else "complete"
        return (
            f"statistiques de service : {self.refreshed} joueur(s) rafraichi(s), "
            f"{self.skipped} deja a jour, {len(self.rejects)} rejet(s), "
            f"{self.calls} appel(s) — passe {etat}"
        )


def _is_fresh(player: str, circuit: str, settings: Settings, now: datetime | None = None) -> bool:
    """Un agregat ecrit recemment ne se redemande pas.

    C'est ce qui rend la reprise **reprenable** : relancer une passe interrompue
    ne repaie que ce qui manquait, et non toute la liste.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT MAX(computed_at) AS quand FROM player_serve_agg "
            " WHERE player = ? AND circuit = ?",
            (player, circuit),
        ).fetchone()
    quand = _parse_moment(str(row["quand"]) if row and row["quand"] else "")
    if quand is None:
        return False
    return ((now or datetime.now(UTC)) - quand) < timedelta(hours=AGG_TTL_HOURS)


def _parse_moment(value: str) -> datetime | None:
    try:
        moment = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


async def sync(
    client: TennisAPIClient,
    players: list[tuple[str, str]],
    settings: Settings | None = None,
    now: datetime | None = None,
    with_games: bool = False,
    force: bool = False,
) -> SyncReport:
    """Rafraichit les agregats d'une liste de joueurs. **S'arrete proprement.**

    `players` porte des couples `(nom local, circuit)`. La reprise passe tout le
    catalogue, l'entretien les seuls joueurs des lots a venir : c'est la seule
    difference entre les deux regimes, et elle est dans l'appelant.

    **Le plancher est verifie avant chaque joueur, pas une fois au depart.** Un
    controle unique laisserait une reprise de 180 joueurs franchir le plancher en
    cours de route et le decouvrir a la fin — c'est-a-dire trop tard, le quota
    etant mensuel.

    `force` leve la peremption de l'agregat, et **seule la passe longue le
    pose**. La raison est que les deux fraicheurs n'en sont pas une seule : un
    agregat ecrit ce matin ne dit rien des timelines, qui sont un second etage de
    collecte et vivent bien plus longtemps. Sans ce drapeau, une reprise lancee
    le lendemain d'un entretien sautait 221 joueurs sur 250 et ne collectait
    rien — un passage complet dont la sortie est indiscernable d'un catalogue
    deja couvert.

    Ce qu'il coute est borne et mesure : un appel `matches-played` par joueur
    saute, soit ~250, contre les milliers que coutent les timelines elles-memes.
    Ce qu'il rapporte au passage n'est pas rien — la table de service est relue
    le jour meme, donc le tournoi en cours entre avec.
    """
    settings = settings or get_settings()
    report = SyncReport(players=len(players))
    horloge = now or datetime.now(UTC)

    for local, circuit in players:
        etat = budget(settings)
        report.remaining = etat.remaining
        if not etat.allowed:
            report.stopped = True
            report.rejects.append(
                Reject(
                    block_type=SOURCE,
                    reason=SOURCE_VIDE,
                    detail=etat.note,
                    payload=f"plancher/{etat.remaining}",
                )
            )
            logger.warning("%s", etat.note)
            break

        if not force and _is_fresh(local, circuit, settings, horloge):
            report.skipped += 1
            continue

        avant = _calls(settings)
        try:
            identity, reponse, rejet = await resolve(client, local, circuit, settings)
        except ProviderError as exc:
            report.rejects.append(
                Reject(
                    block_type=SOURCE,
                    reason=OTHER,
                    detail=f"{local} ({circuit}) : {exc}",
                    payload=f"{local}/{circuit}",
                )
            )
            continue
        finally:
            report.calls += _calls(settings) - avant

        if rejet is not None:
            report.rejects.append(rejet)
            continue
        if not identity.resolved:
            continue
        report.resolved += 1

        if reponse is None:
            # Identite deja en cache : la charge utile n'a pas ete relue.
            avant = _calls(settings)
            reponse = await client.matches_played(identity.canonical)
            report.calls += _calls(settings) - avant

        jeux: tuple[GameLine, ...] = ()
        if with_games:
            # **Hors de l'entretien quotidien, et c'est deliberе.** Une timeline
            # coute quatre a six appels la ou une table de service en coute un :
            # les collecter tous les jours pour tous les joueurs paierait la
            # reprise chaque matin. Le planificateur appelle donc `sync` sans ce
            # drapeau, et la reprise le pose.
            lignes_j, ecartes_j = parse_matches_played(
                reponse.data, identity.canonical, getattr(reponse, "archive_id", 0)
            )
            fenetre_j = (horloge - timedelta(weeks=52)).date().isoformat()
            avant_j = _calls(settings)
            jeux, tally, rejets_j = await collect_games(
                client,
                identity.canonical,
                tuple(item for item in lignes_j if item.played_on >= fenetre_j),
                settings,
            )
            report.calls += _calls(settings) - avant_j
            report.rejects.extend(rejets_j)
            report.timelines.append((identity.canonical, circuit, tally))
            if tally.stopped:
                report.stopped = True

        if _store_player(identity, reponse, circuit, settings, horloge, games=jeux):
            report.refreshed += 1
        if with_games and report.stopped:
            break

    logger.info("%s", report.line)
    return report


def _calls(settings: Settings) -> int:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT COUNT(*) AS n FROM api_usage WHERE provider = ?", (PROVIDER,)
        ).fetchone()
    return int(row["n"]) if row else 0


def _store_player(
    identity: Identity,
    response: Any,
    circuit: str,
    settings: Settings,
    now: datetime,
    games: tuple[GameLine, ...] = (),
) -> bool:
    """Calcule et ecrit les agregats d'un joueur, toutes surfaces puis par surface.

    **Une seule reponse alimente toutes les fenetres** : la surface est dans la
    meme charge utile (`tournament.court.name`), donc aucun appel de plus.
    """
    lignes, ecartes = parse_matches_played(
        response.data, identity.canonical, getattr(response, "archive_id", 0)
    )
    if not lignes:
        return False
    fenetre = (now - timedelta(weeks=52)).date().isoformat()
    recentes = tuple(ligne for ligne in lignes if ligne.played_on >= fenetre)
    if not recentes:
        return False
    if ecartes:
        logger.info(
            "tennisapi %s : %d match(s) sans table de service ou incoherent(s)",
            identity.canonical,
            ecartes,
        )
    # **Les jeux se filtrent par surface comme les lignes de service.** Un
    # agregat de terre battue qui sommerait les jeux de toutes surfaces
    # afficherait une tenue de service que ce joueur n'a jamais eue sur terre —
    # et rien ne le montrerait, les deux comptes vivant dans la meme ligne.
    #
    # **La surface est portee par le jeu, plus rapprochee par sa date.** Le
    # rapprochement par date etait faux de bout en bout — le champ lu n'etait pas
    # une date — et il serait reste fragile une fois repare : la timeline se
    # trouve parfois a J-1, et les deux dates ne coincideraient alors plus.
    for surface in (ALL_SURFACES, *sorted({ligne.surface for ligne in recentes if ligne.surface})):
        retenus = tuple(
            jeu for jeu in games if not surface or _fold_case(jeu.surface) == _fold_case(surface)
        )
        store_aggregate(
            aggregate(recentes, identity.canonical, circuit, surface=surface, games=retenus),
            settings,
        )
    if identity.provider_id:
        note_provider_id(identity.local_name, circuit, identity.provider_id, settings)
    # **La garde de peremption du lot 4 s'applique a cette source comme aux
    # autres.** Une source payante qui repond encore et n'avance plus est le meme
    # defaut qu'un classeur hebdomadaire fige : elle rend 200, les memes matchs,
    # indefiniment. `source_as_of` est le dernier match obtenu.
    dernier = max(ligne.played_on for ligne in recentes)
    fige = freshness.record(PROVIDER, circuit, dernier, settings)
    if fige is not None:
        record_rejects(None, [fige], settings)
    return True


def upcoming_players(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Les joueurs des matchs de tennis **a venir**, avec leur circuit.

    C'est la liste de l'entretien quotidien : un lot tennis porte trente-cinq
    joueurs en moyenne, donc la passe reste a quelques dizaines d'appels. Passer
    tout le catalogue tous les jours couterait cent-quatre-vingts appels pour
    rafraichir des joueurs qui ne jouent pas.

    **Le circuit se lit dans la cle de competition**, jamais dans un libelle.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.home, e.away, c.oddsapi_key FROM events e "
            "  JOIN competitions c ON c.id = e.competition_id "
            " WHERE c.oddsapi_key LIKE 'tennis%' AND e.commence_time >= ? "
            " ORDER BY e.commence_time",
            (utcnow(),),
        ).fetchall()

    vus: dict[tuple[str, str], None] = {}
    for row in rows:
        tour = circuit_of(str(row["oddsapi_key"] or ""))
        if not tour:
            continue
        for nom in (row["home"], row["away"]):
            if nom:
                vus.setdefault((str(nom), tour), None)
    return list(vus)


def recent_players(sessions: int = 5, settings: Settings | None = None) -> list[tuple[str, str]]:
    """Les joueurs des `sessions` derniers **lots analyses**, les plus recents d'abord.

    L'etage intermediaire de la reprise, entre les matchs a venir et le fond de
    catalogue. Il ne se deduit pas de la date d'un match : un lot est ce qui est
    **parti a l'analyse**, donc `prompt_events` — la meme table qui porte deja le
    denominateur du taux de selection. La shortlist ne conviendrait pas, elle se
    vide a mesure qu'on decoche.

    Pourquoi cet ordre a un sens ici et pas ailleurs : une passe longue
    s'interrompt, et ce qu'elle laisse derriere doit etre ce qui sert le moins.
    Un joueur revu dans un lot recent reviendra ; un joueur vu une fois en juin
    n'a aucune raison de revenir avant sa prochaine entree au board.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.home, e.away, c.oddsapi_key FROM prompt_events pe "
            "  JOIN prompts p ON p.id = pe.prompt_id "
            "  JOIN events e ON e.id = pe.event_id "
            "  JOIN competitions c ON c.id = e.competition_id "
            " WHERE c.oddsapi_key LIKE 'tennis%' "
            "   AND p.session_id IN ("
            "         SELECT session_id FROM prompts GROUP BY session_id "
            "          ORDER BY MAX(id) DESC LIMIT ?) "
            " ORDER BY p.id DESC",
            (max(0, int(sessions)),),
        ).fetchall()

    vus: dict[tuple[str, str], None] = {}
    for row in rows:
        tour = circuit_of(str(row["oddsapi_key"] or ""))
        if not tour:
            continue
        for nom in (row["home"], row["away"]):
            if nom:
                vus.setdefault((str(nom), tour), None)
    return list(vus)


def known_players(settings: Settings | None = None) -> list[tuple[str, str]]:
    """Tous les joueurs de tennis vus en base, a venir ou non.

    C'est la liste de la **reprise**, qui ne passe qu'une fois. Elle est bornee
    par le plancher de quota et reprenable : un agregat frais n'est pas
    redemande, donc relancer apres une interruption ne repaie que le reste.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.home, e.away, c.oddsapi_key FROM events e "
            "  JOIN competitions c ON c.id = e.competition_id "
            " WHERE c.oddsapi_key LIKE 'tennis%'"
        ).fetchall()

    vus: dict[tuple[str, str], None] = {}
    for row in rows:
        tour = circuit_of(str(row["oddsapi_key"] or ""))
        if not tour:
            continue
        for nom in (row["home"], row["away"]):
            if nom:
                vus.setdefault((str(nom), tour), None)
    return list(vus)


# -- La collecte des timelines ----------------------------------------------
#
# `fetch_timeline` savait chercher **une** rencontre depuis le lot 5, et rien ne
# l'appelait : `aggregate` etait toujours invoque sans `games=`, donc `served`
# valait zero sur les 176 lignes de la base et la ligne `Jeux` s'omettait
# partout. Le chainon manquant est ici, et c'est une regle de collecte — quand
# s'arreter — plutot qu'un detail de transport.


@dataclass
class TimelineTally:
    """Ce qu'une collecte de timelines a rencontre. **Quatre taux, pas un.**

    Le brief demande de mesurer pendant la passe et non apres, et chacun de ces
    compteurs repond a une question differente : `empty` decrit la **couverture
    de la source**, `alternation` sa **qualite**, `failed` notre reseau. Les
    fondre en un seul taux d'echec ferait chercher une panne la ou il n'y a
    qu'une couverture partielle — le defaut caracteristique du projet.
    """

    attempted: int = 0
    obtained: int = 0
    empty: int = 0
    alternation: int = 0
    failed: int = 0
    calls: int = 0
    replayed: int = 0
    #: Vrai quand le seuil de jeux a ete atteint, donc quand la collecte s'est
    #: arretee d'elle-meme. **Distinct d'une liste epuisee** : le premier dit
    #: que la ligne `Jeux` sortira, le second qu'elle manquera de volume.
    reached: bool = False
    #: Vrai quand le plancher de quota a interrompu la collecte.
    stopped: bool = False
    #: Rencontres ecartees sur leur **age**, sans un appel. **Compte a part et
    #: jamais fondu dans `empty`** : l'une dit que la source ne sert pas cette
    #: rencontre, l'autre qu'on ne le lui a pas demande. Les confondre ferait
    #: lire une couverture en baisse la ou il n'y a qu'un filtre qui travaille,
    #: et c'est exactement ce chiffre qui dira le jour ou la fenetre de
    #: retention de la source aura bouge.
    too_old: int = 0


def _event_paths(first: str, second: str, day: str) -> tuple[str, ...]:
    """Les chemins que `fetch_timeline` essaiera, dans son ordre.

    Ecrit **une seule fois** et derive des memes constantes : deux listes
    paralleles auraient diverge au premier essai ajoute, et la reprise aurait
    alors redemande ce qu'elle croyait deja tenir.
    """
    from datetime import date as _date
    from datetime import timedelta as _delta

    try:
        depart = _date.fromisoformat(str(day)[:10])
    except ValueError:
        depart = None
    chemins: list[str] = []
    for shift in DAY_SHIFTS:
        if depart is None and shift:
            break
        jour = str(day)[:10] if depart is None else (depart + _delta(days=shift)).isoformat()
        for a, b in ((first, second), (second, first)):
            chemins.append(f"{PREFIX}/extend/api/event/get/{a}/{b}/{jour}")
    return tuple(chemins)


def archived_timeline(
    first: str, second: str, day: str, subject: str, settings: Settings
) -> tuple[GameLine | None, bool]:
    """La timeline d'une rencontre **deja archivee**, sans un appel de plus.

    C'est ce qui rend la passe reprenable : une interruption ne redemande pas ce
    qui est en base. Rend `(ligne, vu)` — `vu` dit qu'au moins un des chemins a
    deja ete appele, donc qu'il ne sert a rien de le repayer pour s'entendre
    repondre la meme chose.

    **Un `result` vide archive compte comme vu.** Le traiter autrement ferait
    repayer a chaque reprise les rencontres que la source ne sert pas — soit la
    moitie d'entre elles, mesuree.
    """
    chemins = _event_paths(first, second, day)
    if not chemins:
        return None, False
    marques = ",".join("?" * len(chemins))
    with connect(settings) as conn:
        rows = conn.execute(
            f"SELECT raw_json FROM api_responses "
            f" WHERE provider = ? AND path IN ({marques}) ORDER BY id",
            (PROVIDER, *chemins),
        ).fetchall()
    if not rows:
        return None, False
    for row in rows:
        try:
            charge = json.loads(str(row["raw_json"]))
        except (TypeError, ValueError):
            continue
        ligne, _ = parse_timeline(charge, subject)
        if ligne is not None:
            return ligne, True
    return None, True


def _older_than(day: str, max_age: int, now: datetime) -> bool:
    """La rencontre est-elle hors de la fenetre de retention de la source ?

    **Une date illisible est tentee**, jamais ecartee. Ce filtre existe pour ne
    pas depenser d'appel la ou la mesure dit qu'on ne trouve rien ; il n'a pas
    vocation a trancher un doute, et perdre une timeline sur une date mal formee
    serait payer le filtre du mauvais cote. Le cas est de toute facon hors de
    portee : la fenetre de 52 semaines en amont compare des chaines ISO et
    refuse deja tout le reste.
    """
    if max_age <= 0:
        return False
    try:
        joue = datetime.fromisoformat(str(day)[:10]).replace(tzinfo=UTC)
    except ValueError:
        return False
    return (now - joue).days > max_age


async def collect_games(
    client: TennisAPIClient,
    canonical: str,
    lines: tuple[ServeLine, ...],
    settings: Settings,
    target: int = MIN_GAMES,
    now: datetime | None = None,
) -> tuple[tuple[GameLine, ...], TimelineTally, list[Reject]]:
    """Les jeux d'un joueur, **du plus recent au plus ancien, et pas un de plus**.

    Quatre regles, et c'est le dessin entier :

    - **on s'arrete des que le seuil est atteint.** Une quinzaine de rencontres
      porte les 300 jeux que `enough_games` reclame (served + returned, soit une
      vingtaine par match) la ou 52 semaines en comptent quarante. Ce seul choix
      divise la passe par deux ou trois, et il est juste dans les deux sens :
      au-dela du seuil, un appel de plus n'ajoute rien qu'un taux ne dise deja ;
    - **du plus recent au plus ancien**, parce qu'une interruption doit laisser
      la fenetre la plus proche du match analyse, jamais un fond de saison ;
    - **on ne demande rien au-dela de la fenetre de retention de la source**
      (`timeline_max_age_days`). Ce n'est pas une economie prudente, c'est une
      falaise mesuree : 387 rencontres tentees au-dela de 90 jours, **zero**
      timeline, quand la tranche precedente sert encore 57 %. Le filtre supprime
      69 % des tentatives **sans perdre une seule timeline** ;
    - **le plancher est verifie avant chaque rencontre**, comme dans `sync` et
      pour la meme raison : un controle unique laisserait une reprise le franchir
      en cours de route et le decouvrir trop tard, le quota etant mensuel.

    **L'ordre des trois gardes n'est pas indifferent.** L'archive passe avant le
    filtre d'age : une timeline deja payee se relit gratuitement quel que soit
    l'age de la rencontre, et l'ecarter perdrait une donnee qu'on possede pour
    economiser un appel qu'on ne ferait pas. Le filtre passe avant le plancher :
    ce qui n'est pas demande n'a pas a etre budgete.

    Ce qui n'est **pas** fait ici : aucun ajustement au niveau d'adversaire,
    aucune projection. On somme des comptes, et c'est tout.
    """
    jeux: list[GameLine] = []
    tally = TimelineTally()
    rejets: list[Reject] = []
    total = 0
    horloge = now or datetime.now(UTC)
    age_max = int(settings.timeline_max_age_days)

    for ligne in sorted(lines, key=lambda item: item.played_on, reverse=True):
        if total >= target:
            tally.reached = True
            break
        if not ligne.opponent or not ligne.played_on:
            continue

        # **L'archive d'abord.** Gratuite, et c'est elle qui rend la reprise
        # reprenable ; sans elle une passe interrompue repaierait tout.
        deja, vu = archived_timeline(
            canonical, ligne.opponent, ligne.played_on, canonical, settings
        )
        if deja is not None:
            tally.attempted += 1
            tally.obtained += 1
            tally.replayed += 1
            jeux.append(replace(deja, surface=ligne.surface, played_on=ligne.played_on))
            total += deja.served + deja.returned
            continue
        if vu:
            # Deja demande, deja vide. Le redemander couterait un appel pour la
            # meme reponse.
            tally.attempted += 1
            tally.empty += 1
            tally.replayed += 1
            continue

        if _older_than(ligne.played_on, age_max, horloge):
            # **Hors fenetre de retention : on ne demande pas.** Cette rencontre
            # n'entre pas dans `attempted` — elle n'a pas ete tentee — et le
            # parcours **continue** plutot que de s'interrompre : les lignes sont
            # triees par date, mais une seule date aberrante ferait alors perdre
            # tout le fond de liste.
            tally.too_old += 1
            continue

        etat = budget(settings)
        if not etat.allowed:
            tally.stopped = True
            logger.warning("timelines %s : %s", canonical, etat.note)
            break

        avant = _calls(settings)
        tally.attempted += 1
        try:
            hit, rejet = await fetch_timeline(
                client, canonical, ligne.opponent, ligne.played_on, canonical
            )
        except ProviderError as exc:
            tally.failed += 1
            rejets.append(
                Reject(
                    block_type=SOURCE,
                    reason=OTHER,
                    detail=f"{canonical} vs {ligne.opponent} le {ligne.played_on} : {exc}",
                    payload=f"{canonical}/{ligne.opponent}/{ligne.played_on}",
                )
            )
            continue
        finally:
            tally.calls += _calls(settings) - avant

        if hit is not None:
            tally.obtained += 1
            # **Le rapprochement se fait ici et nulle part ailleurs** : c'est ce
            # parcours qui sait quelle ligne de service a demande cette timeline.
            # La date de la source peut differer d'un jour (`DAY_SHIFTS`), donc
            # la rapprocher apres coup par la date serait faux une fois sur
            # seize — et invisible.
            jeux.append(replace(hit.line, surface=ligne.surface, played_on=ligne.played_on))
            total += hit.line.served + hit.line.returned
            continue
        if rejet is not None:
            rejets.append(rejet)
            if rejet.reason == SCHEMA_INVALID:
                tally.alternation += 1
            else:
                tally.empty += 1

    if total >= target:
        tally.reached = True
    return tuple(jeux), tally, rejets


# -- Le tournoi en cours -----------------------------------------------------
#
# **La consigne de recherche que le gabarit appelle « la plus rentable du lot »
# porte sur une information que l'application collecte deja.** Mesure du
# 18/08/2026, reprise et affinee ici : sur les profils dont un match du tournoi
# en cours figure au board, `matches-played` sert ce match dans 99 % des cas,
# statistiques de service comprises (173 sur 180).
#
# Ce que la ligne `Ici` ajoute au bloc, et qu'aucune autre ne porte :
#
# - `Parcours` nomme les adversaires **et jamais les resultats**, parce qu'il
#   sort de nos propres scans, qui programment sans rapporter. Ici la source
#   rapporte : le score est un fait, pas une deduction ;
# - `Service` et `Retour` sont des agregats sur **52 semaines**. Un joueur qui
#   sert a 52 % de premieres depuis trois jours ne s'y voit pas ;
# - `Fraicheur` decrit le retard de `tennis-data.co.uk`, une source hebdomadaire
#   et **distincte**. La confusion entre les deux a deja coute une conclusion.

#: Ce que la source **ne sert pas**, verifie le 19/08/2026 sur les 27 242 matchs
#: archives : **aucun champ de duree**, sous aucun nom. Le lot 4 l'avait etabli
#: pour `event/get`, c'est vrai aussi de `matches-played`. `best_of` est present
#: dans le schema et nul sur 27 242 lignes ; `draw_size` aussi.
#:
#: `roundId` est servi a 100 %, mais c'est un **entier opaque** : seize valeurs
#: observees, aucun libelle nulle part dans la charge utile, et `draw` est un
#: numero de place dans le tour et non une taille de tableau. Il **ordonne** les
#: tours a l'interieur d'un tournoi — verifie sur Cincinnati 2026, 1 → 3 → 4 → 5
#: → 6 par date croissante — mais il ne les **nomme** pas. La ligne porte donc la
#: date, qui est un fait, plutot qu'un « Q1 » qui serait une invention. Meme
#: regle que partout : rien ne se deduit d'un libelle, et ici il n'y a meme pas
#: de libelle a deduire.
UNPLAYED_MARKS = {"w/o": "forfait", "ret.": "abandon"}

#: Un set du champ `result`, tie-break compris : `6-4`, `7-6(5)`.
_SET = re.compile(r"^(\d+)-(\d+)(\((\d+)\))?$")


@dataclass(frozen=True)
class TournamentMatch:
    """Un match du tournoi en cours, du point de vue d'un joueur.

    `won` vaut None quand la rencontre n'a pas ete disputee : un forfait n'a pas
    de vainqueur sur le court, et le marquer « gagne » ferait entrer dans la
    ligne un match que `Non joue` declare non joue. Les deux lignes doivent dire
    la meme chose du meme fait.
    """

    played_on: str
    opponent: str
    score: str
    won: bool | None
    #: `forfait` ou `abandon`, vide sur une rencontre menee a son terme.
    mark: str = ""
    #: Sur un forfait, qui a passe le tour. **`won` reste None** — personne n'a
    #: gagne sur le court — mais le sens du forfait est une information a part
    #: entiere, et la position le donne : la source range le qualifie en
    #: `player1`, forfait compris. Verifie sur un cas reel du 18/08, O'Connell —
    #: Fonseca, ou `Non joue` dit « forfait adverse » depuis nos propres scans et
    #: tombe sur la meme lecture.
    advanced: bool | None = None
    line: ServeLine | None = None

    @property
    def contested(self) -> bool:
        """Un tapis vert n'est pas un match joue — meme regle qu'`Usure`."""
        return self.mark != "forfait"


def _reverse_score(score: str) -> str:
    """`6-1 7-6(5)` vu de l'autre cote : `1-6 6-7(5)`.

    **La source ecrit le score du point de vue du vainqueur**, et le bloc doit
    l'ecrire du point de vue du joueur nomme : c'est la convention de `H2H` et
    d'`Aller`, et deux conventions dans le meme bloc se liraient a l'envers. Le
    nombre du tie-break reste attache a son set — il compte les points du perdant
    du jeu decisif, quel que soit le cote depuis lequel on lit.
    """
    sortie = []
    for jeton in str(score).split():
        found = _SET.match(jeton)
        if found is None:
            sortie.append(jeton)
            continue
        sortie.append(f"{found.group(2)}-{found.group(1)}{found.group(3) or ''}")
    return " ".join(sortie)


def _instant(value: str) -> datetime | None:
    """Un horodatage de l'une ou l'autre source, compare a l'autre.

    **Deux ecritures du meme instant ne se comparent pas comme des chaines**, et
    c'est un test qui l'a trouve : la source ecrit `2026-08-14T12:00:00.000Z`,
    nos evenements `2026-08-14T12:00:00Z`, et le point trie avant le `Z`. Le
    premier match d'un tournoi tombait donc juste avant le debut de sa propre
    fenetre — silencieusement, et seulement pour lui.
    """
    try:
        moment = datetime.fromisoformat(str(value or "").replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


def _tournament_matches(
    payload: Any, name: str, tournament_id: int, until: str
) -> list[TournamentMatch]:
    """Les matchs d'un joueur dans **ce** tournoi, avant `until`, du plus ancien.

    **Le vainqueur se lit sur la position et non sur le score**, et c'est la
    mesure qui l'impose contre l'intuition. Les deux lectures ont ete recoupees
    contre `tennis_matches` sur 12 049 rencontres :

    - `player1` est le vainqueur sur **12 046, soit 99,98 %** ;
    - le vainqueur deduit du nombre de sets gagnes n'est juste que sur 98,85 %,
      et **15 de ses 16 erreurs sont des abandons** — sur un `4-6 3-6 3-1 ret.`,
      celui qui menait au tableau d'affichage est celui qui a perdu.

    Lire le fait dans la donnee plutot que dans une convention de position est la
    regle du projet ; ici la mesure dit que la donnee est la moins fiable des
    deux, precisement parce que `ret.` casse le sens du score sans toucher a la
    position. Le reflexe reste juste, la mesure tranche autrement.
    """
    matchs: list[TournamentMatch] = []
    for match in ((payload or {}).get("singles") or []) if isinstance(payload, dict) else []:
        if not isinstance(match, dict) or match.get("tournamentId") != tournament_id:
            continue
        jour = str(match.get("date") or "")[:10]
        quand, borne = _instant(match.get("date")), _instant(until)
        if not jour or quand is None or borne is None or quand >= borne:
            continue
        cotes = _side(match, name)
        if cotes is None:
            continue
        mine, _theirs = cotes
        adversaire = str(
            (match.get("player2" if mine == "player1" else "player1") or {}).get("name") or ""
        )
        brut = str(match.get("result") or "").strip()
        marque = next((label for jeton, label in UNPLAYED_MARKS.items() if jeton in brut), "")
        score = " ".join(jeton for jeton in brut.split() if jeton not in UNPLAYED_MARKS)
        premier = mine == "player1"
        gagne = None if marque == "forfait" else premier
        lignes, _ = parse_matches_played(
            {"singles": [match]}, name, _int(match.get("tournamentId"))
        )
        matchs.append(
            TournamentMatch(
                played_on=jour,
                opponent=adversaire,
                score=score if gagne is not False else _reverse_score(score),
                won=gagne,
                mark=marque,
                advanced=premier if marque == "forfait" else None,
                line=lignes[0] if lignes else None,
            )
        )
    return sorted(matchs, key=lambda item: item.played_on)


def archived_profile(
    canonical: str, settings: Settings, cache: dict[str, tuple[Any, str]] | None = None
) -> tuple[Any, str]:
    """La derniere reponse `matches-played` archivee d'un joueur, et sa date.

    **Aucun appel.** Meme idiome qu'`archived_timeline`, et pour la meme raison :
    ce bloc se rend a chaque generation de prompt et a chaque ouverture d'une
    fiche de match, donc il ne peut pas etre suspendu a un appel reseau.

    **Le cache est passe par l'appelant et vit le temps d'un lot**, meme idiome
    que `ratings_by_key` : un joueur revient dans plusieurs blocs d'une meme
    session, et la charge utile se decode a chaque fois. Pas de memo global —
    son invalidation apres une passe de collecte serait a inventer.
    """
    if cache is not None and canonical in cache:
        return cache[canonical]
    chemin = f"{PREFIX}/profile/{canonical}/matches-played"
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT raw_json, fetched_at FROM api_responses "
            " WHERE provider = ? AND path = ? ORDER BY id DESC LIMIT 1",
            (PROVIDER, chemin),
        ).fetchone()
    if row is None:
        found: tuple[Any, str] = (None, "")
    else:
        try:
            found = (json.loads(str(row["raw_json"])), str(row["fetched_at"] or ""))
        except (TypeError, ValueError):
            found = (None, "")
    if cache is not None:
        cache[canonical] = found
    return found


def _same_player(left: str, right: str) -> bool:
    """Deux ecritures du meme joueur ? **Genereux a dessein.**

    Les deux sources n'ecrivent pas les noms pareil, et c'est mesure : nos scans
    disent « Leylah Fernandez » et « Bianca Andreescu », la source « Leylah Annie
    Fernandez » et « Bianca Vanessa Andreescu ». L'egalite stricte de `sort_key`
    les separe, et le fragment « non couvert » nommait alors un match dont le
    score figure sur la ligne juste au-dessus.

    **Le sens de l'erreur commande la tolerance.** Un faux positif envoie
    chercher un score deja rendu — une place de dossier perdue ; un faux negatif
    ne fait que taire un fragment qui n'existait pas hier. En cas de doute on
    declare donc **couvert**.

    **Une seule regle de nom dans ce module**, et elle sert aussi a corroborer
    un tournoi (`_tournament_id`). La strictesse y a ete essayee et elle etait
    inutile : les 14 fragments qui rendaient un autre tournoi portaient des
    adversaires que nous n'avions **jamais** scannes ici, donc c'est le jour
    exact qui les ecarte, pas le nom. Deux reglages pour la meme question
    auraient diverge.

    La regle est celle de `tennis_history.resolve` : **meme nom de famille, et
    prenoms en chaine de prefixes**. Elle reunit « Leylah » et « Leylah Annie »,
    elle separe les freres Zverev — `alexander` et `mischa` ne se prefixent pas.
    """
    gauche, droite = sort_key(left).split(), sort_key(right).split()
    if not gauche or not droite or gauche[-1] != droite[-1]:
        return False
    court, long = sorted((gauche[:-1], droite[:-1]), key=len)
    return all(
        any(autre.startswith(mot) or mot.startswith(autre) for autre in long) for mot in court
    )


def _tournament_name(match: Any) -> str:
    """Le nom du tournoi porte par un match de la source, replie."""
    from .tennis_history import _flat

    tournoi = (match or {}).get("tournament") or {}
    return _flat(str(tournoi.get("name") or ""))


def _scanned_here(
    player: str, competition_id: int | None, until: str, settings: Settings
) -> tuple[set[str], set[str]]:
    """Ce que **nos propres scans** savent du parcours d'un joueur ici.

    Rend `(adversaires, jours)` — les noms **tels que scannes**, compares par
    `_same_player`, et les dates civiles de coup d'envoi. C'est la piece qui
    corrobore un identifiant de tournoi source : voir `_tournament_id`.

    **Le jour se compare a l'exact, jamais a un jour pres.** La tolerance parait
    prudente et elle ouvre precisement le trou qu'on ferme : pendant la semaine
    de chevauchement, un match du tournoi precedent tombe la veille d'un match
    d'ici et corroborerait. Un joueur ne dispute qu'une rencontre par jour — la
    meme premisse que `_resolve_duplicates` — donc l'egalite stricte ne peut pas
    se tromper de tournoi. Ce qu'elle rate, un fuseau qui fait basculer minuit,
    le nom le rattrape.

    **Lecture directe de `events`, jamais `tennis_load.load_for`.** Celui-ci
    appelle `contested_days`, qui appelle `_tournament_id` : passer par lui
    ferait une recursion. La date civile suffit ici — on ne date rien, on
    corrobore.
    """
    if not player or not competition_id or not until:
        return set(), set()
    cle = sort_key(player)
    noms: set[str] = set()
    jours: set[str] = set()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT home, away, commence_time FROM events "
            " WHERE competition_id = ? AND commence_time < ?",
            (competition_id, until),
        ).fetchall()
    for row in rows:
        if cle not in (sort_key(row["home"]), sort_key(row["away"])):
            continue
        autre = row["away"] if cle == sort_key(row["home"]) else row["home"]
        if autre:
            noms.add(str(autre))
        jours.add(str(row["commence_time"])[:10])
    return noms, jours


def declared_tournaments(competition_id: int | None, settings: Settings) -> set[str]:
    """Les noms du tournoi **chez le fournisseur de profils**, replies.

    Lecture unique, celle de `tennis_history.profile_tournament_names` : c'est la
    table verifiee a la main du lot 17, et la recopier en SQL l'aurait fait
    diverger au premier tournoi rattache.

    Import tardif — `tennis_history` importe ce module au chargement.
    """
    from .tennis_history import _flat, profile_tournament_names

    return {_flat(nom) for nom in profile_tournament_names(competition_id, settings)}


def _tournament_id(
    payload: Any,
    name: str,
    window: tuple[str, str],
    scanned: tuple[set[str], set[str]] = (set(), set()),
    declared: set[str] | None = None,
) -> int:
    """L'identifiant source du tournoi en cours, lu sur les matchs du joueur.

    **Il se lit dans la fenetre de notre edition, jamais sur le dernier match du
    joueur.** Un joueur qui entre en lice n'a rien joue ici : son dernier tournoi
    est celui de la semaine passee, et le prendre ferait rendre les matchs de
    Toronto sous le titre « ici ». La fenetre vient de nos propres evenements —
    `tennis_round.edition_for`, deja ecrit — et c'est notre board qui dit quel
    tournoi se joue, pas le calendrier du joueur.

    Une fois l'identifiant connu, **tous** les matchs qui le portent sont pris, y
    compris ceux joues avant notre premier scan : c'est precisement ce que
    `Parcours` ne peut pas faire, et la raison d'etre de cette ligne.

    ## La fenetre ne suffit pas, et le mode sur la fenetre rendait un autre tournoi

    Deux tournois se chevauchent une semaine sur deux : le Canadien finit le
    lundi ou Cincinnati commence, et **notre fenetre d'edition contient la fin du
    precedent**. Le mode y designait donc le tournoi de la semaine passee des que
    le joueur y avait joue plus de matchs qu'ici — cas ordinaire d'un
    demi-finaliste qui entre en lice.

    Mesure du 20/08/2026 sur les 195 blocs de tennis soumis : **14 fragments sur
    223** rendaient un autre tournoi sous le titre « ici ». Le plus net est
    Darderi — Hijikata du 15/08, ou la ligne servait les quatre matchs du
    Canadien de l'un et le Washington de l'autre, sans qu'un mot le signale.
    C'est le defaut caracteristique du projet : l'echec et le cas ordinaire
    rendaient la meme chose.

    **La fenetre est donc corroboree par nos propres scans** : un match de la
    source ne compte pour l'identification que s'il porte un adversaire ou un
    jour que nous avons scannes ici. Sans corroboration possible — un joueur qui
    entre en lice — l'identifiant est **0**, et c'est son partenaire qui le
    donne : `here_lines` fait deja ce partage.

    **Les deux criteres sont necessaires, et c'est mesure des deux cotes.** Sur
    les 409 rencontres scannees de ces blocs, 258 se rapprochent par le nom
    **et** par le jour, **109 par le nom seul** — les deux sources ne datent pas
    toujours pareil, Hijikata — Monfils vaut 13/08 23h05 chez nous et 14/08
    02h00 chez elle — et **14 par le jour seul**, la source ecrivant « Bianca
    Vanessa Andreescu » ou nos scans disent « Bianca Andreescu ».

    ## Et le tournoi du fragment se corrobore contre celui du bloc

    Les deux criteres ci-dessus se lisent sur nos **scans** ; celui-ci se lit sur
    le **nom du tournoi** que la source porte dans chaque match, compare a la
    table de rattachement du lot 17 (`profile_tournament_names`). Elle existait
    et n'etait branchee que sur le palmares : le fournisseur ecrit
    `Cincinnati Open - Cincinnati` et `National Bank Open - Toronto`, et rien
    d'autre n'a besoin d'etre devine.

    Les deux gardes sont **cumulatives et non alternatives** : la corroboration
    par les scans peut tomber sur un joueur qui a croise le meme adversaire dans
    les deux tournois de la quinzaine, le nom du tournoi ne le peut pas.

    **Une competition non rattachee ne rend pas la garde negative**, elle la rend
    muette : un ensemble declare vide n'affirme rien, meme regle que partout — la
    moitie « ici » du palmares se tait plutot que d'ecrire « jamais joue ».
    """
    bas, haut = _instant(window[0]), _instant(window[1])
    if bas is None or haut is None:
        return 0
    noms, jours = scanned
    if not noms and not jours:
        return 0
    compte: dict[int, int] = {}
    for match in ((payload or {}).get("singles") or []) if isinstance(payload, dict) else []:
        if not isinstance(match, dict):
            continue
        quand = _instant(match.get("date"))
        cotes = _side(match, name)
        if quand is None or not (bas <= quand < haut) or cotes is None:
            continue
        mine, _theirs = cotes
        adversaire = str(
            (match.get("player2" if mine == "player1" else "player1") or {}).get("name") or ""
        )
        vu = any(_same_player(adversaire, autre) for autre in noms)
        if not vu and str(match.get("date") or "")[:10] not in jours:
            continue
        if declared and _tournament_name(match) not in declared:
            continue
        identifiant = _int(match.get("tournamentId"))
        if identifiant:
            compte[identifiant] = compte.get(identifiant, 0) + 1
    if not compte:
        return 0
    return max(compte, key=lambda cle: compte[cle])


#: Le drapeau de la ligne `Ici`. **Bas par defaut**, meme raison que
#: `SERVE_LINES_ENABLED` : la coupe budget/lignes de service est deja jointe au
#: 18/08, et joindre une troisieme variable a la meme date rendrait les trois
#: effets indissociables. Le `changelog_mesure` existe pour qu'ils se decoupent.
CURRENT_EVENT_LINE_ENABLED = "current_event_line_enabled"


def _here_result(match: TournamentMatch) -> str:
    """`17/08 bat Sakkari 6-3 6-4`, ou ce qui n'a pas eu lieu.

    Le verbe porte l'issue et le score est **toujours du point de vue du joueur
    nomme** : les deux se contredisent si l'un des deux se retourne, et c'est
    l'invariant que le test verifie plutot qu'une valeur.
    """
    quand = _short_day(match.played_on)
    if match.won is None:
        # **Le sens du forfait est dit, jamais tu.** « forfait Fonseca » laisse
        # deviner qui est sorti, et c'est precisement le fait que la ligne
        # apporte : un tour gagne sans jouer et un tour abandonne ne decrivent
        # pas le meme joueur au tour suivant.
        sens = "forfait de" if match.advanced else "forfait contre"
        return f"{quand} {sens} {match.opponent}".strip()
    verbe = "bat" if match.won else "perd contre"
    fin = f" ({match.mark})" if match.mark else ""
    return f"{quand} {verbe} {match.opponent} {match.score}{fin}".strip()


def _here_serve(matches: list[TournamentMatch]) -> str:
    """`service ici 58.2% 1re · 68.4% s/1re · 12 df (3 matchs, 249 pts)`.

    **Somme des comptes, jamais moyenne de pourcentages** — la regle du module,
    et elle compte double ici : trois matchs de longueurs tres differentes
    donneraient une moyenne qui ne decrit aucun d'eux.

    Le denominateur est en **points**, pas en matchs : c'est lui qui dit si le
    chiffre tient. La date du relevé, elle, est **portee une seule fois par
    joueur**, sur le fragment des resultats qui vient au-dessus : elle qualifie
    les deux, et l'ecrire deux fois couterait une repetition par bloc.

    **Les trois grandeurs sont des taux, et les doubles fautes ne faisaient pas
    exception par choix.** La ligne rendait `12 df` — un compte brut — a cote de
    `61.8% 1re` et `71.0% s/1re`, quand `Service` rend `11.3% df` sur les
    **secondes balles**. Les deux lignes decrivent la meme joueuse a deux
    profondeurs, et rien ne les rapprochait sans un calcul intermediaire : 12
    doubles fautes sur environ 81 secondes balles font 14,8 % sur ce tournoi
    contre 11,3 % sur 52 semaines, soit une degradation nette que le bloc ne
    donnait pas a lire.

    **Le compte brut n'est pas garde a cote**, contrairement a ce que le brief
    propose. Il faudrait un seuil de « petit denominateur » qui s'inventerait, et
    la parenthese borne deja le fragment entier — `(3 matchs, 212 pts)` dit
    exactement ce que le compte disait de la solidite.
    """
    lignes = [item.line for item in matches if item.line is not None]
    if not lignes:
        return ""
    premieres = sum(ligne.first_serve for ligne in lignes)
    points = sum(ligne.first_serve_of for ligne in lignes)
    gagnes = sum(ligne.won_first for ligne in lignes)
    sur_premiere = sum(ligne.won_first_of for ligne in lignes)
    doubles = sum(ligne.double_faults for ligne in lignes)
    secondes = sum(ligne.second_serves for ligne in lignes)
    if not points:
        return ""
    morceaux = [f"{_pct(_rate(premieres, points))} 1re"]
    if sur_premiere:
        morceaux.append(f"{_pct(_rate(gagnes, sur_premiere))} s/1re")
    # **Un taux, sur le denominateur de `Service`.** Les deux lignes decrivent le
    # meme joueur a deux profondeurs et doivent se comparer d'un coup d'oeil ; un
    # compte brut en face d'un taux demande un calcul intermediaire que personne
    # ne fait. Rien quand aucune seconde balle n'a ete servie — un taux sans
    # denominateur n'existe pas, et zero se lirait comme « aucune double faute ».
    if secondes:
        morceaux.append(f"{_pct(_rate(doubles, secondes))} df")
    compte = f"{len(lignes)} match{'s' if len(lignes) > 1 else ''}, {points} pts"
    return f"service ici {' · '.join(morceaux)} ({compte})"


#: Ce que rend la ligne quand nos scans placent ici un match que la source ne
#: rapporte pas. **Une constante et non un litteral recopie**, meme regle que
#: `HERE_NO_MATCH` et `NEUTRAL_MARK` : la mesure de couverture la relit.
HERE_UNCOVERED = "non couvert"

#: Ce que `Fraicheur` ecrit deja quand tout le parcours manque. **Repris mot pour
#: mot** : deux formulations pour le meme fait se liraient comme deux faits, et
#: le lecteur connait deja celle-la.
WHOLE_PATH = "(tout le Parcours)"


def _uncovered(
    player: str,
    competition_id: int | None,
    until: str,
    matches: list[TournamentMatch],
    oddsapi_key: str | None,
    settings: Settings,
) -> str:
    """`1 match non couvert : Aryna Sabalenka (2194)`, ou rien.

    **La soustraction est deterministe, donc elle ne se delegue pas.**
    `Parcours` nomme les rencontres que nos scans placent ici ; cette ligne-ci
    rapporte celles dont la source connait le resultat. Ce qui reste est ce dont
    le bloc **ne dit pas** l'issue — et c'etait a l'analyse de le trouver en
    croisant trois lignes de tete.

    Mesure du 20/08/2026 qui l'impose, sur le bloc Bejlek — Keys : `Parcours`
    nommait quatre adversaires, `Ici` en couvrait trois, et le quatrieme etait
    **Aryna Sabalenka**, jouee la veille. Le fait le plus determinant de la
    rencontre etait celui que le bloc taisait.

    ## Ce que la borne n'est pas

    « Posterieur au releve » est la borne evidente et elle est fausse **deux
    fois**, mesure a l'appui sur les 409 rencontres scannees des blocs soumis :

    - comparee au **jour**, elle n'attrape rien — la journee de tournoi du match
      Sabalenka vaut `2026-08-19` comme le jour du releve, alors que le coup
      d'envoi est a `00:30` le 20 ;
    - comparee a l'**instant**, elle n'attrape que **6 des 28** non couverts. Un
      match commence trente minutes avant le releve n'est pas fini quand il
      passe : Pegula — Cirstea part a 16:30, le releve est a 16:40, et la source
      n'en dit rien. Il faudrait la duree du match, qu'aucune source ne publie.

    La soustraction, elle, n'a pas de borne a choisir : elle compare deux listes
    que l'application possede.

    Le rapprochement se fait sur le **nom ou le jour**, jamais sur un seul des
    deux : les deux sources ne datent pas toujours pareil — nos scans placent
    Hijikata — Monfils le 13/08 a 23h05, la source le 14/08 a 02h00 — et ne
    nomment pas toujours pareil. Le nom passe par `_same_player`, genereux :
    voir la raison la-bas.
    """
    from . import tennis_load

    faced = tennis_load.load_for(player, competition_id, until, settings).faced
    if not faced:
        return ""
    adversaires = [item.opponent for item in matches]
    # **Le jour se compare a l'exact, sans la tolerance de `_DAY_SLACK`**, et les
    # deux usages ne sont pas le meme : corroborer un *tournoi* accepte n'importe
    # lequel de ses matchs, donc un jour voisin ; couvrir *cette* rencontre-ci
    # n'accepte qu'elle. Trouve en rendant le bloc — a `+/-1`, la journee du
    # 18/08 couvrait celle du 19 et Sabalenka disparaissait, ce que ce fragment
    # existe precisement pour empecher.
    jours = {str(item.played_on)[:10] for item in matches}
    manquants = [
        adversaire
        for jour, adversaire in faced
        if jour not in jours and not any(_same_player(adversaire, autre) for autre in adversaires)
    ]
    if not manquants:
        return ""
    compte = f"{len(manquants)} match{'s' if len(manquants) > 1 else ''} {HERE_UNCOVERED}"
    if len(manquants) == len(faced):
        # Les nommer recopierait `Parcours` mot pour mot : trois mots suffisent,
        # et ce sont ceux que `Fraicheur` emploie deja pour le meme fait.
        return f"{compte} {WHOLE_PATH}"
    from .tennis_load import _with_elo

    return f"{compte} : " + ", ".join(_with_elo(nom, oddsapi_key, settings) for nom in manquants)


def _here_for(
    player: str,
    circuit: str,
    window: tuple[str, str],
    until: str,
    settings: Settings,
    tournament_id: int = 0,
    cache: dict[str, tuple[Any, str]] | None = None,
    competition_id: int | None = None,
    oddsapi_key: str | None = None,
) -> tuple[list[str], int]:
    """Les fragments d'un joueur, et l'identifiant de tournoi qu'il a servi."""
    identity = load_identity(player, circuit, settings)
    canonical = identity.canonical if identity and identity.resolved else player
    charge, releve = archived_profile(canonical, settings, cache)
    if charge is None:
        return [], tournament_id
    identifiant = tournament_id or _tournament_id(
        charge,
        canonical,
        window,
        _scanned_here(player, competition_id, until, settings),
        declared_tournaments(competition_id, settings),
    )
    if not identifiant:
        return [], 0
    matchs = _tournament_matches(charge, canonical, identifiant, until)
    horodatage = f" [releve au {_short_day(releve)}]" if releve else ""
    # **Ce que la source ne rapporte pas se compte ici aussi.** Un joueur que la
    # source croit entrant alors que nos scans lui donnent trois tours est le cas
    # ou la ligne a le plus a dire : le taire ferait lire « aucun match » comme
    # un fait sur le joueur.
    manquants = _uncovered(player, competition_id, until, matchs, oddsapi_key, settings)
    if not matchs:
        # **« aucun match dans ce tournoi » et jamais un silence.** Un joueur qui
        # entre en lice est un fait sur le match — c'est meme le fait dominant
        # quand l'autre sort de trois tours — et un blanc se lirait comme un
        # defaut de collecte.
        fragments = [f"{player} {HERE_NO_MATCH}{horodatage}"]
        if manquants:
            fragments.append(manquants)
        return fragments, identifiant
    parcours = " | ".join(_here_result(item) for item in matchs)
    # **La ligne porte la date de son releve, comme `Parcours` porte la fenetre
    # de nos scans.** Sans elle, une liste s'arretant la veille se lit comme un
    # parcours complet : constate sur le rendu reel du 19/08, ou le match de
    # Jaime Faria contre Adam Walton, joue apres le releve, manquait sans qu'un
    # mot le dise — et `Parcours`, lui, le portait.
    #
    # Elle est **par joueur** : deux profils se rafraichissent a deux instants
    # differents, et une date de lot ferait affirmer sur l'un ce qui n'est vrai
    # que de l'autre.
    fragments = [f"{player} {parcours}{horodatage}"]
    if manquants:
        fragments.append(manquants)
    service = _here_serve([item for item in matchs if item.contested])
    if service:
        fragments.append(service)
    return fragments, identifiant


def contested_days(
    player: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> dict[str, int]:
    """Combien de matchs **disputes** la source rapporte, par jour de tournoi.

    **Elle sert a demonter une premisse, pas a nommer un adversaire**, et c'est
    ce qui la rend utilisable. `tennis_load._resolve_duplicates` repose sur « un
    joueur ne dispute qu'une rencontre par journee de tournoi » ; le jour ou la
    source en compte deux, la premisse est fausse **pour ce jour-la**, et ca se
    lit sans rapprocher un seul nom.

    Mesure du 19/08/2026 qui l'impose. Sur Madison Keys – Xiyu Wang, le bloc rend
    a quatre lignes d'ecart :

        Non joue   Xiyu Wang — Bianca Andreescu le 13/08, adversaire remplace,
                   non disputee
        Ici        Xiyu Wang 13/08 bat Polina Kudermetova 6-3 6-2
                   | 13/08 bat Bianca Vanessa Andreescu 6-0 6-4 | …

    Nos deux scans du 13/08 sont reels, les deux matchs ont ete joues, et c'est
    la deduction qui a produit un faux positif — le cas que `CLAUDE.md` annoncait
    comme « ne s'observe pas en base ». Il s'observe.

    **Le rapprochement par nom etait la fausse piste**, et il fallait le mesurer
    pour l'ecarter : la source ecrit « Bianca Vanessa Andreescu » ou nos scans
    disent « Bianca Andreescu », donc `sort_key` ne tombe pas et une comparaison
    souple serait exactement le « en cas de doute on devine » que le projet
    refuse partout. Le **jour**, lui, est le meme des deux cotes — verifie, la
    journee de tournoi de nos deux evenements vaut `2026-08-13` et la source
    date les deux matchs du meme jour.

    **Positif seulement.** Un jour absent de la reponse ne prouve rien : la
    source peut ne pas couvrir ce tournoi, ou ce joueur, ou n'avoir pas encore
    publie. On ne leve donc la deduction que la ou la source **affirme**, jamais
    la ou elle se tait.

    **Hors du drapeau `CURRENT_EVENT_LINE_ENABLED`, et c'est deliberе.** Le
    drapeau garde une ligne *ajoutee* au bloc ; ici on retire une affirmation
    *fausse* d'une ligne deja servie. Attendre l'activation d'`Ici` laisserait
    « adversaire remplace, non disputee » sur un match joue, sur toutes les
    sessions d'ici la.
    """
    settings = settings or get_settings()
    from .tennis_round import _edition_in_base

    if not player or not competition_id or not commence_time:
        return {}
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT oddsapi_key FROM competitions WHERE id = ?", (competition_id,)
        ).fetchone()
    circuit = circuit_of(str(row["oddsapi_key"]) if row and row["oddsapi_key"] else "")
    if not circuit:
        return {}
    edition = _edition_in_base(competition_id, commence_time, settings)
    if not edition.matches:
        return {}
    identity = load_identity(player, circuit, settings)
    canonical = identity.canonical if identity and identity.resolved else player
    charge, _ = archived_profile(canonical, settings)
    if charge is None:
        return {}
    identifiant = _tournament_id(
        charge,
        canonical,
        (edition.matches[0][0], commence_time),
        _scanned_here(player, competition_id, commence_time, settings),
        declared_tournaments(competition_id, settings),
    )
    if not identifiant:
        return {}
    compte: dict[str, int] = {}
    for item in _tournament_matches(charge, canonical, identifiant, commence_time):
        if item.contested:
            jour = str(item.played_on)[:10]
            compte[jour] = compte.get(jour, 0) + 1
    return compte


#: Ce qu'un joueur qui entre en lice rend. **Une constante et non un litteral
#: recopie** : la mesure de couverture la relit pour classer un bloc, et deux
#: ecritures de la meme phrase auraient diverge au premier ajustement — meme
#: regle que `NEUTRAL_MARK` et `weather.ALERT_MARK`.
HERE_NO_MATCH = "aucun match dans ce tournoi"


@dataclass(frozen=True)
class HereCoverage:
    """Ce que la ligne `Ici` couvre, par circuit et par mois.

    **Trois etats et non deux**, parce qu'ils n'appellent pas la meme lecture :
    un bloc `renseigne` porte des resultats des deux cotes ; un bloc `partiel`
    en porte d'un cote et « aucun match dans ce tournoi » de l'autre — ce qui est
    un **fait sur le match**, souvent le fait dominant quand l'un sort de trois
    tours et l'autre entre en lice ; un bloc `absent` n'a pas de ligne du tout,
    et c'est le seul des trois qui decrive un manque de collecte.

    Les fondre ferait lire une entree en lice comme un trou, exactement ce que
    la mention explicite existe pour eviter.
    """

    month: str
    circuit: str
    blocks: int = 0
    filled: int = 0
    partial: int = 0
    absent: int = 0

    @property
    def served(self) -> int:
        """Blocs portant une ligne, quelle qu'elle dise."""
        return self.filled + self.partial

    @property
    def share(self) -> float | None:
        return self.served / self.blocks if self.blocks else None


def here_coverage(settings: Settings | None = None) -> list[HereCoverage]:
    """La couverture de la ligne `Ici` sur les blocs tennis reellement soumis.

    La population est `prompt_events` — les matchs **partis a l'analyse** — et
    non le board : c'est la seule qui dise ce que le modele a eu sous les yeux.

    **Mesure hors du drapeau, et c'est delibere.** Elle decrit ce que la source
    sait servir, pas ce que la configuration du jour rend : gardee par le
    drapeau, elle rendrait des zeros le jour ou il redescend et se lirait comme
    une source tarie. Le drapeau est donc force ici, et nulle part ailleurs.

    Aucun appel : la charge utile `matches-played` est archivee.
    """
    settings = settings or get_settings()
    ouvert = settings.model_copy(update={CURRENT_EVENT_LINE_ENABLED: True})
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.home, e.away, e.commence_time, e.competition_id, c.oddsapi_key "
            "  FROM prompt_events pe "
            "  JOIN events e ON e.id = pe.event_id "
            "  JOIN sports s ON s.id = e.sport_id "
            "  JOIN competitions c ON c.id = e.competition_id "
            " WHERE s.key = 'tennis' ORDER BY e.commence_time"
        ).fetchall()

    # Un joueur revient dans plusieurs blocs d'un meme tournoi : sans ce cache,
    # sa charge utile se relit et se decode a chaque fois. Mesure sur les 190
    # blocs archives — 414 lectures pour 253 profils distincts.
    cache: dict[str, tuple[Any, str]] = {}
    tally: dict[tuple[str, str], dict[str, int]] = {}
    for row in rows:
        circuit = circuit_of(str(row["oddsapi_key"] or ""))
        if not circuit:
            continue
        cle = (str(row["commence_time"])[:7], circuit)
        compte = tally.setdefault(cle, {"blocks": 0, "filled": 0, "partial": 0, "absent": 0})
        compte["blocks"] += 1
        lignes = here_lines(
            str(row["home"] or ""),
            str(row["away"] or ""),
            circuit,
            row["competition_id"],
            str(row["commence_time"]),
            ouvert,
            cache,
        )
        if not lignes:
            compte["absent"] += 1
        elif HERE_NO_MATCH in lignes[0][1]:
            compte["partial"] += 1
        else:
            compte["filled"] += 1
    return [
        HereCoverage(month=mois, circuit=circuit, **compte)
        for (mois, circuit), compte in sorted(tally.items())
    ]


def here_lines(
    home: str,
    away: str,
    circuit: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
    cache: dict[str, tuple[Any, str]] | None = None,
    oddsapi_key: str | None = None,
) -> list[tuple[str, str]]:
    """La ligne `Ici` : ce que chaque joueur a fait dans **ce** tournoi.

    Aucun appel : la charge utile `matches-played` est deja archivee par la passe
    d'entretien, et c'est elle qu'on relit. Rien n'est rendu tant que le drapeau
    `CURRENT_EVENT_LINE_ENABLED` est bas.

    **L'identifiant de tournoi se resout une fois pour les deux joueurs.** Celui
    qui a joue le donne a celui qui entre en lice : sans ce partage, un entrant
    n'aurait aucun tournoi de reference et sa ligne se tairait la ou elle a le
    plus a dire.
    """
    settings = settings or get_settings()
    if not getattr(settings, CURRENT_EVENT_LINE_ENABLED, False) or not circuit:
        return []
    from .tennis_round import _edition_in_base

    edition = _edition_in_base(competition_id, commence_time, settings)
    if not edition.matches:
        return []
    window = (edition.matches[0][0], commence_time)

    fragments: list[str] = []
    identifiant = 0
    # Deux passes : la premiere resout le tournoi sur celui des deux qui a joue,
    # la seconde rend les fragments dans l'ordre du bloc — domicile d'abord,
    # comme partout.
    rendus: dict[str, list[str]] = {}
    for joueur in (home, away):
        if joueur:
            rendus[joueur], identifiant = _here_for(
                joueur,
                circuit,
                window,
                commence_time,
                settings,
                identifiant,
                cache,
                competition_id,
                oddsapi_key,
            )
    for joueur in (home, away):
        if joueur and not rendus.get(joueur) and identifiant:
            rendus[joueur], _ = _here_for(
                joueur,
                circuit,
                window,
                commence_time,
                settings,
                identifiant,
                cache,
                competition_id,
                oddsapi_key,
            )
    for joueur in (home, away):
        fragments.extend(rendus.get(joueur) or [])
    return [("Ici", "\n".join(fragments))] if fragments else []
