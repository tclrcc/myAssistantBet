"""Repos et charge de matchs d'un joueur, calcules sur nos propres donnees.

Le football recoit sa forme, ses absents et son classement d'API-Football ; le
tennis n'a que l'Elo, ce qui laisse le bloc CONTEXTE presque vide. Or une
information decisive dort deja dans la base : les tours precedents du meme
tournoi ont ete scannes les jours d'avant.

De ces lignes on tire deux choses, sans un seul appel reseau :

- **les jours de repos** — un joueur qui a joue hier et un joueur qui a joue
  avant-hier n'abordent pas le meme match ;
- **le nombre de tours deja disputes** dans ce tournoi.

Ce qu'on ne tire **pas**, et qu'il ne faut pas inventer : la duree des matchs,
le score, ni la maniere. La base ne stocke aucun resultat. Un joueur present au
tour suivant a forcement passe le precedent, mais l'ecrire supposerait qu'aucun
forfait n'existe — on se contente donc de dater ses apparitions.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from ..config import Settings, get_settings
from ..db import connect
from . import elo
from .labels import sort_key

logger = logging.getLogger(__name__)

#: Au-dela, on parle d'un autre tournoi ou d'une autre semaine : le repos
#: n'a plus de sens comme information de fraicheur.
MAX_DAYS = 10


@dataclass
class Load:
    """Ce que la base sait du parcours d'un joueur dans ce tournoi."""

    rounds: int = 0
    days_rest: int | None = None
    #: Adversaires deja rencontres ici, du premier tour au dernier.
    opponents: tuple[str, ...] = ()

    @property
    def fragment(self) -> str:
        """`2j (3 tours)`, ou rien si le tournoi vient de commencer.

        Le nombre de tours accompagne le repos : deux jours apres un premier
        tour et deux jours apres un quart ne se valent pas.
        """
        if self.days_rest is None:
            return ""
        tours = f" ({self.rounds} tour{'s' if self.rounds > 1 else ''})" if self.rounds else ""
        return f"{self.days_rest}j{tours}"


def load_for(
    player: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> Load:
    """Parcours d'un joueur dans ce tournoi, avant le match considere.

    Rapprochement des noms par `sort_key` : le fournisseur ecrit le meme joueur
    de la meme facon d'un tour a l'autre, mais la casse et les accents peuvent
    varier. Aucun rapprochement flou ici — deux joueurs differents ne doivent
    jamais partager un parcours.
    """
    if not competition_id or not player:
        return Load()
    settings = settings or get_settings()
    key = sort_key(player)

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT home, away, commence_time FROM events "
            "WHERE competition_id = ? AND commence_time < ? ORDER BY commence_time DESC",
            (competition_id, commence_time),
        ).fetchall()

    when = _parse(commence_time)
    dates: list[datetime] = []
    faced: list[tuple[datetime, str]] = []
    for row in rows:
        if key not in (sort_key(row["home"]), sort_key(row["away"])):
            continue
        played = _parse(row["commence_time"])
        if when is None or played is None or (when - played).days > MAX_DAYS:
            continue
        dates.append(played)
        # L'adversaire est l'autre nom de la ligne. Il est retenu tel qu'il a ete
        # scanne : c'est ainsi qu'il figure partout ailleurs dans l'application.
        autre = row["away"] if key == sort_key(row["home"]) else row["home"]
        if autre:
            faced.append((played, autre))

    if not dates or when is None:
        return Load()
    return Load(
        rounds=len(dates),
        days_rest=(when.date() - max(dates).date()).days,
        opponents=tuple(nom for _, nom in sorted(faced)),
    )


def lines(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Repos » du bloc, vide si aucun tour precedent n'est connu.

    Un tournoi dont on n'a scanne que le jour meme ne produit rien : ecrire
    « 0 tour » laisserait croire a une entree en lice alors qu'on ne sait
    simplement pas.
    """
    settings = settings or get_settings()
    fragments = []
    for player in (home, away):
        if not player:
            continue
        fragment = load_for(player, competition_id, commence_time, settings).fragment
        if fragment:
            fragments.append(f"{player} {fragment}")
    return [("Repos", " | ".join(fragments))] if fragments else []


def path_lines(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    oddsapi_key: str | None = None,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Ligne « Parcours » : qui chaque joueur a deja rencontre dans ce tournoi.

    **Les adversaires, jamais les resultats.** La base ne stocke aucun score : un
    joueur present au tour suivant a forcement passe le precedent, mais l'ecrire
    « il a battu X » supposerait qu'aucun forfait n'existe. La ligne nomme donc
    qui a ete rencontre, et laisse le reste a la recherche.

    L'Elo de l'adversaire accompagne son nom quand il est connu — c'est lui qui
    distingue un parcours facile d'un parcours d'usure, et il ne coute rien : le
    classement est deja en base pour la ligne « Elo ».

    Ce que cette ligne **ne peut pas** donner, et qu'il ne faut pas lui prêter :
    la duree des matchs et les statistiques de service. Aucune source gratuite ne
    les publie — verifie le 7 aout 2026 : `tennis-data.co.uk` ne sert ni l'une ni
    les autres et retarde d'une semaine sur le tournoi en cours, et les CSV de
    Jeff Sackmann, qui les portaient, ont disparu de GitHub.
    """
    settings = settings or get_settings()
    fragments = []
    for player in (home, away):
        if not player:
            continue
        faced = load_for(player, competition_id, commence_time, settings).opponents
        if not faced:
            continue
        noms = ", ".join(_with_elo(nom, oddsapi_key, settings) for nom in faced)
        fragments.append(f"{player} {noms}")
    return [("Parcours", " | ".join(fragments))] if fragments else []


def _with_elo(name: str, oddsapi_key: str | None, settings: Settings) -> str:
    """`Krejcikova (1905)` — le nom, et son Elo quand le rapprochement est sur."""
    row = elo.lookup(name, elo.tour_for(oddsapi_key), settings)
    rating = (row or {}).get("elo")
    return f"{name} ({int(rating)})" if rating else name


def _parse(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(UTC)
    except ValueError:
        return None
