"""Historique des sessions, saisie des picks joues et taux de reussite.

**Aucun calcul financier ici** (SPEC.md section 9) : ni ROI, ni value, ni CLV, ni
esperance. La mise est enregistree parce qu'elle fait partie du souvenir de ce
qui a ete joue, mais elle n'est jamais agregee ni transformee en indicateur.

Le seul indicateur produit est un taux de reussite : gagnes / (gagnes + perdus).
Les paris annules et ceux encore en attente sont exclus du denominateur.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .labels import affiche, sort_key

logger = logging.getLogger(__name__)

RESULTS = ("pending", "win", "loss", "void")
RESULT_LABELS = {
    "pending": "en attente",
    "win": "gagné",
    "loss": "perdu",
    "void": "annulé",
}
NO_SPORT = "—"
#: Titre du bloc des selections qu'aucun match ne porte. Elles existent — un
#: pari sur un vainqueur de tournoi, une ligne dont le rapprochement a echoue —
#: et les taire les rendrait introuvables.
NO_COMPETITION = "Hors compétition"


class HistoryError(ValueError):
    """Saisie de pick invalide. Le message est affiche tel quel."""


def _local(value: str, tz: str) -> datetime:
    moment = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    return moment.astimezone(ZoneInfo(tz))


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
    #: Vrai uniquement si le pick est rattache a un coupon : « joue » veut dire
    #: pose chez le bookmaker, pas propose par l'analyse.
    played: bool
    stake: float | None
    result: str
    #: Renseignes depuis la phase des coupons ; vides sur les lectures qui n'en
    #: ont pas l'usage, ce qui evite une jointure a chaque affichage de pick.
    sport_label: str = ""
    coupon_id: int | None = None
    #: Renseignes par `list_picks`, qui range les selections par competition.
    competition: str = ""
    sport_order: int = 99
    commence_local: datetime | None = None

    @property
    def group(self) -> str:
        """Sport et competition, tels qu'ils titrent un bloc de la feuille."""
        if not self.competition:
            return NO_COMPETITION
        return f"{self.sport_label} · {self.competition}"

    @property
    def settled(self) -> bool:
        """Le resultat est connu. Un pari annule l'est : il n'y a plus rien a saisir."""
        return self.result in ("win", "loss", "void")

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


def _pick(row: Any, tier_labels: dict[str, str], tz: str = "") -> Pick:
    # « combine » designe desormais un coupon a plusieurs jambes : laisser ce
    # mot ici ferait lire un pari la ou il n'y a qu'une selection dont le match
    # n'a pas ete rapproche.
    event_label = affiche(row["home"], row["away"]) if row["home"] else "— hors match —"
    return Pick(
        pick_id=int(row["id"]),
        session_id=int(row["session_id"]),
        event_id=row["event_id"],
        event_label=event_label,
        tier=row["tier"],
        tier_label=tier_labels.get(row["tier"], row["tier"]),
        market=row["market"],
        selection=row["selection"],
        price=row["price"],
        confidence=row["confidence"],
        played=bool(row["played"]),
        stake=row["stake"],
        result=row["result"] or "pending",
        coupon_id=row["coupon_id"],
        sport_label=_column(row, "sport_label") or "",
        competition=_column(row, "competition") or "",
        sport_order=int(_column(row, "sport_order") or 99),
        commence_local=(
            _local(row["commence_time"], tz)
            if tz and _column(row, "commence_time") is not None
            else None
        ),
    )


def _column(row: Any, name: str) -> Any:
    """Valeur d'une colonne optionnelle. Toutes les lectures ne joignent pas
    les memes tables : `sqlite3.Row` leve sur une colonne absente."""
    try:
        return row[name]
    except (IndexError, KeyError):
        return None


#: Colonnes communes aux lectures de picks : le match, sa competition et son
#: sport. `sports.id` sert a ranger le football avant le tennis, comme partout
#: ailleurs dans l'application.
_PICK_JOIN = (
    "SELECT k.*, e.home, e.away, e.commence_time, s.label AS sport_label, "
    "       s.id AS sport_order, c.label AS competition FROM picks k "
    "LEFT JOIN events e ON e.id = k.event_id "
    "LEFT JOIN sports s ON s.id = e.sport_id "
    "LEFT JOIN competitions c ON c.id = e.competition_id "
)


