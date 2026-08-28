"""Saisie groupee : un marche, plusieurs matchs, rien que des nombres a taper.

Un bookmaker affiche ses cotes par marche : une page « vainqueur du match »
pour vingt matchs, une autre « les deux joueurs gagnent un set » pour les memes
vingt. La fiche evenement fait l'inverse — tous les marches d'un seul match — et
relever un ecran de bookmaker avec elle demanderait vingt allers-retours.

Cette vue-ci epouse la forme de la source : une ligne par match, une colonne par
issue, dans l'ordre chronologique qui est celui du bookmaker. On lit de haut en
bas et on ne tape que des nombres.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Protocol
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .labels import affiche
from .manual import MANUAL_BOOKMAKER, MANUAL_MARKET, ManualError
from .matching import normalize
from .render import LABEL_WIDTH, MARKET_ORDER_BY_SPORT, is_price
from .session import has_started

logger = logging.getLogger(__name__)

#: Cles **et libelles** des marches que le rendu compact modelise deja. Une
#: saisie ne peut usurper ni les uns ni les autres : sous un meme libelle, ses
#: cotes se melangeraient a un releve d'API et plus rien ne dirait laquelle
#: vient d'ou. Reserver la cle seule ne suffit pas — `h2h` s'affiche
#: « Vainqueur », et c'est ce mot-la que l'utilisateur a envie de taper.
RESERVED_MARKETS = frozenset(
    word.lower()
    for order in MARKET_ORDER_BY_SPORT.values()
    for key, label in order
    for word in (key, label)
    if key != MANUAL_MARKET
)

#: Largeur utile de la colonne de libelles du rendu compact, moins l'espace qui
#: separe le libelle de sa valeur. Au-dela, le prompt tronquerait en silence et
#: « 2 joueurs 1 set » deviendrait « 2 joueurs 1 » : autant le dire a la saisie.
MAX_MARKET_LENGTH = LABEL_WIDTH - 1

#: Deux colonnes couvrent le tennis (deux joueurs, ou oui/non) ; trois couvrent
#: le 1N2 du football. Au-dela la grille cesse d'etre lisible.
MAX_OUTCOMES = 4


@dataclass
class GridRow:
    """Un match de la shortlist, tel qu'il apparait dans la grille."""

    event_id: int
    home: str
    away: str
    competition: str
    local_time: datetime
    #: Le match a commence. La ligne reste saisissable — on releve parfois apres
    #: coup — mais elle est signalee : ses cotes n'iront dans aucun prompt.
    started: bool = False

    @property
    def affiche(self) -> str:
        return affiche(self.home, self.away)

    def outcome_names(self, labels: list[str]) -> list[str]:
        """Libelles des colonnes : ceux fournis, ou les participants a defaut."""
        if labels:
            return labels
        return [name for name in (self.home, self.away) if name]


@dataclass
class GridView:
    """Grille prete a afficher."""

    session_id: int
    label: str
    rows: list[GridRow] = field(default_factory=list)

    @property
    def count(self) -> int:
        return len(self.rows)


@dataclass
class GridResult:
    """Bilan d'une saisie groupee."""

    market: str = ""
    written: int = 0
    events: int = 0
    removed: int = 0
    rejected: list[str] = field(default_factory=list)


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def parse_outcome_labels(raw: str) -> list[str]:
    """`Oui, Non` -> ['Oui', 'Non']. Vide : les participants du match serviront."""
    labels = [part.strip() for part in (raw or "").split(",")]
    return [label for label in labels if label][:MAX_OUTCOMES]


def market_key(label: str) -> str:
    """Cle de marche pour une saisie. Un libelle vide retombe sur `outright`."""
    key = (label or "").strip()
    if not key:
        return MANUAL_MARKET
    if len(key) > MAX_MARKET_LENGTH:
        raise ManualError(
            f"Nom de marche trop long : {MAX_MARKET_LENGTH} caracteres maximum, "
            "c'est la largeur de la colonne du prompt."
        )
    if key.lower() in RESERVED_MARKETS:
        raise ManualError(
            f"« {key} » est un marche servi par l'API : choisis un autre nom pour "
            "ne pas melanger une saisie a un releve de marche."
        )
    return key


