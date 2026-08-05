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
from .labels import sport_emoji

logger = logging.getLogger(__name__)

#: Prefixes de cles The Odds API par sport interne. Le cyclisme n'est pas couvert.
SPORT_PREFIXES = {"soccer_": "football", "tennis_": "tennis"}

#: Surfaces de tennis. Elles decident quel Elo de surface est rendu dans le
#: bloc CONTEXTE ; laissee vide, seul l'Elo general apparait. Aucune deduction
#: automatique depuis le libelle du tournoi : ce serait une invention.
SURFACES = {"hard": "Dur", "clay": "Terre battue", "grass": "Gazon"}


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
    """Toutes les competitions, actives d'abord, pour l'ecran de gestion."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT c.id, c.label, c.oddsapi_key, c.apifootball_league_id, c.priority, "
            "       c.active, c.api_active, c.notes, c.surface, "
            "       s.key AS sport_key, s.label AS sport_label "
            "FROM competitions c JOIN sports s ON s.id = c.sport_id "
            "ORDER BY c.active DESC, s.id, c.priority DESC, c.label"
        ).fetchall()
    return [{**dict(row), "sport_emoji": sport_emoji(row["sport_key"])} for row in rows]


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
