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

**Ce que la vue partielle interdit, en revanche, c'est de compter depuis le
debut.** Nommer « 2e tour » suppose de connaitre la taille du tableau ; nommer
« quart de finale » ne suppose que de savoir combien il reste de joueurs. Les
tours de la fin sont donc nommes sans condition, et les premiers seulement
quand le nombre de joueurs vus est une taille de tableau qui existe
(`PLAUSIBLE_DRAWS`). Le tableau ATP a 79 joueurs n'en est pas une : il ne
produit aucun libelle sur ses premieres journees, et retrouve la parole a
l'approche de la fin.

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

#: Tailles de tableau qui existent sur les circuits ATP et WTA. Un total de
#: joueurs qui n'y figure pas signale une vue tronquee du tournoi : les tours
#: comptes depuis le debut se taisent alors, faute de pouvoir dire lequel est
#: le premier.
PLAUSIBLE_DRAWS = (28, 32, 48, 56, 64, 96, 128)

#: Au-dela, c'est une autre edition du meme tournoi. La competition garde son
#: identifiant d'une annee sur l'autre : sans cette coupure, les joueurs de
#: l'edition precedente gonfleraient le compte.
EDITION_GAP_DAYS = 5

#: Nom des derniers tours, par nombre de joueurs encore en lice au debut du
#: tour. Au-dela, on compte depuis le debut (« 1er tour ») : c'est ainsi que le
#: tennis se raconte, et le passage se fait exactement a seize.
FINAL_ROUNDS = {
    2: "finale",
    4: "demi-finale",
    8: "quart de finale",
    16: "huitième de finale",
}


@dataclass(frozen=True)
class Edition:
    """Les matchs d'une meme edition d'un tournoi, tels que nous les avons vus."""

    #: `(commence_time UTC, cle du joueur A, cle du joueur B)`, triee par date.
    matches: tuple[tuple[str, str, str], ...] = ()

    @property
    def players(self) -> int:
        """Joueurs distincts vus dans cette edition."""
        return len({key for _, home, away in self.matches for key in (home, away) if key})


def _is_power_of_two(value: int) -> bool:
    return value > 0 and value & (value - 1) == 0


def _ceil_power_of_two(value: int) -> int:
    """Plus petite puissance de deux superieure ou egale a `value`."""
    if value <= 1:
        return 1
    return 1 << (value - 1).bit_length()


def draw_sequence(size: int) -> list[int]:
    """Joueurs en lice au debut de chaque tour, pour un tableau de `size`.

    Un tableau qui n'est pas une puissance de deux distribue des exemptions au
    premier tour : de 96 joueurs on passe a 64, pas a 48. La suite se lit donc
    `[96, 64, 32, 16, 8, 4, 2]`, ou le premier tour est bien le premier element.
    """
    sequence = [size]
    current = size
    while current > 2:
        current = current // 2 if _is_power_of_two(current) else 1 << (current.bit_length() - 1)
        sequence.append(current)
    return sequence


def _ordinal(index: int) -> str:
    return "1er tour" if index == 1 else f"{index}e tour"


def label_for(edition: Edition, commence_time: str) -> str | None:
    """Nom du tour de ce match, ou None si rien ne peut etre affirme."""
    when = parse(commence_time)
    if when is None or not edition.matches:
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

    if alive <= max(FINAL_ROUNDS):
        # Les derniers tours se nomment depuis la fin : aucune connaissance du
        # tableau n'est necessaire, seulement le nombre de joueurs restants. Un
        # tour deja entame laisse un compte impair — quatre joueurs moins une
        # demi-finale jouee en font trois — d'ou l'arrondi a la puissance de
        # deux superieure, qui rend le compte du **debut** du tour.
        return FINAL_ROUNDS.get(_ceil_power_of_two(alive))

    size = edition.players
    if size not in PLAUSIBLE_DRAWS:
        return None
    sequence = draw_sequence(size)
    remaining = [value for value in sequence if value >= alive]
    if not remaining:
        return None
    return _ordinal(sequence.index(min(remaining)) + 1)


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


def round_for(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> str | None:
    """Tour d'un match, relu en base. Aucun appel reseau."""
    if not competition_id or not commence_time:
        return None
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT home, away, commence_time FROM events WHERE competition_id = ? "
            "ORDER BY commence_time",
            (competition_id,),
        ).fetchall()
    return label_for(edition_for(rows, commence_time), commence_time)


def lines(
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Tour » du bloc CONTEXTE, vide si le tour ne peut etre affirme."""
    label = round_for(competition_id, commence_time, settings)
    return [("Tour", label)] if label else []