def list_picks(session_id: int, settings: Settings | None = None) -> list[Pick]:
    """Picks enregistres pour une session, dans l'ordre de saisie."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        labels = _tier_labels(conn)
        rows = conn.execute(
            _PICK_JOIN + "WHERE k.session_id = ? ORDER BY k.id", (session_id,)
        ).fetchall()
    return [_pick(row, labels, settings.tz) for row in rows]


def get_pick(pick_id: int, settings: Settings | None = None) -> Pick | None:
    """Un pick, ou None s'il n'existe pas."""
    settings = settings or get_settings()
    with connect(settings) as conn:
        labels = _tier_labels(conn)
        row = conn.execute(_PICK_JOIN + "WHERE k.id = ?", (pick_id,)).fetchone()
    return _pick(row, labels, settings.tz) if row is not None else None


@dataclass
class Worksheet:
    """Les selections d'une session, rangees pour le travail de la journee.

    Deux blocs plutot qu'une liste : **ce qui reste a trancher** et **ce qui
    l'est deja**. Melanges, la liste ne dit pas ou en est la saisie, et il faut
    relire quinze lignes pour trouver les trois qui attendent encore un
    resultat. Chaque bloc est groupe par competition — c'est ainsi qu'on relit
    une journee, tournoi par tournoi, pas dans l'ordre ou Claude a rendu son
    tableau.
    """

    pending: list[tuple[str, list[Pick]]] = field(default_factory=list)
    settled: list[tuple[str, list[Pick]]] = field(default_factory=list)

    @property
    def pending_count(self) -> int:
        return sum(len(picks) for _, picks in self.pending)

    @property
    def settled_count(self) -> int:
        return sum(len(picks) for _, picks in self.settled)

    @property
    def total(self) -> int:
        return self.pending_count + self.settled_count

    @property
    def empty(self) -> bool:
        return self.total == 0


def _grouped(picks: list[Pick]) -> list[tuple[str, list[Pick]]]:
    """Groupe par competition, sport par sport puis par ordre alphabetique.

    Les selections sans match ferment la marche : elles n'appartiennent a aucun
    tournoi et les mettre en tete decalerait tout le reste.
    """
    ordered = sorted(
        picks,
        key=lambda pick: (
            pick.competition == "",
            pick.sport_order,
            sort_key(pick.competition),
            pick.commence_local or datetime.max.replace(tzinfo=UTC),
            pick.pick_id,
        ),
    )
    groups: dict[str, list[Pick]] = {}
    for pick in ordered:
        groups.setdefault(pick.group, []).append(pick)
    return list(groups.items())


def worksheet(session_id: int, settings: Settings | None = None) -> Worksheet:
    """Feuille de session : ce qui reste a trancher, puis ce qui l'est deja."""
    picks = list_picks(session_id, settings)
    return Worksheet(
        pending=_grouped([pick for pick in picks if not pick.settled]),
        settled=_grouped([pick for pick in picks if pick.settled]),
    )


# -- Matchs proposes au rattachement d'une selection ------------------------

#: Fenetre des matchs proposes autour d'une session, en heures. Une session est
#: le travail d'une journee : ses paris portent sur les matchs du jour et sur la
#: nuit qui suit. Au-dela, on proposerait le catalogue entier dans un menu.
PICKABLE_BEFORE_H = 24
PICKABLE_AFTER_H = 48


@dataclass
class PickableEvent:
    """Un match proposable au rattachement d'une selection."""

    event_id: int
    home: str
    away: str
    sport_label: str
    competition: str
    local_time: datetime
    #: Le match fait partie de la shortlist, donc de ce qui a ete analyse.
    in_session: bool = True

    @property
    def affiche(self) -> str:
        return affiche(self.home, self.away)

    @property
    def group(self) -> str:
        """Libelle de l'`optgroup`. Sport et competition, toujours."""
        prefix = "" if self.in_session else "Hors sélection — "
        return f"{prefix}{self.sport_label} · {self.competition}"

    @property
    def label(self) -> str:
        """Un match hors shortlist porte son horaire : il peut etre d'un autre jour."""
        if self.in_session:
            return self.affiche
        return f"{self.local_time:%d/%m %H:%M} · {self.affiche}"