def parse_price(raw: str) -> float | None:
    """Lit une cote seule — `2.13` ou `2,13`. None si la cellule est vide ou illisible.

    Volontairement distinct de `manual.parse_odds`, qui lit `nom cote` : applique
    ici, son motif verrait « 2. » comme un nom et « 13 » comme la cote.
    """
    text = (raw or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        price = float(text)
    except ValueError:
        return None
    return price if is_price(price) else None


def build_view(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> GridView:
    """Matchs de la session, dans l'ordre chronologique — celui du bookmaker.

    Aucun match n'est retire : l'import des picks s'appuie sur cette meme liste,
    et il arrive apres les matchs. Ceux qui ont commence sont signales.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        label_row = conn.execute(
            "SELECT label FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        rows = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, "
            "       COALESCE(c.label, '—') AS competition "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time, e.id",
            (session_id,),
        ).fetchall()

    return GridView(
        session_id=session_id,
        label=(label_row["label"] if label_row else "") or f"Session {session_id}",
        rows=[
            GridRow(
                event_id=int(row["id"]),
                home=row["home"],
                away=row["away"] or "",
                competition=row["competition"],
                local_time=_local(row["commence_time"], settings.tz),
                started=has_started(row["commence_time"], now),
            )
            for row in rows
        ],
    )


def save_grid(
    session_id: int,
    market: str,
    outcome_labels: list[str],
    cells: dict[str, str],
    replace: bool = False,
    settings: Settings | None = None,
) -> GridResult:
    """Ecrit les cotes de la grille. `cells` est indexe par `<event_id>:<colonne>`.

    Ne touche que les lignes du bookmaker `manual` et du marche vise : les cotes
    d'API restent intactes, et les autres saisies aussi.
    """
    settings = settings or get_settings()
    key = market_key(market)
    view = build_view(session_id, settings)
    result = GridResult(market=key)

    if not view.rows:
        raise ManualError("La shortlist est vide : coche des matchs avant de saisir.")

    with connect(settings) as conn:
        for row in view.rows:
            names = row.outcome_names(outcome_labels)
            written_here = 0
            for index, name in enumerate(names):
                raw = cells.get(f"{row.event_id}:{index}", "")
                if not (raw or "").strip():
                    continue
                price = parse_price(raw)
                if price is None:
                    result.rejected.append(f"{row.affiche} · {name} : « {raw.strip()} »")
                    continue
                # Une ressaisie met la cote a jour au lieu de la dupliquer.
                conn.execute(
                    "DELETE FROM odds WHERE event_id = ? AND bookmaker = ? AND market_key = ? "
                    "AND outcome_name = ?",
                    (row.event_id, MANUAL_BOOKMAKER, key, name),
                )
                conn.execute(
                    "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, "
                    "                  fetched_at) VALUES (?, ?, ?, ?, ?, ?)",
                    (row.event_id, MANUAL_BOOKMAKER, key, name, price, utcnow()),
                )
                written_here += 1

            if written_here:
                result.written += written_here
                result.events += 1
            elif replace:
                cursor = conn.execute(
                    "DELETE FROM odds WHERE event_id = ? AND bookmaker = ? AND market_key = ?",
                    (row.event_id, MANUAL_BOOKMAKER, key),
                )
                result.removed += cursor.rowcount

    if not result.written and not result.rejected and not result.removed:
        raise ManualError("Aucune cote saisie : remplis au moins une cellule.")

    logger.info(
        "Saisie groupee « %s » sur la session %d : %d cote(s) sur %d match(s), %d refusee(s)",
        key,
        session_id,
        result.written,
        result.events,
        len(result.rejected),
    )
    return result


# -- Collage depuis un bookmaker --------------------------------------------

#: Une cote collee : `2,03` ou `2.03`. Volontairement stricte sur la decimale,
#: pour ne pas confondre une cote avec un horaire, un compteur de paris ou un
#: numero de tour.
PASTED_PRICE = re.compile(r"^\d+[.,]\d{1,2}$")

#: Un nom de famille suffit a reconnaitre un match ; les jetons restants
#: affinent. En dessous, la ligne n'ancre rien.
MIN_ANCHOR_SCORE = 0.6

#: Ecart minimal avec le deuxieme candidat. Deux participants trop proches
#: doivent rester non reconnus plutot que d'etre attribues au hasard.
MIN_ANCHOR_GAP = 0.1


class Matchable(Protocol):
    """Ce dont `anchor` a besoin : un identifiant et deux participants."""

    event_id: int
    home: str
    away: str


@dataclass
class PasteResult:
    """Ce qu'un collage a permis de reconnaitre. N'ecrit rien : il pre-remplit."""

    #: Cellules pretes pour la grille, indexees `<event_id>:<colonne>`.
    cells: dict[str, str] = field(default_factory=dict)
    recognised: list[str] = field(default_factory=list)
    missing: list[str] = field(default_factory=list)
    orphan_prices: int = 0

    @property
    def filled(self) -> int:
        return len(self.cells)


def _anchor_score(line_tokens: set[str], participant: str) -> float:
    """A quel point cette ligne designe ce participant.

    Le nom de famille pese l'essentiel : un bookmaker abrege les prenoms
    (« S. Baez ») mais jamais le nom. Les jetons restants departagent.
    """
    tokens = [token for token in normalize(participant).split() if len(token) >= 3]
    if not tokens:
        return 0.0
    surname = tokens[-1]
    if surname not in line_tokens:
        return 0.0
    others = tokens[:-1]
    if not others:
        return 1.0
    found = sum(1 for token in others if token in line_tokens)
    return MIN_ANCHOR_SCORE + (1.0 - MIN_ANCHOR_SCORE) * (found / len(others))


def anchor(line: str, rows: Sequence[Matchable]) -> Matchable | None:
    """Le match que designe cette ligne, ou None si le doute subsiste.

    Sert au collage de cotes comme a l'import des picks : dans les deux cas on
    reconnait un match dans du texte ecrit par quelqu'un d'autre. Les lignes
    proposees ne sont pas toujours celles de la grille — l'import des picks
    cherche aussi hors de la shortlist — d'ou le protocole plutot que `GridRow`.
    """
    line_tokens = set(normalize(line).split())
    if not line_tokens:
        return None

    scored: list[tuple[float, Matchable]] = []
    for row in rows:
        score = max(_anchor_score(line_tokens, name) for name in (row.home, row.away or row.home))
        if score >= MIN_ANCHOR_SCORE:
            scored.append((score, row))

    if not scored:
        return None
    scored.sort(key=lambda pair: pair[0], reverse=True)
    if len(scored) > 1 and scored[0][0] - scored[1][0] < MIN_ANCHOR_GAP:
        # Deux matchs se disputent la ligne : ne pas deviner.
        return None
    return scored[0][1]


def parse_paste(raw: str, rows: list[GridRow], outcome_count: int = 2) -> PasteResult:
    """Lit un bloc copie sur la page d'un bookmaker et pre-remplit la grille.

    Le format d'un bookmaker n'est pas un contrat : on ne suppose rien d'autre
    que « des noms et des nombres, dans cet ordre ». Chaque ligne qui designe un
    match ouvre son groupe de cotes ; les nombres suivants le remplissent.

    Ne se fie jamais a l'ordre d'affichage du bookmaker, qui n'est pas celui de
    la grille : l'ancrage se fait par le nom, et ce qui n'est pas reconnu est
    signale plutot qu'attribue au petit bonheur.
    """
    result = PasteResult()
    if not rows:
        return result

    current: GridRow | None = None
    filled = 0
    for line in (raw or "").splitlines():
        text = line.strip()
        if not text:
            continue

        if PASTED_PRICE.match(text):
            price = parse_price(text)
            if price is None:
                continue
            if current is None or filled >= outcome_count:
                result.orphan_prices += 1
                continue
            result.cells[f"{current.event_id}:{filled}"] = f"{price:.2f}"
            filled += 1
            continue

        found = anchor(text, rows)
        # Re-designer le match courant (le bookmaker repete les noms en libelles
        # d'issue) ne redemarre pas son groupe : seul un autre match le fait.
        if found is not None and found is not current:
            current = found
            filled = 0

    seen = {row.event_id for row in rows if f"{row.event_id}:0" in result.cells}
    result.recognised = [row.affiche for row in rows if row.event_id in seen]
    result.missing = [row.affiche for row in rows if row.event_id not in seen]
    return result
