"""Le tour d'un match de tennis, deduit de nos propres scans.

Aucune source ne le donne. The Odds API ne transmet pas le tour, et
`tennisdata.co.uk` publie son fichier une fois par semaine : verifie en reel, un
fichier rafraichi le 6 aout ne portait aucun match posterieur au 3, alors que le
tournoi avait commence le 4. Un tour se deduit donc, ou ne se dit pas.

**L'invariant du tableau a elimination directe** : chaque match elimine
exactement un joueur. Donc, a tout instant,

    joueurs en lice = joueurs vus dans le tournoi - matchs deja joues

et le tour se lit dans ce seul nombre : 2 joueurs restants sont une finale, 4
une demi-finale, 16 des huitiemes.

Ce comptage est **juste meme quand notre vue du tournoi est partielle**, et
c'est ce qui le rend utilisable. Un match qu'on n'a jamais scanne elimine un
joueur qu'on n'a jamais vu : il ne compte ni au numerateur ni au denominateur,
et le solde reste exact. Constate en reel sur le Canadian Open : le tableau ATP
n'a montre que 79 joueurs — une vue tronquee de ses premiers jours — et le
nombre de joueurs en lice y tombait malgre tout sur les memes 16 que le tableau
WTA, vu entier, pour la meme journee.

**Tous les tours se nomment donc depuis la fin** — finale, demi, quart,
huitieme, 16e, 32e — et jamais par un ordinal. « 2e tour » exigerait de savoir
ou est le premier, donc la taille du tableau, et rien ne permet de la deduire :
une vue qui commence a une frontiere de tour est **indiscernable** d'un tableau
plus petit vu en entier. Essaye puis retire, apres l'avoir constate en reel :
sur le Canadian Open feminin, 64 joueuses vues sur un tableau de 96 ont fait
annoncer « 2e tour » a huit blocs pour un tour de 32.

En cas de doute, aucune ligne. Un « demi-finale » affiche sur un quart serait
l'erreur la plus visible que ce module puisse produire.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import timedelta
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from .labels import sort_key
from .tournament_day import parse

#: Au-dela, c'est une autre edition du meme tournoi. La competition garde son
#: identifiant d'une annee sur l'autre : sans cette coupure, les joueurs de
#: l'edition precedente gonfleraient le compte.
EDITION_GAP_DAYS = 5

#: Nom du tour, par nombre de joueurs en lice **au debut du tour**. Tous comptes
#: **depuis la fin**, et c'est la seule facon d'avoir raison.
#:
#: L'ordinal — « 1er tour », « 2e tour » — a ete essaye et retire. Il exige de
#: connaitre la taille du tableau, que rien ne permet de deduire : une vue qui
#: commence a une frontiere de tour est **indiscernable** d'un tableau plus
#: petit vu en entier. Constate en reel sur le Canadian Open feminin — 64
#: joueuses vues sur un tableau de 96, le premier tour n'ayant jamais ete scanne
#: — ou huit blocs ont annonce « 2e tour » pour un tour de 32. Le compte des
#: joueurs restants, lui, reste juste sur une vue tronquee : c'est le meme
#: invariant qui rendait deja les derniers tours fiables, applique partout.
ROUND_NAMES = {
    2: "finale",
    4: "demi-finale",
    8: "quart de finale",
    16: "huitième de finale",
    32: "16e de finale",
    64: "32e de finale",
    128: "64e de finale",
}


#: Tailles de tableau qui existent vraiment sur le circuit. Les puissances de
#: deux, plus les tableaux a exemptions — 24, 28, 48, 56 et 96 — ou les tetes de
#: serie entrent au deuxieme tour.
PLAUSIBLE_DRAWS = frozenset({4, 8, 16, 24, 28, 32, 48, 56, 64, 96, 128})

#: Ce que la ligne dit quand le tour ne peut pas etre etabli. **La phase n'est
#: servie par personne** : verifie le 12/08/2026, `/sports/{cle}/events` rend
#: six champs — `id`, `sport_key`, `sport_title`, `commence_time`, `home_team`,
#: `away_team` — et rien d'autre, pour zero credit. Le fichier de resultats,
#: lui, parait une semaine apres coup et ignore les qualifications.
#:
#: Elle ne se devine pas non plus : un tableau de qualification ne finit pas par
#: une finale mais par douze qualifies, donc compter depuis la fin y produit un
#: nombre qui ne designe rien. La ligne dit donc ce qu'elle sait — la population
#: vue ne forme pas un tableau — et s'arrete la.
UNSET_ROUND = "phase non renseignee"


@dataclass(frozen=True)
class Edition:
    """Les matchs d'une meme edition d'un tournoi, tels que nous les avons vus."""

    #: `(commence_time UTC, cle du joueur A, cle du joueur B)`, triee par date.
    matches: tuple[tuple[str, str, str], ...] = ()

    @property
    def players(self) -> int:
        """Joueurs distincts vus dans cette edition."""
        return len({key for _, home, away in self.matches for key in (home, away) if key})


def _ceil_power_of_two(value: int) -> int:
    """Plus petite puissance de deux superieure ou egale a `value`."""
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def is_bracket(players: int) -> bool:
    """La population vue forme-t-elle un tableau qui existe ?

    **Ecrit une fois et lu deux fois** : le tour ne se nomme que sur un tableau,
    et `truncated()` dit la meme chose a l'envers. Deux ecritures auraient
    diverge, et c'est exactement ce qui manquait — le module *savait* que la vue
    n'etait pas un tableau, et nommait un tour quand meme.
    """
    return players > 1 and players in PLAUSIBLE_DRAWS


def label_for(edition: Edition, commence_time: str) -> str | None:
    """Nom du tour de ce match, ou None si rien ne peut etre affirme."""
    when = parse(commence_time)
    if when is None or not edition.matches:
        return None

    if not is_bracket(edition.players):
        # **Le compte ne decrit un tour que s'il decrit un tableau.** Mesure du
        # 12/08/2026, sur une soiree de qualifications a Cincinnati : 34 joueurs
        # vus a 11:05, 76 a 20:15 — aucun des deux n'est une taille de tableau,
        # et le meme match a pourtant ete rendu « 16e de finale » puis « 32e de
        # finale ». L'etiquette suivait l'avancement de nos scans, pas le
        # tournoi.
        #
        # Deux causes, indiscernables d'ici et toutes deux fatales au comptage :
        # un tableau de qualification servi sous la meme cle que le tableau
        # principal — deux tableaux, donc deux populations melangees — et une
        # vue qui commence apres le debut du tournoi. La premiere ne finit meme
        # pas par une finale : compter depuis la fin y produit un nombre qui ne
        # designe rien et emprunte le vocabulaire du tableau principal.
        return None

    # Strictement avant : les matchs simultanes appartiennent au meme tour, et
    # se compter les uns les autres ferait descendre le compte a l'interieur
    # d'un tour deja commence.
    played = sum(1 for moment, _, _ in edition.matches if (parse(moment) or when) < when)
    alive = edition.players - played
    if alive < 2:
        # Plus de matchs comptes que de joueurs vus : la vue est incoherente,
        # rien ne peut en sortir.
        return None

    # Un tour deja entame laisse un compte impair — quatre joueurs moins une
    # demi-finale jouee en font trois — d'ou l'arrondi a la puissance de deux
    # superieure, qui rend le compte du **debut** du tour.
    start = _ceil_power_of_two(alive)
    if start > edition.players:
        # Ce tour n'a jamais pu exister : il demanderait plus de joueurs que le
        # tournoi n'en a montre. C'est le cas d'un tableau a exemptions vu des
        # son premier tour — 96 joueurs ne forment pas un tour de 128.
        return None
    return ROUND_NAMES.get(start)


def edition_for(matches: Sequence[Any], commence_time: str) -> Edition:
    """Isole l'edition du tournoi qui contient ce match.

    `matches` porte toutes les rencontres connues de la competition, quelle que
    soit l'annee. Les editions se separent par un trou de plus de
    `EDITION_GAP_DAYS` : deux tournois du meme nom sont a un an l'un de l'autre,
    et deux journees d'un meme tournoi a un jour.
    """
    rows = sorted(
        (
            (row["commence_time"], sort_key(row["home"]), sort_key(row["away"]))
            for row in matches
            if parse(row["commence_time"]) is not None
        ),
        key=lambda item: item[0],
    )
    if not rows:
        return Edition()

    gap = timedelta(days=EDITION_GAP_DAYS)
    groups: list[list[tuple[str, str, str]]] = []
    previous = None
    for row in rows:
        moment = parse(row[0])
        if previous is None or moment - previous > gap:
            groups.append([])
        groups[-1].append(row)
        previous = moment

    target = parse(commence_time)
    for group in groups:
        first, last = parse(group[0][0]), parse(group[-1][0])
        if first is None or last is None or target is None:
            continue
        if first - gap <= target <= last + gap:
            return Edition(matches=tuple(group))
    return Edition()


def _edition_in_base(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> Edition:
    """L'edition qui contient ce match, relue en base. Aucun appel reseau.

    Lecture unique, partagee par les trois sorties du module : la ligne, le
    booleen de troncature et le tour lui-meme les tiraient chacun de leur propre
    requete, sur la meme table et pour le meme resultat.
    """
    if not competition_id or not commence_time:
        return Edition()
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT home, away, commence_time FROM events WHERE competition_id = ? "
            "ORDER BY commence_time",
            (competition_id,),
        ).fetchall()
    return edition_for(rows, commence_time)


def round_for(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> str | None:
    """Tour d'un match, relu en base. Aucun appel reseau."""
    return label_for(_edition_in_base(competition_id, commence_time, settings), commence_time)


