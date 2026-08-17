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

import logging
import unicodedata
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import last_known_quota
from ..providers.tennisapi import PROVIDER, TennisAPIClient
from .ingestion import MATCH_REF_UNRESOLVED, SOURCE, Reject
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
INTROUVABLE = "introuvable"
FALLBACKS = (EXACT, CASSE, ACCENTS, INTROUVABLE)


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
    """Casse **et** accents replies. C'est `labels.sort_key`, nomme ici pour que
    le niveau de repli se lise dans le code qui le decide."""
    return sort_key(str(text or "").strip())


def pick_candidate(name: str, candidates: list[str]) -> tuple[str, str]:
    """Le candidat qui correspond au nom, et le niveau de repli qui a tranche.

    Rend `("", INTROUVABLE)` quand rien ne correspond **ou quand plusieurs
    candidats correspondent au meme niveau**. C'est la regle du projet, et elle
    est plus severe ici qu'ailleurs : il n'existe aucune resolution manuelle pour
    rattraper, et attribuer a un joueur les statistiques d'un autre serait pire
    qu'une ligne absente — meme arbitrage que l'Elo tennis.

    Le cas est reel : « Alexander Zverev » rend `['Alexander Zverev',
    'Alexander Zverev Sr']`, et c'est le niveau **exact** qui les departage. Sans
    la progression par niveau, un repli tolerant les aurait pris tous les deux.
    """
    propres = [str(c).strip() for c in candidates if str(c).strip()]
    for niveau, fold in ((EXACT, str.strip), (CASSE, _fold_case), (ACCENTS, _fold_accents)):
        cible = fold(str(name))
        trouves = [candidat for candidat in propres if fold(candidat) == cible]
        if len(trouves) == 1:
            return trouves[0], niveau
        if len(trouves) > 1:
            # Deux candidats indiscernables a ce niveau : on ne devine pas, et
            # descendre d'un cran n'aiderait pas — les niveaux suivants sont
            # **plus** tolerants, donc ils en trouveraient au moins autant.
            return "", INTROUVABLE
    return "", INTROUVABLE


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


async def resolve(
    client: TennisAPIClient,
    local_name: str,
    tour: str,
    settings: Settings | None = None,
) -> tuple[Identity, Reject | None]:
    """Resout la graphie d'un joueur chez le fournisseur. **Une fois, puis cache.**

    Rend l'identite et, quand rien n'a ete trouve, le rejet a journaliser.

    **Deux appels au plus, et le second est mesure, pas suppose.** Le premier
    part avec le nom tel quel — la casse est prise en charge par le serveur. S'il
    rend une liste **vide** et que le nom porte des accents, le second part avec
    les accents replies : `Karolína Muchová` rend `[]` la ou `Karolina Muchova`
    repond. Un nom sans accent ne declenche jamais ce second appel, donc il ne
    coute rien au cas ordinaire.

    **Jamais de troisieme essai sur un nom tronque.** Chercher « Kessler » rend
    trois joueuses, et il n'existe ici aucune resolution manuelle pour
    departager : c'est exactement le cas ou l'Elo tennis refuse de deviner.
    """
    settings = settings or get_settings()
    connue = load_identity(local_name, tour, settings)
    if connue is not None:
        return connue, None

    response = await client.search_raw(local_name, tour)
    candidats = [str(item) for item in response.data] if isinstance(response.data, list) else []
    canonical, niveau = pick_candidate(local_name, candidats)

    if not canonical and not candidats and _fold_accents(local_name) != _fold_case(local_name):
        # La source ne replie pas les accents en entree : c'est mesure, et c'est
        # le seul cas ou un second appel apprend quelque chose.
        replie = _sans_accents(local_name)
        response = await client.search_raw(replie, tour)
        candidats = [str(item) for item in response.data] if isinstance(response.data, list) else []
        canonical, _ = pick_candidate(replie, candidats)
        if canonical:
            niveau = ACCENTS

    identity = Identity(
        local_name=local_name,
        tour=tour,
        canonical=canonical,
        fallback=niveau if canonical else INTROUVABLE,
        response_id=response.archive_id or None,
    )
    store_identity(identity, settings)
    if identity.resolved:
        return identity, None

    return identity, Reject(
        block_type=SOURCE,
        reason=MATCH_REF_UNRESOLVED,
        detail=(
            f"{local_name} ({tour}) : aucune graphie de tennis-api.com ne correspond. "
            f"Candidats rendus : {', '.join(candidats) if candidats else 'aucun'}. "
            "Rien n'est devine — les statistiques d'un autre joueur seraient pires "
            "qu'une ligne absente."
        ),
        payload=f"{local_name}/{tour} -> {candidats!r}"[:400],
    )


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