def pickable_events(session_id: int, settings: Settings | None = None) -> list[PickableEvent]:
    """Matchs proposes au rattachement d'une selection, shortlist d'abord.

    La shortlist ne suffit pas. Un match qui a commence quitte le board : il ne
    peut plus y etre coche, donc il n'entre plus dans aucune shortlist — et la
    selection qui le vise restait « — hors match — » pour toujours. Sans
    evenement, elle n'a ni sport ni competition : elle disparait des
    statistiques par sport sans que rien ne le dise.

    Les matchs voisins de la session sont donc proposes aussi, marques comme
    tels : ils n'ont pas ete analyses, et l'utilisateur doit le voir.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        session = conn.execute(
            "SELECT created_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return []
        created = datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)
        rows = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, s.id AS sport_id, "
            "       s.label AS sport_label, "
            "       COALESCE(c.label, 'Saisie manuelle') AS competition, "
            "       EXISTS (SELECT 1 FROM session_events se "
            "               WHERE se.session_id = ? AND se.event_id = e.id) AS in_session "
            "FROM events e "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE (e.commence_time >= ? AND e.commence_time <= ?) "
            "   OR EXISTS (SELECT 1 FROM session_events se "
            "              WHERE se.session_id = ? AND se.event_id = e.id) "
            "ORDER BY in_session DESC, s.id, competition, e.commence_time",
            (
                session_id,
                _iso(created - timedelta(hours=PICKABLE_BEFORE_H)),
                _iso(created + timedelta(hours=PICKABLE_AFTER_H)),
                session_id,
            ),
        ).fetchall()

    return [
        PickableEvent(
            event_id=int(row["id"]),
            home=row["home"],
            away=row["away"] or "",
            sport_label=row["sport_label"],
            competition=row["competition"],
            local_time=_local(row["commence_time"], settings.tz),
            in_session=bool(row["in_session"]),
        )
        for row in rows
    ]


def pickable_groups(
    session_id: int, settings: Settings | None = None
) -> list[tuple[str, list[PickableEvent]]]:
    """Les memes matchs, groupes par sport et competition pour un `optgroup`.

    Retrouver un match parmi cent : une liste a plat oblige a lire chaque
    ligne. Le groupement est fait ici et non dans le template — le filtre
    `groupby` de Jinja retrie par ordre alphabetique, ce qui remettrait les
    matchs hors shortlist devant ceux de la session.
    """
    groups: dict[str, list[PickableEvent]] = {}
    for event in pickable_events(session_id, settings):
        groups.setdefault(event.group, []).append(event)
    return list(groups.items())


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
    played: bool = False,
    result: str = "pending",
    settings: Settings | None = None,
) -> int:
    """Enregistre une selection. Renvoie son id.

    **Elle n'est pas jouee pour autant** : `played` ne passe a vrai qu'au
    rattachement a un coupon, c'est a dire quand le pari a reellement ete pose
    chez le bookmaker. Sans cette regle, une selection proposee par Claude puis
    ecartee comptait dans les taux au meme titre qu'un pari joue, et les
    indicateurs melangeaient deux questions differentes : ce que vaut l'analyse,
    et ce que valent mes paris.
    """
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


def set_event(pick_id: int, event_id: str = "", settings: Settings | None = None) -> None:
    """Rattache une selection a un match, ou l'en detache si `event_id` est vide.

    Se corrige apres coup : le rapprochement automatique de l'import refuse de
    deviner, et la shortlist ne contient pas toujours le match vise. Sans cette
    reprise, une selection restait « — hors match — » definitivement — donc
    sans sport ni competition, donc muette dans les statistiques.
    """
    identifier = str(event_id).strip()
    with connect(settings) as conn:
        if not identifier:
            conn.execute("UPDATE picks SET event_id = NULL WHERE id = ?", (pick_id,))
            return
        if not identifier.isdigit():
            raise HistoryError(f"Match inconnu : {event_id}")
        known = conn.execute("SELECT 1 FROM events WHERE id = ?", (int(identifier),)).fetchone()
        if known is None:
            raise HistoryError(f"Match inconnu : {event_id}")
        conn.execute("UPDATE picks SET event_id = ? WHERE id = ?", (int(identifier), pick_id))


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
    """Taux de reussite par palier et par sport, sur les picks **joues**.

    Joue veut dire rattache a un coupon, donc reellement pose chez le
    bookmaker. Une selection proposee puis ecartee ne compte pas : elle
    repondrait a une autre question que celle posee ici.

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