def truncated(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> bool:
    """Vrai si notre vue de cette edition a **provablement** manque des tours.

    Le seul signal sur : le nombre de joueurs vus n'est **pas** une taille de
    tableau qui existe. Mesure en reel — le Canadian Open masculin en a montre
    **79**, ce qui ne forme aucun tableau, donc ses premieres journees precedent
    notre fenetre de scan.

    **Le compte des tours manquants, lui, n'est pas derivable**, et c'est la
    meme raison qui fait que ce module ne nomme jamais « 2e tour » : il faudrait
    la taille du tableau, et une vue qui commence a une frontiere de tour est
    indiscernable d'un tableau plus petit vu en entier. D'ou un booleen et non un
    nombre.

    **Faux negatif assume**, pour cette raison exacte : le tableau feminin du
    meme tournoi n'a montre que **64** joueuses sur 96, et 64 etant une taille de
    tableau valide, rien ne permet de le savoir. Un silence vaut mieux qu'une
    affirmation fausse — c'est la regle du module.
    """
    players = _edition_in_base(competition_id, commence_time, settings).players
    return players > 1 and not is_bracket(players)


def lines(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Tour », et **deux etats plutot qu'un silence**.

    Le tour se nomme quand la population vue forme un tableau ; sinon la ligne
    dit qu'elle ne le forme pas, avec le compte qui le montre. Le silence
    d'avant se lisait comme « tournoi sans tour », alors qu'il valait « nous ne
    savons pas ou en est le tableau » — et il laissait surtout la place a une
    etiquette fausse, puisque le compte etait rendu sans ce garde-fou.

    Le compte est **dans la valeur** : il transforme une affirmation
    invérifiable en fait verifiable d'un coup d'oeil, meme idiome que la fenetre
    de `Parcours` et l'heure de releve des marches.
    """
    edition = _edition_in_base(competition_id, commence_time, settings)
    label = label_for(edition, commence_time)
    if label:
        return [("Tour", label)]
    if edition.players > 1:
        return [("Tour", f"{UNSET_ROUND} ({edition.players} joueurs vus ne forment aucun tableau)")]
    return []
