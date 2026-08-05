"""Gestion des competitions scannees.

Le tennis n'est couvert par The Odds API que pendant les tournois, et les cles de
competition changent d'une saison a l'autre. Plutot que de figer une liste dans
une migration, on la synchronise depuis `GET /sports` — **endpoint gratuit**
(SPEC.md section 4), donc sans consequence sur le quota.

Une competition decouverte est creee **inactive** : rien ne se met a couter des
credits sans une decision explicite.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from ..config import Settings, get_settings
from ..db import connect
from ..providers.oddsapi import OddsAPIClient
from .labels import sort_key, sport_emoji

logger = logging.getLogger(__name__)

#: Prefixes de cles The Odds API par sport interne. Le cyclisme n'est pas couvert.
SPORT_PREFIXES = {"soccer_": "football", "tennis_": "tennis"}

#: Surfaces de tennis. Elles decident quel Elo de surface est rendu dans le
#: bloc CONTEXTE ; laissee vide, seul l'Elo general apparait. Aucune deduction
#: automatique depuis le libelle du tournoi : ce serait une invention.
SURFACES = {"hard": "Dur", "clay": "Terre battue", "grass": "Gazon"}

#: Niveaux de tournoi, du plus releve au plus modeste. Un Grand Chelem se joue
#: au meilleur des cinq manches chez les hommes sur un tableau de 128 ; un 250
#: se joue en deux manches gagnantes sur un tableau de 28. Melanger leurs taux
#: de reussite produit un chiffre qui ne decrit ni l'un ni l'autre.
#:
#: `masters_1000` couvre les Masters 1000 de l'ATP **et** les WTA 1000 : c'est
#: le meme etage de la hierarchie, et le circuit se lit deja dans le libelle.
#: Les separer diviserait par deux des echantillons deja courts.
CATEGORIES = {
    "grand_slam": "Grand Chelem",
    "finals": "Masters de fin d'année",
    "masters_1000": "Masters 1000",
    "level_500": "ATP/WTA 500",
    "level_250": "ATP/WTA 250",
    "challenger": "Challenger / WTA 125",
    "itf": "ITF",
}

#: Rang d'affichage d'un niveau. Une competition sans niveau ferme la marche
#: plutot que de s'intercaler au hasard.
CATEGORY_ORDER = {key: index for index, key in enumerate(CATEGORIES)}


def category_label(key: str | None) -> str:
    """Libelle d'un niveau, chaine vide s'il n'est pas renseigne."""
    return CATEGORIES.get(key or "", "")


def category_rank(key: str | None) -> int:
    """Rang de tri d'un niveau. Les non renseignes passent en dernier."""
    return CATEGORY_ORDER.get(key or "", len(CATEGORIES))


@dataclass
class SyncReport:
    """Bilan d'une synchronisation depuis `/sports`."""

    created: list[str] = field(default_factory=list)
    updated: list[str] = field(default_factory=list)
    ignored: int = 0
    #: Competitions que l'API ne sert pas en ce moment — hors saison, ou pas
    #: encore ouvertes aux paris. Elles existent et peuvent etre activees
    #: d'avance : le jour ou les cotes arrivent, le scan les prend.
    dormant: int = 0

    @property
    def total(self) -> int:
        return len(self.created) + len(self.updated)


def _sport_key_for(oddsapi_key: str) -> str | None:
    for prefix, sport in SPORT_PREFIXES.items():
        if oddsapi_key.startswith(prefix):
            return sport
    return None


