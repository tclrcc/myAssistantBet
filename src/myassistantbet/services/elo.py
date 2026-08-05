"""Classements Elo tennis : stockage local, rapprochement des noms, rendu.

Le football recoit forme, classement, absents et confrontations directes ; le
tennis n'avait rien, et son bloc CONTEXTE restait vide. Les classements Elo
publies par Tennis Abstract comblent ce trou pour un cout nul.

Deux temps separes, comme pour le contexte football :

- `refresh()` interroge les deux rapports et **persiste les lignes brutes** ;
- `lines()` relit la base. Regenerer un prompt ne declenche aucun appel.

**Aucune conversion, jamais.** Un ecart d'Elo se traduit en probabilite de
victoire — la page source donne meme la table de correspondance — et cette
probabilite, rapprochee d'une cote, est exactement le calcul que la section 9
interdit. On collecte et on affiche des ratings bruts ; ce que Claude a le
droit d'en faire est cadre par le template de prompt.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError
from ..providers.tennisabstract import REPORTS, TennisAbstractClient
from .matching import similarity

logger = logging.getLogger(__name__)

#: Les ratings sont recalcules une fois par semaine. Rafraichir plus souvent
#: n'apporterait rien ; moins souvent ferait rater un mouvement de forme.
MAX_AGE_HOURS = 24

#: Similarite minimale pour rapprocher un nom de joueur. Plus severe que pour
#: les clubs : attribuer a un joueur le rating d'un autre serait pire que de
#: n'afficher aucune ligne, et rien ici n'a de resolution manuelle.
MIN_SCORE = 0.88
#: Ecart minimal avec le second candidat, pour ne pas trancher entre deux
#: freres ou deux homonymes.
MIN_GAP = 0.06

#: Champs par surface. La surface est portee par la competition : la deviner
#: d'apres un libelle de tournoi serait une invention.
SURFACES = {
    "hard": ("dur", "hard_elo", "hard_rank"),
    "clay": ("terre", "clay_elo", "clay_rank"),
    "grass": ("gazon", "grass_elo", "grass_rank"),
}

FIELDS = (
    "player",
    "elo_rank",
    "elo",
    "hard_rank",
    "hard_elo",
    "clay_rank",
    "clay_elo",
    "grass_rank",
    "grass_elo",
    "peak_elo",
    "peak_month",
    "tour_rank",
)


@dataclass
class RefreshReport:
    """Bilan d'un rafraichissement. Aucun credit n'est consomme."""

    counts: dict[str, int] = field(default_factory=dict)
    errors: list[str] = field(default_factory=list)
    skipped: bool = False

    @property
    def total(self) -> int:
        return sum(self.counts.values())

    @property
    def ok(self) -> bool:
        return not self.errors


def normalize(name: str) -> str:
    """Minuscules, accents retires, ponctuation retiree.

    Volontairement distincte de `matching.normalize`, qui retire les jetons de
    club et les chiffres aux extremites : appliquee a un nom de joueur, elle
    abimerait « Alex de Minaur » ou « Juan Carlos Ferrero ».
    """
    decomposed = unicodedata.normalize("NFKD", name or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def tour_for(oddsapi_key: str | None) -> str | None:
    """`tennis_atp_canadian_open` -> `atp`. None si la cle ne dit rien."""
    key = (oddsapi_key or "").lower()
    for tour in REPORTS:
        if f"_{tour}_" in key or key.endswith(f"_{tour}"):
            return tour
    return None


# -- Persistance ------------------------------------------------------------


def store(
    tour: str,
    players: list[dict[str, Any]],
    settings: Settings | None = None,
    moment: datetime | None = None,
) -> int:
    """Remplace le classement d'un circuit. Renvoie le nombre de lignes ecrites.

    Remplacement complet et non fusion : un joueur sorti du classement — moins
    de dix matchs sur 52 semaines — doit disparaitre, sinon son rating vieillit
    en base sans que rien ne le signale.

    `moment` doit etre celui qui sert a juger la fraicheur : horodater avec
    l'horloge reelle une recuperation datee autrement rendrait `is_stale()`
    incoherent avec ce qui est ecrit.
    """
    stamp = moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if moment else utcnow()
    rows = []
    for entry in players:
        key = normalize(str(entry.get("player") or ""))
        if not key:
            continue
        rows.append((tour, key, *[entry.get(name) for name in FIELDS], stamp))

    with connect(settings) as conn:
        conn.execute("DELETE FROM tennis_elo WHERE tour = ?", (tour,))
        conn.executemany(
            f"INSERT OR REPLACE INTO tennis_elo (tour, normalized, {', '.join(FIELDS)}, "
            f"fetched_at) VALUES ({', '.join('?' * (len(FIELDS) + 3))})",
            rows,
        )
    return len(rows)


def last_fetch(tour: str, settings: Settings | None = None) -> str | None:
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT MAX(fetched_at) AS moment FROM tennis_elo WHERE tour = ?", (tour,)
        ).fetchone()
    return row["moment"] if row and row["moment"] else None


def is_stale(tour: str, settings: Settings | None = None, now: datetime | None = None) -> bool:
    """Vrai si le classement manque ou date de plus de `MAX_AGE_HOURS`."""
    moment = last_fetch(tour, settings)
    if not moment:
        return True
    try:
        fetched = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return True
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    reference = now or datetime.now(UTC)
    return (reference - fetched).total_seconds() > MAX_AGE_HOURS * 3600


# -- Recuperation -----------------------------------------------------------


async def refresh(
    client: TennisAbstractClient,
    settings: Settings | None = None,
    tours: tuple[str, ...] = ("atp", "wta"),
    force: bool = False,
    now: datetime | None = None,
) -> RefreshReport:
    """Met a jour les classements Elo. Gratuit, donc sans garde-fou de quota.

    Un circuit en echec n'empeche jamais l'autre d'aboutir, et un echec total
    laisse simplement le bloc CONTEXTE sans ligne Elo — comme avant.
    """
    settings = settings or get_settings()
    report = RefreshReport()

    for tour in tours:
        if not force and not is_stale(tour, settings, now):
            continue
        try:
            players = await client.elo_ratings(tour)
        except ProviderError as exc:
            report.errors.append(f"{tour} : {exc}")
            logger.warning("Classement Elo %s indisponible : %s", tour, exc)
            continue
        report.counts[tour] = store(tour, players, settings, now)

    report.skipped = not report.counts and not report.errors
    if report.counts:
        logger.info(
            "Classements Elo mis a jour : %s",
            ", ".join(f"{tour} {count}" for tour, count in report.counts.items()),
        )
    return report


# -- Rapprochement ----------------------------------------------------------


def lookup(
    name: str,
    tour: str | None = None,
    settings: Settings | None = None,
) -> dict[str, Any] | None:
    """Ligne Elo d'un joueur, ou None si le rapprochement n'est pas sur.

    Correspondance exacte du nom normalise d'abord ; sinon le meilleur candidat,
    a condition d'etre bon **et** nettement detache. **En cas de doute on ne
    devine pas** : mieux vaut aucune ligne qu'un rating attribue au mauvais
    joueur, d'autant qu'il n'existe ici aucune resolution manuelle pour rattraper.
    """
    key = normalize(name)
    if not key:
        return None

    with connect(settings) as conn:
        if tour:
            rows = conn.execute("SELECT * FROM tennis_elo WHERE tour = ?", (tour,)).fetchall()
        else:
            # Evenement saisi a la main : aucune cle ne dit le circuit. Les deux
            # listes sont fouillees, un nom de joueur ne s'y trouvant qu'une fois.
            rows = conn.execute("SELECT * FROM tennis_elo").fetchall()

    if not rows:
        return None
    for row in rows:
        if row["normalized"] == key:
            return dict(row)

    scored = sorted(
        ((similarity(key, row["normalized"]), row) for row in rows),
        key=lambda item: -item[0],
    )
    best_score, best_row = scored[0]
    if best_score < MIN_SCORE:
        return None
    if len(scored) > 1 and best_score - scored[1][0] < MIN_GAP:
        logger.info("Elo ambigu pour « %s » : deux candidats trop proches", name)
        return None
    return dict(best_row)


def state(settings: Settings | None = None) -> list[dict[str, Any]]:
    """Etat des classements par circuit, pour l'ecran des competitions."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT tour, COUNT(*) AS players, MAX(fetched_at) AS fetched_at "
            "FROM tennis_elo GROUP BY tour ORDER BY tour"
        ).fetchall()
    return [dict(row) for row in rows]


