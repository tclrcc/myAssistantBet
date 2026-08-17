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
from dataclasses import dataclass
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from ..providers.base import last_known_quota
from ..providers.tennisapi import PROVIDER
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
