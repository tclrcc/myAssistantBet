"""Journees de tournoi : regrouper les matchs comme le tournoi les joue.

Une journee civile ne decrit pas une journee de tournoi. A Montreal, la session
du soir commence vers 19h locales, soit 01h a Paris : le match s'affiche
« demain » alors qu'il appartient a la soiree de la veille. A Melbourne c'est
l'inverse : un match a 01h a Paris ouvre la journee du jour meme.

**Une heure de bascule fixe reglerait l'un en cassant l'autre.** Les matchs d'un
tournoi se regroupent donc par **trou horaire** : un ecart de plus de
`GAP_HOURS` separe deux journees, en deca c'est la meme. La journee prend la
date locale de son **premier** match. Aucun fuseau a stocker, aucune table a
tenir a jour, et la regle vaut pour les deux hemispheres.

Verifie sur les donnees reelles du Canadian Open : le dernier match d'une
journee part a 23h10 UTC et le premier de la session de nuit a 00h10 UTC, soit
une heure d'ecart — la meme journee. Le trou suivant, entre la fin de nuit et
la reprise de l'apres-midi, depasse dix heures.

Le regroupement est **par competition** : deux tournois joues sur deux
continents n'ont aucune raison de partager leurs trous horaires.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from zoneinfo import ZoneInfo

#: Au-dela, c'est une autre journee de tournoi. Six heures separent nettement la
#: fin d'une session de nuit de la reprise du lendemain, sans jamais couper une
#: session en deux — le plus grand trou constate a l'interieur d'une journee
#: tient en trois heures.
GAP_HOURS = 6


@dataclass(frozen=True)
class Day:
    """Une journee de tournoi proposee au filtre."""

    #: Date locale du premier match de la journee, en ISO (`2026-08-04`).
    key: str
    #: Ce que l'utilisateur lit : « aujourd'hui · 04/08 ».
    label: str
    count: int


def parse(value: str | None) -> datetime | None:
    """Lit un horodatage ISO en UTC. Renvoie None si la valeur est illisible."""
    if not value:
        return None
    try:
        moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def day_keys(
    events: Iterable[tuple[int, object, str]],
    tz: str,
    gap_hours: int = GAP_HOURS,
) -> dict[int, str]:
    """Associe a chaque evenement la date de la journee de tournoi qui le porte.

    `events` est une suite de `(event_id, competition_key, commence_time)`. Les
    evenements dont l'horodatage est illisible sont ignores plutot que ranges
    au hasard : une journee fausse serait pire qu'un match absent du filtre.
    """
    zone = ZoneInfo(tz)
    gap = timedelta(hours=gap_hours)

    par_competition: dict[object, list[tuple[int, datetime]]] = {}
    for event_id, competition, commence_time in events:
        moment = parse(commence_time)
        if moment is None:
            continue
        par_competition.setdefault(competition, []).append((event_id, moment))

    keys: dict[int, str] = {}
    for matchs in par_competition.values():
        matchs.sort(key=lambda item: (item[1], item[0]))
        debut: datetime | None = None
        precedent: datetime | None = None
        for event_id, moment in matchs:
            if debut is None or precedent is None or moment - precedent > gap:
                debut = moment
            keys[event_id] = debut.astimezone(zone).date().isoformat()
            precedent = moment
    return keys


def options(keys: Iterable[str], tz: str, now: datetime | None = None) -> list[Day]:
    """Journees proposees au filtre, de la plus proche a la plus lointaine.

    Le compte accompagne chaque journee : sans lui, choisir une date revient a
    tenter sa chance. « aujourd'hui » et « demain » sont nommes parce que ce
    sont les deux seuls choix qu'on fait sans reflechir.
    """
    zone = ZoneInfo(tz)
    moment = (now or datetime.now(UTC)).astimezone(zone)
    aujourdhui = moment.date()

    comptes: dict[str, int] = {}
    for key in keys:
        comptes[key] = comptes.get(key, 0) + 1

    jours: list[Day] = []
    for key in sorted(comptes):
        try:
            jour = date.fromisoformat(key)
        except ValueError:
            continue
        ecart = (jour - aujourdhui).days
        nom = {-1: "hier", 0: "aujourd'hui", 1: "demain"}.get(ecart)
        libelle = jour.strftime("%d/%m")
        jours.append(
            Day(
                key=key,
                label=f"{nom} · {libelle}" if nom else libelle,
                count=comptes[key],
            )
        )
    return jours
