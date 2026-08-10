"""Seuils numeriques regles par l'utilisateur.

Certaines regles du prompt et du rendu dependent d'un nombre qui n'est ni une
constante du projet ni une donnee : « a partir de combien de matchs un lot
porte-t-il deux combines », « a partir de quelle avancee de saison un enjeu de
classement veut-il dire quelque chose ». Ces nombres sont des **decisions de
l'utilisateur**, au meme titre que les bandes de cote ou de confiance, et les
coder en dur obligerait a redeployer pour changer d'avis.

Ils vivent donc dans `preferences`, la table cle/valeur qui porte deja les
consignes permanentes. Un registre les declare — libelle, defaut, bornes,
raison — pour que l'ecran des reglages les rende sans les connaitre un par un,
et pour qu'un seuil ajoute demain n'ait pas a toucher au gabarit.

**Une valeur illisible vaut le defaut**, jamais une erreur : un seuil est une
preference d'affichage, et refuser de servir une page parce qu'un nombre a ete
mal saisi serait hors de proportion.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class Threshold:
    """Un seuil reglable, et ce qu'il decide."""

    key: str
    label: str
    default: int
    low: int
    high: int
    #: Ce que le seuil change, en une phrase. Rendu sous le champ : un nombre
    #: sans son effet ne se regle pas, il se subit.
    note: str


#: Cle de preference : le prefixe evite qu'un seuil et une consigne de texte se
#: disputent un nom.
PREFIX = "seuil_"

THRESHOLDS: dict[str, Threshold] = {
    "combo_min_lot": Threshold(
        key="combo_min_lot",
        label="Lot minimum pour deux combinés",
        default=6,
        low=2,
        high=40,
        note=(
            "En dessous, le prompt ne demande qu'un seul combiné. Sur un lot de 5 matchs "
            "et un taux de sélection médian de 36 %, réclamer un combiné de 3-4 jambes "
            "et un second de 4-5 jambes — une seule sélection par match — était "
            "insatisfiable avant même que l'analyse commence."
        ),
    ),
    "combo_solo_min_lot": Threshold(
        key="combo_solo_min_lot",
        label="Lot minimum pour un combiné",
        default=5,
        low=2,
        high=20,
        note=(
            "En dessous, le prompt ne demande **aucun** combiné. Le seuil des deux combinés "
            "avait son symétrique manquant : sur un lot de 4 matchs et un taux de sélection "
            "médian de 36 %, l'espérance tourne autour de 1.4 sélection, quand la section D "
            "en réclame trois indépendantes. Mieux vaut supprimer la demande que faire écrire "
            "qu'elle était insatisfiable."
        ),
    ),
    "enjeu_min_journees": Threshold(
        key="enjeu_min_journees",
        label="Journées avant qu'un enjeu de classement compte",
        default=8,
        low=1,
        high=20,
        note=(
            "En dessous, la ligne « Enjeu » est datée et marquée « indicatif ». À la 3e "
            "journée sur 32, « Relegation Playoffs » décrit l'ordre alphabetique autant "
            "que le niveau — et le prompt ordonne de la recopier comme l'enjeu réel, sans "
            "recherche. Environ un quart d'une saison ordinaire."
        ),
    ),
}


def value_of(key: str, settings: Settings | None = None) -> int:
    """Valeur reglee d'un seuil, ou son defaut.

    Une valeur absente, illisible ou hors bornes rend le defaut : un seuil mal
    saisi doit degrader vers un comportement connu, pas casser une generation
    de prompt.
    """
    threshold = THRESHOLDS[key]
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT value FROM preferences WHERE key = ?", (PREFIX + key,)
        ).fetchone()
    if row is None:
        return threshold.default
    try:
        value = int(str(row["value"]).strip())
    except (TypeError, ValueError):
        logger.warning("Seuil %s illisible (%r) : valeur par defaut", key, row["value"])
        return threshold.default
    return value if threshold.low <= value <= threshold.high else threshold.default


def current(settings: Settings | None = None) -> list[tuple[Threshold, int]]:
    """Tous les seuils et leur valeur courante, pour l'ecran des reglages."""
    settings = settings or get_settings()
    return [(entry, value_of(key, settings)) for key, entry in THRESHOLDS.items()]


def save(key: str, raw: str, settings: Settings | None = None) -> None:
    """Enregistre un seuil. Hors bornes ou illisible, il revient au defaut.

    Le retour au defaut est **ecrit en base** plutot que laisse a la lecture :
    sans cela, le champ afficherait la valeur par defaut alors que la table
    porte encore la saisie refusee, et la prochaine relecture repartirait dessus.
    """
    if key not in THRESHOLDS:
        return
    threshold = THRESHOLDS[key]
    try:
        value = int((raw or "").strip())
    except (TypeError, ValueError):
        value = threshold.default
    value = value if threshold.low <= value <= threshold.high else threshold.default
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO preferences (key, value, updated_at) VALUES (?, ?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value = excluded.value, "
            "                               updated_at = excluded.updated_at",
            (PREFIX + key, str(value), utcnow()),
        )
    logger.info("Seuil %s : %d", key, value)
