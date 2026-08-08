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
from datetime import UTC, date, datetime

from ..config import Settings, get_settings
from ..db import connect
from . import elo, tournament_day
from .labels import sort_key

logger = logging.getLogger(__name__)

#: Au-dela, on parle d'un autre tournoi ou d'une autre semaine : le repos
#: n'a plus de sens comme information de fraicheur.
MAX_DAYS = 10

#: Identifiant sentinelle du match analyse dans le regroupement en journees. Un
#: entier negatif ne peut collisionner avec aucune cle primaire d'evenement.
_ICI = -1


@dataclass
class Load:
    """Ce que la base sait du parcours d'un joueur dans ce tournoi."""

    rounds: int = 0
    days_rest: int | None = None
    #: Adversaires deja rencontres ici, du premier tour au dernier.
    opponents: tuple[str, ...] = ()
    #: Premiere journee de tournoi que nos scans ont vue, au format ISO. Ce que
    #: le tournoi a joue avant elle n'existe nulle part chez nous.
    first_day: str = ""

    @property
    def fragment(self) -> str:
        """`2j`, ou rien si aucun tour precedent n'est connu.

        Le nombre de tours **ne l'accompagne plus**. Il comptait les apparitions
        que nous avions scannees, pas les matchs joues : sur un tournoi dont les
        premiers jours precedent notre fenetre, il en manque. Constate en reel —
        le bloc creditait Michelsen d'un tour la ou l'ATP lui en donne deux. La
        ligne « Tour » dit desormais ou en est le tournoi, et elle le dit juste.
        """
        return f"{self.days_rest}j" if self.days_rest is not None else ""


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
        # Toute la competition, match du jour compris : le regroupement en
        # journees de tournoi a besoin de la suite pour placer ses coupures.
        toutes = conn.execute(
            "SELECT id, home, away, commence_time FROM events "
            "WHERE competition_id = ? ORDER BY commence_time",
            (competition_id,),
        ).fetchall()
    rows = [row for row in toutes if row["commence_time"] < commence_time]

    # **Le repos se compte en journees de tournoi, jamais en dates civiles.** A
    # Montreal, un match de la session du soir part a 01h du matin a Paris : sa
    # date civile est celle du lendemain, et le repos calcule dessus perdait un
    # jour d'un cote et en gagnait un de l'autre. Constate en reel — le bloc
    # donnait van de Zandschulp a 1j et Paul a 3j la ou l'ATP date leurs deux
    # matchs precedents du meme mercredi.
    #
    # Le match du jour entre dans le regroupement sous un identifiant sentinelle
    # plutot que d'y etre cherche : rien ne garantit qu'il figure en base — un
    # contexte peut se calculer sur une rencontre saisie a la main, ou pas
    # encore scannee — et l'y supposer faisait disparaitre la ligne entiere.
    journees = tournament_day.day_keys(
        [(int(row["id"]), competition_id, row["commence_time"]) for row in toutes]
        + [(_ICI, competition_id, commence_time)],
        settings.tz,
    )
    ici = journees.get(_ICI)

    when = _parse(commence_time)
    dates: list[datetime] = []
    veilles: list[str | None] = []
    faced: list[tuple[datetime, str]] = []
    for row in rows:
        if key not in (sort_key(row["home"]), sort_key(row["away"])):
            continue
        played = _parse(row["commence_time"])
        if when is None or played is None or (when - played).days > MAX_DAYS:
            continue
        dates.append(played)
        veilles.append(journees.get(int(row["id"])))
        # L'adversaire est l'autre nom de la ligne. Il est retenu tel qu'il a ete
        # scanne : c'est ainsi qu'il figure partout ailleurs dans l'application.
        autre = row["away"] if key == sort_key(row["home"]) else row["home"]
        if autre:
            faced.append((played, autre))

    if not dates or when is None:
        return Load()
    connues = [journees[int(row["id"])] for row in toutes if int(row["id"]) in journees]
    return Load(
        rounds=len(dates),
        days_rest=_rest(ici, [jour for jour in veilles if jour]),
        opponents=tuple(nom for _, nom in sorted(faced)),
        first_day=min(connues) if connues else "",
    )


def _rest(here: str | None, previous: list[str]) -> int | None:
    """Journees de tournoi entre la derniere apparition et celle du jour."""
    if not here or not previous:
        return None
    try:
        return (date.fromisoformat(here) - date.fromisoformat(max(previous))).days
    except ValueError:
        return None


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
    debut = ""
    for player in (home, away):
        if not player:
            continue
        charge = load_for(player, competition_id, commence_time, settings)
        debut = debut or charge.first_day
        if not charge.opponents:
            continue
        noms = ", ".join(_with_elo(nom, oddsapi_key, settings) for nom in charge.opponents)
        fragments.append(f"{player} {noms}")
    if not fragments:
        return []
    return [("Parcours", " | ".join(fragments) + _since(debut))]


def _since(first_day: str) -> str:
    """` [vu depuis le 04/08]` — la fenetre de nos scans, jamais celle du tournoi.

    La liste se lisait comme un parcours complet, et elle ne l'est pas : un
    tournoi commence avant notre fenetre de scan a des premiers tours que nous
    n'avons jamais vus. Constate en reel — le `Parcours` de Norrie omettait son
    premier tour contre Ugo Carabelli, joue la veille du premier jour scanne, et
    seule une recherche exterieure l'a rattrape.

    La date suffit a rendre le trou visible : comparee a « Tour », elle dit tout
    de suite si le debut du tableau manque. Compter les tours absents demanderait
    la taille du tableau, que rien ne donne.
    """
    try:
        jour = date.fromisoformat(first_day)
    except ValueError:
        return ""
    return f" [vu depuis le {jour.strftime('%d/%m')}]"


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