def list_all(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Toutes les competitions, actives d'abord, pour l'ecran de gestion.

    Rangees ensuite par sport puis **par niveau** : les Grands Chelems avant les
    Masters 1000, avant les 500. Sur quarante tournois de tennis, l'ordre
    alphabetique melangeait un Grand Chelem et un 500 sans que rien ne le dise.
    """
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.oddsapi_key, c.apifootball_league_id, c.priority, "
            "       c.active, c.api_active, c.notes, c.surface, c.category, "
            "       s.id AS sport_order, s.key AS sport_key, s.label AS sport_label "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id"
        ).fetchall()
    competitions = [
        {
            **dict(row),
            "sport_emoji": sport_emoji(row["sport_key"]),
            "category_label": category_label(row["category"]),
        }
        for row in rows
    ]
    competitions.sort(
        key=lambda row: (
            not row["active"],
            row["sport_order"],
            category_rank(row["category"]),
            -row["priority"],
            sort_key(row["label"]),
        )
    )
    return competitions


def set_active(competition_id: int, active: bool, settings: Settings | None = None) -> None:
    """Active ou desactive une competition. Seules les actives sont scannees."""
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET active = ? WHERE id = ?", (1 if active else 0, competition_id)
        )


def set_notes(competition_id: int, notes: str, settings: Settings | None = None) -> None:
    """Enregistre la fiche d'une competition : format, phase, enjeu, particularites.

    Ce texte entre tel quel dans le prompt, une fois par lot. Il tient lieu de
    ce qu'aucune API ne donne — qu'une coupe se joue en aller-retour, qu'un
    championnat vient de reprendre, qu'une competition se dispute a huis clos.
    Vide, la fiche disparait plutot que d'occuper une ligne pour rien.
    """
    cleaned = (notes or "").strip()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET notes = ? WHERE id = ?",
            (cleaned or None, competition_id),
        )
    logger.info(
        "Fiche de competition %d : %s",
        competition_id,
        f"{len(cleaned)} caracteres" if cleaned else "effacee",
    )


def set_surface(competition_id: int, surface: str, settings: Settings | None = None) -> None:
    """Fixe la surface d'une competition de tennis.

    Une valeur inconnue est traitee comme « non renseignee » plutot que refusee :
    le seul effet est de ne rendre que l'Elo general, ce qui n'a rien de grave.
    """
    value = (surface or "").strip().lower()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET surface = ? WHERE id = ?",
            (value if value in SURFACES else None, competition_id),
        )
    logger.info("Surface de la competition %d : %s", competition_id, value or "non renseignee")


def set_category(competition_id: int, category: str, settings: Settings | None = None) -> None:
    """Fixe le niveau d'une competition.

    Comme la surface, elle se saisit a la main : deduire « Masters 1000 » du mot
    « Masters » dans un libelle marcherait pour Monte-Carlo et se tromperait sur
    le Masters de fin d'annee. Une valeur inconnue vaut « non renseigne » plutot
    qu'une erreur : le seul effet est une ligne de moins dans les statistiques.
    """
    value = (category or "").strip().lower()
    with connect(settings) as conn:
        conn.execute(
            "UPDATE competitions SET category = ? WHERE id = ?",
            (value if value in CATEGORIES else None, competition_id),
        )
    logger.info("Niveau de la competition %d : %s", competition_id, value or "non renseigne")


async def sync_from_api(client: OddsAPIClient, settings: Settings | None = None) -> SyncReport:
    """Aligne la table `competitions` sur le catalogue **complet** de The Odds API.

    Gratuit : `/sports` ne consomme aucun credit. N'active jamais rien de
    lui-meme et ne desactive jamais une competition existante.

    `include_inactive` est indispensable : sans lui, seules les competitions
    que le fournisseur sert a l'instant sont decouvertes. Une phase de
    qualification europeenne ou un tournoi qui ouvre dans trois jours reste
    alors introuvable jusqu'a ce que les cotes arrivent — donc trop tard pour
    l'activer avant les premiers matchs.
    """
    settings = settings or get_settings()
    report = SyncReport()

    sports = await client.get_sports(include_inactive=True)
    with connect(settings) as conn:
        sport_ids = {
            row["key"]: int(row["id"]) for row in conn.execute("SELECT id, key FROM sports")
        }

        for entry in sports:
            oddsapi_key = entry.get("key")
            title = entry.get("title") or oddsapi_key
            if not oddsapi_key:
                continue
            sport_key = _sport_key_for(oddsapi_key)
            if sport_key is None or sport_key not in sport_ids:
                report.ignored += 1
                continue

            served = 1 if entry.get("active") else 0
            if not served:
                report.dormant += 1

            existing = conn.execute(
                "SELECT id, label, api_active FROM competitions WHERE oddsapi_key = ?",
                (oddsapi_key,),
            ).fetchone()
            if existing is None:
                conn.execute(
                    "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active, "
                    "                          api_active) VALUES (?, ?, ?, 0, 0, ?)",
                    (sport_ids[sport_key], oddsapi_key, title, served),
                )
                report.created.append(f"{title} ({oddsapi_key})")
            elif existing["label"] != title or existing["api_active"] != served:
                # Le libelle et la disponibilite du fournisseur font foi ;
                # l'activation choisie par l'utilisateur ne bouge jamais.
                conn.execute(
                    "UPDATE competitions SET label = ?, api_active = ? WHERE id = ?",
                    (title, served, existing["id"]),
                )
                if existing["label"] != title:
                    report.updated.append(f"{title} ({oddsapi_key})")

    logger.info(
        "Synchronisation des competitions : %d creees, %d mises a jour, %d ignorees, "
        "%d non servies actuellement",
        len(report.created),
        len(report.updated),
        report.ignored,
        report.dormant,
    )
    return report