# -- Ce que vaut l'analyse --------------------------------------------------

#: Sous ce nombre de paris tranches, un marche n'est pas liste : la longue
#: traine des libelles vus une fois noierait les marches qui comptent.
ANALYSIS_MIN_MARKET = 2


@dataclass
class Analysis:
    """Ce que vaut l'analyse, jouee ou non.

    Distincte de `stats()`, qui ne mesure que les paris reellement poses. Ici
    on juge la **selection** : avait-elle raison ? Une selection ecartee dont
    le resultat est connu compte donc autant qu'une selection jouee.

    `played` et `skipped` se lisent ensemble : si les selections ecartees
    gagnent aussi souvent que celles jouees, le tri n'apporte rien. C'est la
    seule facon de mesurer ce que vaut le geste de trier, et elle ne coute
    qu'un resultat saisi sur une ligne qu'on n'a pas jouee.
    """

    settled: int = 0
    by_tier: list[RateRow] = field(default_factory=list)
    by_confidence: list[RateRow] = field(default_factory=list)
    by_sport: list[RateRow] = field(default_factory=list)
    by_market: list[RateRow] = field(default_factory=list)
    played: RateRow = field(default_factory=lambda: RateRow("played", "Jouées"))
    skipped: RateRow = field(default_factory=lambda: RateRow("skipped", "Écartées"))
    #: Marches ecartes faute d'echantillon. Annonce plutot que tue en silence.
    hidden_markets: int = 0

    @property
    def empty(self) -> bool:
        return self.settled == 0

    @property
    def comparable(self) -> bool:
        """Vrai si les deux cotes de la comparaison ont de quoi etre lues."""
        return self.played.settled > 0 and self.skipped.settled > 0

    @property
    def overall(self) -> RateRow:
        """Les deux populations reunies : le taux de l'analyse, tous picks confondus.

        Deduit de `played` et `skipped` plutot que compte a part : deux
        comptages du meme ensemble finiraient par diverger, et il faudrait
        alors arbitrer lequel dit vrai.
        """
        total = RateRow(key="all", label="Toutes")
        for entry in (self.played, self.skipped):
            total.won += entry.won
            total.lost += entry.lost
            total.void += entry.void
            total.pending += entry.pending
        return total


def _rate_tally(entries: list[tuple[str, str, str]], minimum: int = 1) -> list[RateRow]:
    """Agrege des triplets (cle, libelle, resultat) en lignes de taux."""
    grouped: dict[str, RateRow] = {}
    for key, label, result in entries:
        row = grouped.setdefault(key, RateRow(key=key, label=label))
        if result == "win":
            row.won += 1
        elif result == "loss":
            row.lost += 1
        elif result == "void":
            row.void += 1
        else:
            row.pending += 1
    return [row for row in grouped.values() if row.settled >= minimum]