def has_data(tour: str | None = None, settings: Settings | None = None) -> bool:
    """Vrai si un classement est disponible. Sans lui, aucune ligne n'est rendue."""
    with connect(settings) as conn:
        if tour:
            row = conn.execute(
                "SELECT 1 FROM tennis_elo WHERE tour = ? LIMIT 1", (tour,)
            ).fetchone()
        else:
            row = conn.execute("SELECT 1 FROM tennis_elo LIMIT 1").fetchone()
    return row is not None


# -- Rendu ------------------------------------------------------------------


def _fragment(name: str, row: dict[str, Any] | None, surface: str | None) -> str:
    """`Popyrin 1893 (#28) · dur 1901 (#25) · pic 1950 (2024-08)`."""
    if row is None:
        return f"{name} — non trouvé au classement Elo"

    parts = []
    if row.get("elo") is not None:
        rank = f" (#{row['elo_rank']})" if row.get("elo_rank") else ""
        parts.append(f"{row['elo']:.0f}{rank}")
    if surface in SURFACES:
        label, elo_field, rank_field = SURFACES[surface]
        if row.get(elo_field) is not None:
            rank = f" (#{row[rank_field]})" if row.get(rank_field) else ""
            parts.append(f"{label} {row[elo_field]:.0f}{rank}")
    if row.get("peak_elo") is not None:
        month = f" {row['peak_month']}" if row.get("peak_month") else ""
        parts.append(f"pic {row['peak_elo']:.0f}{month}")
    if row.get("tour_rank"):
        parts.append(f"classement {row['tour_rank']}e")

    return f"{name} " + " · ".join(parts) if parts else name


def lines(
    home: str,
    away: str,
    oddsapi_key: str | None = None,
    surface: str | None = None,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Lignes Elo du bloc CONTEXTE, pretes pour `render_event`.

    Rien n'est rendu tant qu'aucun classement n'a ete recupere : ecrire deux
    fois « non trouvé » sur une base vierge ferait chercher un probleme de
    rapprochement la ou il n'y a qu'un rafraichissement jamais lance. En
    revanche, si le classement existe et qu'un joueur n'y figure pas, la ligne
    le dit — c'est une information sur le joueur, pas un trou de collecte.
    """
    tour = tour_for(oddsapi_key)
    if not has_data(tour, settings):
        return []

    found = [lookup(name, tour, settings) for name in (home, away)]
    if not any(found):
        return []

    fragments = [
        _fragment(name, row, surface) for name, row in zip((home, away), found, strict=True)
    ]
    return [("Elo", "\n".join(fragments))]
