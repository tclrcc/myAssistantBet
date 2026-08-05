"""Historique des sessions, saisie des picks joues et taux de reussite.

**Aucun calcul financier ici** (SPEC.md section 9) : ni ROI, ni value, ni CLV, ni
esperance. La mise est enregistree parce qu'elle fait partie du souvenir de ce
qui a ete joue, mais elle n'est jamais agregee ni transformee en indicateur.

Le seul indicateur produit est un taux de reussite : gagnes / (gagnes + perdus).
Les paris annules et ceux encore en attente sont exclus du denominateur.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

RESULTS = ("pending", "win", "loss", "void")
RESULT_LABELS = {
    "pending": "en attente",
    "win": "gagné",
    "loss": "perdu",
    "void": "annulé",
}
NO_SPORT = "—"


class HistoryError(ValueError):
    """Saisie de pick invalide. Le message est affiche tel quel."""


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


@dataclass
class SessionSummary:
    """Une session passee, vue de l'historique."""

    session_id: int
    label: str
    created_local: datetime
    events: int = 0
    prompts: int = 0
    picks: int = 0


@dataclass
class Pick:
    """Un pick joue, saisi a posteriori."""

    pick_id: int
    session_id: int
    event_id: int | None
    event_label: str
    tier: str
    tier_label: str
    market: str
    selection: str
    price: float | None
    confidence: int | None
    played: bool
    stake: float | None
    result: str

    @property
    def result_label(self) -> str:
        return RESULT_LABELS.get(self.result, self.result)


@dataclass
class RateRow:
    """Taux de reussite d'un regroupement. Aucune notion d'argent."""

    key: str
    label: str
    won: int = 0
    lost: int = 0
    void: int = 0
    pending: int = 0

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def total(self) -> int:
        return self.won + self.lost + self.void + self.pending

    @property
    def rate(self) -> float | None:
        """Taux de reussite, ou None tant que rien n'est tranche."""
        return None if self.settled == 0 else self.won / self.settled

    @property
    def rate_label(self) -> str:
        return "—" if self.rate is None else f"{self.rate * 100:.0f} %"


@dataclass
class Stats:
    """Taux de reussite par palier et par sport."""

    by_tier: list[RateRow] = field(default_factory=list)
    by_sport: list[RateRow] = field(default_factory=list)
    overall: RateRow = field(default_factory=lambda: RateRow("all", "Tous"))

    @property
    def empty(self) -> bool:
        return self.overall.total == 0


# -- Lecture ----------------------------------------------------------------


def list_sessions(settings: Settings | None = None) -> list[SessionSummary]:
    """Sessions passees, les plus recentes d'abord."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT s.id, s.label, s.created_at, "
            "  (SELECT COUNT(*) FROM session_events se WHERE se.session_id = s.id) AS events, "
            "  (SELECT COUNT(*) FROM prompts p WHERE p.session_id = s.id) AS prompts, "
            "  (SELECT COUNT(*) FROM picks k WHERE k.session_id = s.id) AS picks "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC"
        ).fetchall()

    return [
        SessionSummary(
            session_id=int(row["id"]),
            label=row["label"] or f"Session {row['id']}",
            created_local=_local(row["created_at"], settings.tz),
            events=int(row["events"]),
            prompts=int(row["prompts"]),
            picks=int(row["picks"]),
        )
        for row in rows
    ]


def list_prompts(session_id: int, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Prompts generes pour une session, du plus recent au plus ancien."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, template_name, token_estimate, created_at FROM prompts "
            "WHERE session_id = ? ORDER BY created_at DESC, id DESC",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "template_name": row["template_name"],
            "token_estimate": row["token_estimate"],
            "created_local": _local(row["created_at"], settings.tz),
        }
        for row in rows
    ]


def _tier_labels(conn) -> dict[str, str]:
    return {
        row["key"]: f"{row['emoji']} {row['label']}"
        for row in conn.execute("SELECT key, label, emoji FROM tiers")
    }


def list_picks(session_id: int, settings: Settings | None = None) -> list[Pick]:
    """Picks enregistres pour une session."""
    with connect(settings) as conn:
        labels = _tier_labels(conn)
        rows = conn.execute(
            "SELECT k.*, e.home, e.away FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "WHERE k.session_id = ? ORDER BY k.id",
            (session_id,),
        ).fetchall()

    picks = []
    for row in rows:
        if row["home"]:
            event_label = f"{row['home']} – {row['away']}" if row["away"] else row["home"]
        else:
            event_label = "combiné / hors match"
        picks.append(
            Pick(
                pick_id=int(row["id"]),
                session_id=int(row["session_id"]),
                event_id=row["event_id"],
                event_label=event_label,
                tier=row["tier"],
                tier_label=labels.get(row["tier"], row["tier"]),
                market=row["market"],
                selection=row["selection"],
                price=row["price"],
                confidence=row["confidence"],
                played=bool(row["played"]),
                stake=row["stake"],
                result=row["result"] or "pending",
            )
        )
    return picks


def session_events(session_id: int, settings: Settings | None = None) -> list[dict[str, Any]]:
    """Evenements de la session, pour le selecteur du formulaire de pick."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.home, e.away FROM session_events se "
            "JOIN events e ON e.id = se.event_id WHERE se.session_id = ? "
            "ORDER BY e.commence_time",
            (session_id,),
        ).fetchall()
    return [
        {
            "id": int(row["id"]),
            "label": f"{row['home']} – {row['away']}" if row["away"] else row["home"],
        }
        for row in rows
    ]