def analysis(settings: Settings | None = None) -> Analysis:
    """Taux de reussite de **toutes** les selections, jouees ou non.

    Aucun filtre sur `played` : c'est precisement ce qui distingue cette vue de
    `stats()`. Aucun montant n'y entre non plus.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        rows = conn.execute(
            "SELECT k.tier, k.result, k.market, k.confidence, k.played, "
            "       s.key AS sport_key FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id"
        ).fetchall()

    report = Analysis()
    if not rows:
        return report

    results = [(row["result"] or "pending") for row in rows]
    report.settled = sum(1 for result in results if result in ("win", "loss"))

    report.by_tier = _rate_tally(
        [
            (row["tier"], tier_labels.get(row["tier"], row["tier"]), result)
            for row, result in zip(rows, results, strict=True)
        ]
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )

    report.by_confidence = sorted(
        _rate_tally(
            [
                (str(row["confidence"]), f"confiance {row['confidence']}", result)
                for row, result in zip(rows, results, strict=True)
                if row["confidence"] is not None
            ]
        ),
        key=lambda item: item.key,
        reverse=True,
    )

    report.by_sport = sorted(
        _rate_tally(
            [
                (row["sport_key"] or NO_SPORT, sport_labels.get(row["sport_key"], NO_SPORT), result)
                for row, result in zip(rows, results, strict=True)
            ]
        ),
        key=lambda item: item.label,
    )

    markets = [
        (_market_key(row["market"]), (row["market"] or "").strip(), result)
        for row, result in zip(rows, results, strict=True)
        if _market_key(row["market"])
    ]
    report.by_market = sorted(
        _rate_tally(markets, ANALYSIS_MIN_MARKET), key=lambda item: (-item.settled, item.label)
    )
    report.hidden_markets = len(_rate_tally(markets)) - len(report.by_market)

    for row, result in zip(rows, results, strict=True):
        entry = report.played if row["played"] else report.skipped
        if result == "win":
            entry.won += 1
        elif result == "loss":
            entry.lost += 1
        elif result == "void":
            entry.void += 1
        else:
            entry.pending += 1

    return report


# -- Retour d'experience, pour le prompt ------------------------------------

#: Fenetre du retour : les N derniers picks tranches. Au-dela on parlerait
#: d'une autre saison, d'autres competitions et d'une autre facon de jouer.
FEEDBACK_WINDOW = 60

#: Sous ce total, aucun taux n'est publie. Un 2/3 se lit « 67 % » et n'apprend
#: rien ; dire qu'il manque du recul est en revanche une information juste.
FEEDBACK_MIN_TOTAL = 10

#: Meme regle a l'echelle d'une ligne : un palier vu trois fois reste tu.
FEEDBACK_MIN_ROWS = 4


@dataclass
class FeedbackRow:
    """Un regroupement et son taux. Ni mise, ni gain, ni esperance."""

    key: str
    label: str
    won: int = 0
    lost: int = 0

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def rate(self) -> float | None:
        return None if self.settled == 0 else self.won / self.settled

    #: Largeur du libelle dans le prompt. Un nom de competition depasse volontiers
    #: la largeur d'un palier : sans troncature, une seule ligne longue casse
    #: l'alignement de tout le bloc et le rend penible a lire.
    LABEL_WIDTH = 20

    @property
    def line(self) -> str:
        """`🔴 GIGA FUN         2/14    14 %`, aligne comme le reste du prompt."""
        if self.rate is None:
            return self.label
        label = self.label
        if len(label) > self.LABEL_WIDTH:
            label = label[: self.LABEL_WIDTH - 1] + "…"
        compte = f"{self.won}/{self.settled}"
        return f"{label:<{self.LABEL_WIDTH}} {compte:<7} {self.rate * 100:.0f} %"


@dataclass
class Feedback:
    """Ce que l'historique dit, tel qu'il entre dans le prompt.

    Le seul indicateur reste `gagnes / (gagnes + perdus)`, comme partout
    ailleurs dans ce module : aucun montant n'y entre, et rien ici ne se
    rapproche d'une cote — ce serait calculer une esperance.
    """

    settled: int = 0
    window: int = FEEDBACK_WINDOW
    minimum: int = FEEDBACK_MIN_TOTAL
    by_tier: list[FeedbackRow] = field(default_factory=list)
    by_confidence: list[FeedbackRow] = field(default_factory=list)
    by_sport: list[FeedbackRow] = field(default_factory=list)
    by_competition: list[FeedbackRow] = field(default_factory=list)
    by_market: list[FeedbackRow] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Aucun pari tranche : le bloc disparait entierement du prompt."""
        return self.settled == 0

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un pourcentage veuille dire quelque chose."""
        return self.settled >= self.minimum


def _market_key(text: str) -> str:
    """Regroupe les libelles de marche, qui sont ecrits a la main.

    `Over 2.5 buts`, `over 2,5 buts` et `Over  2.5 Buts` sont le meme marche.
    La normalisation de `matching.py` ne convient pas ici : elle retire les
    chiffres aux extremites, et « Over 2.5 » y deviendrait « over ».
    """
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


def _feedback_tally(entries: list[tuple[str, str, str]]) -> list[FeedbackRow]:
    """Agrege des triplets (cle, libelle, resultat) en lignes exploitables.

    Les regroupements trop peu fournis sont ecartes : sur trois paris, le taux
    mesure le hasard. Les entrees arrivant du plus recent au plus ancien, le
    libelle retenu est la derniere orthographe employee.
    """
    grouped: dict[str, FeedbackRow] = {}
    for key, label, result in entries:
        row = grouped.setdefault(key, FeedbackRow(key=key, label=label))
        if result == "win":
            row.won += 1
        else:
            row.lost += 1
    return [row for row in grouped.values() if row.settled >= FEEDBACK_MIN_ROWS]


def feedback(settings: Settings | None = None, played_only: bool = False) -> Feedback:
    """Taux de reussite des dernieres selections tranchees, pour nourrir le prompt.

    Ferme la boucle du parcours : le prompt part, les picks reviennent, leurs
    resultats sont saisis — et la session suivante sait enfin ce qui a tenu.
    Sans cela l'analyse repart de zero a chaque fois et conseille un marche
    sans jamais apprendre qu'il ne passe pas.

    Par defaut, **toutes** les selections tranchees comptent, jouees ou non :
    ce bloc juge l'analyse, pas la discipline de mise. Une selection proposee
    puis ecartee dont on connait le resultat dit tout autant si l'angle etait
    bon. `played_only` restreint aux paris reellement poses, ce que mesure
    `stats()`.

    Les paris annules et ceux en attente sont exclus : une selection sans
    resultat n'apprend rien, et la compter au denominateur ferait mentir le taux.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        tier_labels = _tier_labels(conn)
        sport_labels = {
            row["key"]: row["label"] for row in conn.execute("SELECT key, label FROM sports")
        }
        rows = conn.execute(
            "SELECT k.tier, k.result, k.market, k.confidence, s.key AS sport_key, "
            "       COALESCE(c.label, '') AS competition FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE k.result IN ('win', 'loss') "
            + ("AND k.played = 1 " if played_only else "")
            + "ORDER BY k.created_at DESC, k.id DESC LIMIT ?",
            (FEEDBACK_WINDOW,),
        ).fetchall()

    report = Feedback(settled=len(rows))
    if not report.enough:
        # En dessous du seuil, on ne publie aucun detail : le prompt dira qu'il
        # manque du recul, ce qui vaut mieux qu'un pourcentage trompeur.
        return report

    report.by_tier = _feedback_tally(
        [(row["tier"], tier_labels.get(row["tier"], row["tier"]), row["result"]) for row in rows]
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )

    report.by_confidence = _feedback_tally(
        [
            (str(row["confidence"]), f"confiance {row['confidence']}", row["result"])
            for row in rows
            if row["confidence"] is not None
        ]
    )
    report.by_confidence.sort(key=lambda item: item.key, reverse=True)

    report.by_sport = sorted(
        _feedback_tally(
            [
                (
                    row["sport_key"] or NO_SPORT,
                    sport_labels.get(row["sport_key"], NO_SPORT),
                    row["result"],
                )
                for row in rows
            ]
        ),
        key=lambda item: item.label,
    )

    report.by_market = sorted(
        _feedback_tally(
            [
                (_market_key(row["market"]), (row["market"] or "").strip(), row["result"])
                for row in rows
                if _market_key(row["market"])
            ]
        ),
        key=lambda item: (-item.settled, item.label),
    )

    # Par competition : c'est la reponse a « quel type de match ». Un taux par
    # sport melange une Ligue 1 lisible et un championnat scandinave d'ete.
    report.by_competition = sorted(
        _feedback_tally(
            [
                (row["competition"], row["competition"], row["result"])
                for row in rows
                if row["competition"]
            ]
        ),
        key=lambda item: (-item.settled, item.label),
    )

    logger.info(
        "Retour d'experience : %d pari(s) tranche(s) sur les %d derniers",
        report.settled,
        FEEDBACK_WINDOW,
    )
    return report