def tiers(settings: Settings | None = None) -> list[dict[str, str]]:
    """Paliers pour les formulaires. `emoji` et `name` restent separes du
    `label` complet : l'import des picks doit pouvoir reconnaitre l'un ou
    l'autre dans un tableau ecrit a la main."""
    with connect(settings) as conn:
        return [
            {
                "key": row["key"],
                "label": f"{row['emoji']} {row['label']}",
                "emoji": row["emoji"] or "",
                "name": row["label"],
            }
            for row in conn.execute("SELECT key, label, emoji FROM tiers ORDER BY position")
        ]


# -- Ecriture ---------------------------------------------------------------


def _as_float(value: str, field_name: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    if not text:
        return None
    try:
        return float(text)
    except ValueError as exc:
        raise HistoryError(f"« {field_name} » doit être un nombre.") from exc


def add_pick(
    session_id: int,
    tier: str,
    market: str,
    selection: str,
    *,
    event_id: str = "",
    price: str = "",
    confidence: str = "",
    stake: str = "",
    played: bool = True,
    result: str = "pending",
    settings: Settings | None = None,
) -> int:
    """Enregistre un pick joue. Renvoie son id."""
    settings = settings or get_settings()
    if not market.strip():
        raise HistoryError("« Marché » est obligatoire.")
    if not selection.strip():
        raise HistoryError("« Sélection » est obligatoire.")
    if result not in RESULTS:
        raise HistoryError(f"Résultat inconnu : {result}")

    price_value = _as_float(price, "Cote")
    stake_value = _as_float(stake, "Mise")
    confidence_value = _as_float(confidence, "Confiance")
    if confidence_value is not None and not 1 <= confidence_value <= 5:
        raise HistoryError("« Confiance » doit être comprise entre 1 et 5.")

    with connect(settings) as conn:
        known = {row["key"] for row in conn.execute("SELECT key FROM tiers")}
        if tier not in known:
            raise HistoryError(f"Palier inconnu : {tier}")

        cursor = conn.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, stake, result, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                int(event_id) if str(event_id).strip().isdigit() else None,
                tier,
                market.strip(),
                selection.strip(),
                price_value,
                int(confidence_value) if confidence_value is not None else None,
                1 if played else 0,
                stake_value,
                result,
                utcnow(),
            ),
        )
        return int(cursor.lastrowid)


def set_result(pick_id: int, result: str, settings: Settings | None = None) -> None:
    """Met a jour le resultat d'un pick."""
    if result not in RESULTS:
        raise HistoryError(f"Résultat inconnu : {result}")
    with connect(settings) as conn:
        conn.execute("UPDATE picks SET result = ? WHERE id = ?", (result, pick_id))


def delete_pick(pick_id: int, settings: Settings | None = None) -> None:
    with connect(settings) as conn:
        conn.execute("DELETE FROM picks WHERE id = ?", (pick_id,))


# -- Statistiques -----------------------------------------------------------


def _tally(rows: list[Any], key_field: str, labels: dict[str, str]) -> list[RateRow]:
    grouped: dict[str, RateRow] = {}
    for row in rows:
        key = row[key_field] or NO_SPORT
        entry = grouped.setdefault(key, RateRow(key=key, label=labels.get(key, key)))
        result = row["result"] or "pending"
        if result == "win":
            entry.won += 1
        elif result == "loss":
            entry.lost += 1
        elif result == "void":
            entry.void += 1
        else:
            entry.pending += 1
    return list(grouped.values())


def stats(settings: Settings | None = None) -> Stats:
    """Taux de reussite par palier et par sport, sur les picks joues.

    Aucune ponderation par la mise : ce serait un indicateur financier, donc
    hors du perimetre de l'application.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        rows = conn.execute(
            "SELECT k.tier, k.result, s.key AS sport_key FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "WHERE k.played = 1"
        ).fetchall()

    by_tier = _tally(rows, "tier", tier_labels)
    by_tier.sort(key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99)
    by_sport = sorted(_tally(rows, "sport_key", sport_labels), key=lambda item: item.label)

    overall = RateRow(key="all", label="Tous")
    for entry in by_tier:
        overall.won += entry.won
        overall.lost += entry.lost
        overall.void += entry.void
        overall.pending += entry.pending

    return Stats(by_tier=by_tier, by_sport=by_sport, overall=overall)
