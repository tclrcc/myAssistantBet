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
from math import sqrt
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .competitions import category_label, category_rank
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


#: z d'un intervalle de confiance a 95 %.
WILSON_Z = 1.96


def wilson(won: int, settled: int) -> tuple[float, float] | None:
    """Intervalle de Wilson a 95 % sur une proportion observee.

    Choisi plutot que l'intervalle normal, qui donne des bornes hors de [0, 1]
    et une largeur nulle a `x = 0` — soit exactement les deux cas ou la page a
    le plus besoin d'etre juste : 0/6 sur ULTRA FUN, et les regroupements de
    quelques lignes.

    C'est de la statistique descriptive sur des resultats passes. Rien n'en
    sort qui ressemble a une prevision : l'intervalle dit ce que ces tirages-la
    permettent d'affirmer, pas ce que le prochain fera.
    """
    if settled <= 0:
        return None
    z_squared = WILSON_Z * WILSON_Z
    observed = won / settled
    denominator = settled + z_squared
    centre = (won + z_squared / 2) / denominator
    half = (WILSON_Z / denominator) * sqrt(settled * observed * (1 - observed) + z_squared / 4)
    # Les bornes se rabattent sur [0, 1] : un taux ne sort pas de la, et une
    # borne a -0.03 se lirait comme une grandeur signee.
    return (max(0.0, centre - half), min(1.0, centre + half))


@dataclass
class Band:
    """Bande cible d'un niveau de confiance, en points de pourcentage.

    Elle donne a « confiance 4 » le referentiel qui lui manquait : sans elle,
    l'ecart entre la confiance annoncee et le taux constate ne se mesurait
    contre rien, alors que la page affirmait qu'il disait la derive.

    Reglable depuis les reglages, jamais en dur : c'est une decision de
    l'utilisateur sur sa propre echelle, pas une constante du projet.
    """

    level: int
    low: float
    high: float | None = None

    @property
    def label(self) -> str:
        return f"{self.low:.0f} – {self.high:.0f} %" if self.high else f"{self.low:.0f} % et plus"

    def excludes(self, interval: tuple[float, float]) -> bool:
        """L'intervalle est **entierement** hors de la bande.

        Le chevauchement le plus tenu suffit a se taire : signaler des qu'un
        taux sort de sa bande ferait crier a la derive sur du bruit, et au
        volume actuel presque chaque intervalle couvre plusieurs bandes.
        """
        low, high = interval[0] * 100, interval[1] * 100
        if high < self.low:
            return True
        return self.high is not None and low > self.high


@dataclass
class RateRow:
    """Taux de reussite d'un regroupement. Aucune notion d'argent.

    Le taux implicite moyen (`1/cote`) n'en est pas un : c'est de
    l'arithmetique sur des cotes deja connues, sur des selections deja
    tranchees. Rien n'y est multiplie par une mise, et rien n'en sort qui
    ressemble a un gain ou a une esperance.
    """

    key: str
    label: str
    won: int = 0
    lost: int = 0
    void: int = 0
    pending: int = 0
    #: Selections tranchees du regroupement qui portent une cote. Tenu a part
    #: de `settled` : une cote manquante ne retire pas la selection du taux
    #: constate, elle la retire du seul taux implicite.
    priced: int = 0
    #: Somme des `1/cote` de ces selections. Stockee en somme plutot qu'en
    #: moyenne pour que deux regroupements s'additionnent sans se ponderer.
    implied_sum: float = 0.0
    #: Identifiants des selections **tranchees** du regroupement. Sert a
    #: reconnaitre deux regroupements de deux axes differents qui decrivent le
    #: meme echantillon.
    #:
    #: Tranchees seulement, donc `len(members) == settled` : c'est le taux
    #: affiche qui est en cause dans un recouvrement, et lui ne se calcule que
    #: sur elles. En y comptant les paris en attente, la note annoncait « les
    #: memes 39 selections » a cote de barres portant 17/37 — un nombre que
    #: rien d'autre sur la page ne permettait de verifier.
    members: set[int] = field(default_factory=set)
    #: Matchs distincts portes par ces selections tranchees. Plusieurs lignes
    #: sur la meme rencontre — Vainqueur, handicap jeux et total de jeux — sont
    #: **une seule issue comptee trois fois** : le joueur qui gagne en deux sets
    #: les fait passer ensemble.
    events: set[int] = field(default_factory=set)
    #: Selections tranchees qu'aucun match ne porte. Comptees chacune pour une
    #: unite : rien ne permet de les rapprocher, donc rien ne permet de les
    #: declarer correlees.
    unattached: int = 0
    #: Bande cible, sur les seuls regroupements par confiance. Les autres axes
    #: n'en ont pas : un sport ne se fixe pas d'objectif de taux.
    band: Band | None = None

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

    @property
    def thin(self) -> bool:
        """Trop peu de paris tranches pour que le taux decrive autre chose que
        le hasard. Meme seuil que le prompt, reaction differente : le bloc du
        prompt tait la ligne, la page la garde et la marque — l'utilisateur
        vient justement y regarder ses propres donnees.
        """
        return 0 < self.settled < ANALYSIS_MIN_ROWS

    def merge(self, other: RateRow) -> None:
        """Ajoute un regroupement a celui-ci, champ par champ.

        Ecrit une seule fois, et c'est ce qui manquait : chaque chantier a
        ajoute un champ a cette classe, et les deux fusions recopiees a la main
        ne les ont pas suivis. `Analysis.overall` annoncait « 0 événements
        distincts » sur trois selections tranchees — un total qui oubliait
        silencieusement tout ce qui avait ete ajoute apres lui.
        """
        self.won += other.won
        self.lost += other.lost
        self.void += other.void
        self.pending += other.pending
        self.priced += other.priced
        self.implied_sum += other.implied_sum
        self.unattached += other.unattached
        self.members |= other.members
        self.events |= other.events

    @property
    def units(self) -> int:
        """Evenements distincts derriere les selections tranchees.

        C'est l'**effectif independant** : trois lignes sur le meme match ne
        sont pas trois observations. Quand il est nettement inferieur a
        `settled`, l'intervalle de Wilson — qui suppose l'independance — est
        optimiste, et le vrai est plus large.
        """
        return len(self.events) + self.unattached

    @property
    def clustered(self) -> bool:
        """Des selections se partagent des matchs."""
        return 0 < self.units < self.settled

    @property
    def units_label(self) -> str:
        """« 30 événements », et rien quand chaque pari a le sien."""
        return "" if not self.clustered else f"{self.units} événement(s)"

    @property
    def interval(self) -> tuple[float, float] | None:
        """Intervalle de Wilson a 95 % du taux constate."""
        return wilson(self.won, self.settled)

    @property
    def interval_label(self) -> str:
        """« [47 – 76] », en points de pourcentage."""
        bounds = self.interval
        if bounds is None:
            return ""
        low, high = bounds
        return f"[{low * 100:.0f} – {high * 100:.0f}]"

    @property
    def inconclusive(self) -> bool:
        """L'intervalle contient 50 % : le taux ne tranche pas.

        Un regroupement dans ce cas n'est pas presente comme un constat — il ne
        dit pas si l'on passe plus souvent qu'a pile ou face. Au volume actuel
        cela concerne la quasi-totalite des lignes, **et c'est le message** :
        c'est une propriete de l'echantillon, pas un defaut d'affichage.

        Distinct de `thin`, qui compte les lignes : une ligne peut porter assez
        de paris et rester indecise, et une ligne courte peut trancher — 0/6 ne
        contient pas 50 %.
        """
        bounds = self.interval
        return bounds is not None and bounds[0] <= 0.5 <= bounds[1]

    @property
    def off_band(self) -> bool:
        """Le taux est hors de sa bande cible, et l'intervalle le confirme.

        Le test porte sur l'**intervalle** et non sur le taux : un 44 % dont
        l'intervalle va de 31 a 57 traverse deux bandes, et le declarer hors de
        la sienne serait affirmer plus que les donnees ne portent.
        """
        bounds = self.interval
        return self.band is not None and bounds is not None and self.band.excludes(bounds)

    @property
    def implied(self) -> float | None:
        """Moyenne des `1/cote` sur les selections tranchees **et cotees**.

        Meme seuil de lecture que le taux constate : sous `ANALYSIS_MIN_ROWS`
        cotes, la moyenne decrit une poignee de prix et non un regroupement.
        """
        if self.priced < ANALYSIS_MIN_ROWS:
            return None
        return self.implied_sum / self.priced

    @property
    def implied_label(self) -> str:
        return "—" if self.implied is None else f"{self.implied * 100:.0f} %"

    @property
    def gap(self) -> float | None:
        """Ecart en points entre le taux constate et le taux implicite moyen.

        **Structurellement negatif** : `1/cote` porte la marge du bookmaker, si
        bien qu'un regroupement parfaitement neutre s'ecarte deja de la valeur
        de cette marge. Il ne se lit que compare a un autre ecart de la meme
        page, jamais dans l'absolu — la note sous le tableau le dit.
        """
        if self.rate is None or self.implied is None:
            return None
        return self.rate - self.implied

    @property
    def gap_label(self) -> str:
        return "—" if self.gap is None else f"{self.gap * 100:+.0f} pts"

    @property
    def priced_note(self) -> str:
        """« 9 cotées », et seulement quand ce n'est pas tout le regroupement.

        Les deux colonnes de cotes n'ont alors pas le meme denominateur que le
        taux constate : le taire ferait soustraire deux populations differentes
        sans que rien ne le signale.

        Rien non plus quand **aucune** selection n'est cotee : les deux colonnes
        affichent deja « — », et « 0 cotée(s) » a cote ne ferait que du bruit
        sur toutes les lignes anterieures a cette mesure.
        """
        if self.priced == 0 or self.priced == self.settled:
            return ""
        return f"{self.priced} cotée(s)"


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

#: Resultats rendus par une recherche de match. Au-dela, le menu redevient
#: illisible — et une recherche qui ramene cinquante matchs demande surtout a
#: etre precisee.
SEARCH_LIMIT = 50


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
        """L'horaire d'abord, l'affiche ensuite — sur **tous** les matchs.

        Il n'accompagnait que les matchs hors shortlist. Mais une shortlist
        porte trente affiches reparties sur deux jours, et le rattachement se
        fait de memoire — « le match de 20h30 ». Sans l'heure, il fallait
        reconnaitre l'affiche pour retrouver le match, alors que c'est
        justement ce dont on n'est pas sur.
        """
        return f"{self.local_time:%d/%m %H:%M} · {self.affiche}"


def pickable_events(
    session_id: int,
    settings: Settings | None = None,
    query: str = "",
) -> list[PickableEvent]:
    """Matchs proposes au rattachement d'une selection, shortlist d'abord.

    La shortlist ne suffit pas. Un match qui a commence quitte le board : il ne
    peut plus y etre coche, donc il n'entre plus dans aucune shortlist — et la
    selection qui le vise restait « — hors match — » pour toujours. Sans
    evenement, elle n'a ni sport ni competition : elle disparait des
    statistiques par sport sans que rien ne le dise.

    Les matchs voisins de la session sont donc proposes aussi, marques comme
    tels : ils n'ont pas ete analyses, et l'utilisateur doit le voir.

    **`query` leve la fenetre de temps, et elle seule.** Le voisinage de
    `PICKABLE_BEFORE_H` / `PICKABLE_AFTER_H` couvre la journee de travail, pas
    un pari pose trois jours plus tot ni un match reporte. Quand le match
    cherche n'est nulle part dans le menu, il n'y avait plus aucun recours :
    la selection restait sans evenement, donc sans sport, donc muette dans les
    statistiques. Une recherche par libelle rouvre tout le catalogue, bornee a
    `SEARCH_LIMIT` — un menu de mille lignes ne se lit pas davantage qu'un menu
    vide.
    """
    settings = settings or get_settings()
    needle = query.strip()
    with connect(settings) as conn:
        session = conn.execute(
            "SELECT created_at FROM sessions WHERE id = ?", (session_id,)
        ).fetchone()
        if session is None:
            return []
        created = datetime.fromisoformat(session["created_at"].replace("Z", "+00:00"))
        if created.tzinfo is None:
            created = created.replace(tzinfo=UTC)

        columns = (
            "SELECT e.id, e.home, e.away, e.commence_time, s.id AS sport_id, "
            "       s.label AS sport_label, "
            "       COALESCE(c.label, 'Saisie manuelle') AS competition, "
            "       EXISTS (SELECT 1 FROM session_events se "
            "               WHERE se.session_id = ? AND se.event_id = e.id) AS in_session "
            "FROM events e "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
        )
        if needle:
            like = f"%{needle}%"
            rows = conn.execute(
                columns + "WHERE e.home LIKE ? OR e.away LIKE ? OR c.label LIKE ? "
                "ORDER BY in_session DESC, e.commence_time DESC LIMIT ?",
                (session_id, like, like, like, SEARCH_LIMIT),
            ).fetchall()
        else:
            rows = conn.execute(
                columns + "WHERE (e.commence_time >= ? AND e.commence_time <= ?) "
                "   OR EXISTS (SELECT 1 FROM session_events se "
                "              WHERE se.session_id = ? AND se.event_id = e.id) "
                "ORDER BY in_session DESC, e.commence_time",
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
    session_id: int, settings: Settings | None = None, query: str = ""
) -> list[tuple[str, list[PickableEvent]]]:
    """Les memes matchs, groupes par sport et competition pour un `optgroup`.

    Retrouver un match parmi cent : une liste a plat oblige a lire chaque
    ligne. Le groupement est fait ici et non dans le template — le filtre
    `groupby` de Jinja retrie par ordre alphabetique, ce qui remettrait les
    matchs hors shortlist devant ceux de la session.

    **Les groupes sont ranges par heure du premier match**, la shortlist
    d'abord. Ils l'etaient par identifiant de sport puis par nom de
    competition : « Bundesliga 2 » passait donc devant « Premier League » pour
    des raisons alphabetiques, et on cherchait le match de 20h30 en parcourant
    des competitions rangees dans un ordre qui ne dit rien de la soiree. Une
    session se relit dans l'ordre ou elle s'est jouee.
    """
    groups: dict[str, list[PickableEvent]] = {}
    for event in pickable_events(session_id, settings, query):
        groups.setdefault(event.group, []).append(event)
    return sorted(
        groups.items(),
        key=lambda item: (not item[1][0].in_session, min(e.local_time for e in item[1])),
    )


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
    # Une cote se saisit au moment de l'analyse, et c'est celle-la qui compte :
    # elle vient du bloc CONTEXTE que Claude avait sous les yeux, releve au
    # scan. La relire chez le fournisseur au moment du rattachement donnerait
    # un prix posterieur, donc une autre grandeur. Elle reste facultative — les
    # selections anterieures a cette regle n'en portent pas — mais 1.00 ou
    # moins n'est pas une cote : ce serait un taux implicite d'au moins 100 %.
    if price_value is not None and price_value <= 1.0:
        raise HistoryError("« Cote » doit être supérieure à 1.00.")
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


def _count(entry: RateRow, result: str, row: Any = None) -> None:
    """Ajoute une selection a un regroupement.

    Ecrit une seule fois parce que quatre endroits comptent la meme chose : le
    taux par palier, celui par sport, la comparaison joue / ecarte et le total.
    Les copier aurait suffi a ce qu'un seul d'entre eux oublie la cote.

    Recoit **la ligne** et non ses champs un a un : chaque chantier de cette
    page en a ajoute un — la cote, l'identifiant, puis le match — et la liste
    d'arguments grossissait a chaque fois, obligeant a retoucher les cinq axes
    d'`analysis()` pour un champ qu'un seul d'entre eux lit.
    """
    settled = result in ("win", "loss")
    if settled and _column(row, "id") is not None:
        entry.members.add(int(row["id"]))
    if settled:
        # Un pick sans match ne se regroupe pas : rien ne dit a quelle rencontre
        # il appartient, donc rien ne permet de le rapprocher d'un autre. Il
        # compte pour une unite a lui seul — c'est l'hypothese **optimiste**, et
        # la note du bloc dit dans quel sens elle penche.
        event_id = _column(row, "event_id")
        if event_id is None:
            entry.unattached += 1
        else:
            entry.events.add(int(event_id))
    if result == "win":
        entry.won += 1
    elif result == "loss":
        entry.lost += 1
    elif result == "void":
        entry.void += 1
    else:
        entry.pending += 1
    # Seules les selections tranchees portent un taux constate : ne cumuler que
    # celles-la garde les deux colonnes comparables. Une cote <= 1.00 ne peut
    # pas etre une cote et donnerait un taux implicite superieur a 100 %.
    price = _column(row, "price")
    if settled and price is not None and price > 1.0:
        entry.priced += 1
        entry.implied_sum += 1.0 / price


def _tally(rows: list[Any], key_field: str, labels: dict[str, str]) -> list[RateRow]:
    grouped: dict[str, RateRow] = {}
    for row in rows:
        key = row[key_field] or NO_SPORT
        entry = grouped.setdefault(key, RateRow(key=key, label=labels.get(key, key)))
        _count(entry, row["result"] or "pending", row)
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
            "SELECT k.id, k.tier, k.result, k.price, k.event_id, s.key AS sport_key FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "WHERE k.played = 1"
        ).fetchall()

    by_tier = _tally(rows, "tier", tier_labels)
    by_tier.sort(key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99)
    by_sport = sorted(_tally(rows, "sport_key", sport_labels), key=lambda item: item.label)

    overall = RateRow(key="all", label="Tous")
    for entry in by_tier:
        overall.merge(entry)

    return Stats(by_tier=by_tier, by_sport=by_sport, overall=overall)


# -- Seuils de lecture, communs aux deux surfaces ---------------------------
#
# Sous quel compte un taux ne veut plus rien dire est une propriete des
# **donnees**, pas de l'endroit qui les affiche : les seuils sont donc ecrits
# une seule fois. Les copier des deux cotes les aurait fait diverger, et la
# page aurait fini par publier ce que le prompt refuse.
#
# Ce qui differe, c'est la **reaction**, et les deux sont justes :
#   · le prompt se tait. Claude n'a aucun moyen de savoir qu'il lit une semaine
#     de paris plutot qu'un historique, et un chiffre faux oriente plus
#     surement que pas de chiffre du tout ;
#   · la page le dit. C'est la surface ou l'utilisateur vient regarder ses
#     propres donnees : les lui cacher repondrait a cote de la question posee.
#     La ligne reste affichee et porte sa faiblesse, le detail chiffre sous le
#     graphique restant complet en toutes circonstances.

#: Sous ce total, aucun taux n'est publie. Un 2/3 se lit « 67 % » et n'apprend
#: rien ; dire qu'il manque du recul est en revanche une information juste.
#:
#: Releve a 40 apres coup : a 17 selections tranchees, le bloc publiait un
#: 2/6 en ATP contre 5/7 en WTA qui ne reposait que sur treize matchs d'un
#: seul tournoi, joues la meme nuit. Ce n'est pas « je lis mieux la WTA »,
#: c'est « une soiree s'est mal passee », et le prompt le presentait comme un
#: ordre de passage. Un chiffre faux oriente plus surement que pas de chiffre.
FEEDBACK_MIN_TOTAL = 40

#: Meme regle a l'echelle d'une ligne : un regroupement vu sept fois reste tu.
FEEDBACK_MIN_ROWS = 8

#: Journees d'analyse distinctes sous lesquelles aucun detail n'est publie.
#:
#: `FEEDBACK_MIN_TOTAL` garde le **volume**, ce garde-fou garde l'**etalement**,
#: et les deux ne se remplacent pas : 63 selections tranchees prises en quatre
#: jours restent une semaine de paris, pas un historique. Mesure sur les donnees
#: reelles — les 60 selections de la fenetre couvraient du 5 au 8 aout, un seul
#: tournoi de tennis en deux tableaux et une seule soiree de coupes d'Europe.
#: Le bloc annoncait alors « Masters 1000 13/30 » et « Tennis 13/30 » comme deux
#: observations independantes, la ou c'etaient les memes matchs sous deux noms,
#: et « Conference League 11 » sur une unique soiree.
#:
#: Une concentration ne se mesure pas par competition : les deux tableaux du
#: Canadian Open sont deux competitions distinctes en base, si bien qu'un compte
#: de competitions aurait declare l'echantillon varie. C'est le calendrier qui
#: la dit — dix journees ne tiennent pas dans une semaine de tournoi.
FEEDBACK_MIN_DAYS = 10

#: La page reprend les seuils du prompt, sans en inventer d'autres. Le meme
#: 46 % sur 35 selections d'un seul tournoi est trompeur des deux cotes ; seule
#: change la facon de le dire.
ANALYSIS_MIN_TOTAL = FEEDBACK_MIN_TOTAL
ANALYSIS_MIN_ROWS = FEEDBACK_MIN_ROWS
ANALYSIS_MIN_DAYS = FEEDBACK_MIN_DAYS


# -- Ce que vaut l'analyse --------------------------------------------------

#: Part de recouvrement au-dela de laquelle deux regroupements de deux axes
#: differents decrivent le meme echantillon sous deux noms. Elle se mesure
#: **des deux cotes** : un sous-ensemble entierement contenu dans un autre n'est
#: pas le meme echantillon, c'est une partie de celui-ci.
COLLINEAR_SHARE = 0.95

#: Part du volume au-dela de laquelle une echelle se comporte comme si elle
#: comptait moins de niveaux qu'elle n'en a. Mesure qui l'a fixee : 95 des 96
#: selections portent une confiance 3 ou 4, et 89 des 96 un palier SAFE ou FUN.
CONCENTRATION_SHARE = 0.80

#: Nombre de niveaux sur lesquels cette part se mesure.
CONCENTRATION_LEVELS = 2

#: Les cinq niveaux de confiance, **tous rendus meme jamais employes**. C'est
#: precisement le niveau qui ne sert jamais qui decrit la facon d'etiqueter :
#: omettre une confiance 5 a zero ferait lire une echelle a quatre crans, et
#: l'echelle n'aurait plus l'air d'etre sous-employee.
CONFIDENCE_SCALE = (5, 4, 3, 2, 1)

#: Sous ce nombre de paris tranches, un marche n'est pas liste : la longue
#: traine des libelles vus une fois noierait les marches qui comptent. C'est le
#: seul cas ou la page ecarte vraiment une ligne, et il ne contredit pas la
#: regle ci-dessus : un libelle vu une fois n'est pas un taux fragile, c'est du
#: bruit d'orthographe. Le compte des ecartes est annonce (`hidden_markets`).
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
    #: Journees d'analyse distinctes couvertes par ces selections tranchees.
    #: Comptee comme dans `feedback()` : la journee ou la decision a ete prise,
    #: et non celle du match — deux paris pris dans la meme seance restent une
    #: seule seance, meme a cheval sur minuit.
    days: int = 0
    minimum: int = ANALYSIS_MIN_TOTAL
    minimum_days: int = ANALYSIS_MIN_DAYS
    minimum_rows: int = ANALYSIS_MIN_ROWS
    by_tier: list[RateRow] = field(default_factory=list)
    by_confidence: list[RateRow] = field(default_factory=list)
    by_sport: list[RateRow] = field(default_factory=list)
    #: Niveau de tournoi. Un Grand Chelem et un 250 ne se jouent ni sur le meme
    #: format ni contre les memes joueurs : leurs taux ne se melangent pas.
    by_category: list[RateRow] = field(default_factory=list)
    by_market: list[RateRow] = field(default_factory=list)
    played: RateRow = field(default_factory=lambda: RateRow("played", "Jouées"))
    skipped: RateRow = field(default_factory=lambda: RateRow("skipped", "Écartées"))
    #: Marches ecartes faute d'echantillon. Annonce plutot que tue en silence.
    hidden_markets: int = 0
    #: Regroupements de deux axes differents qui portent les memes selections.
    #: Signales, jamais masques : le bloc reste affiche, c'est sa redondance qui
    #: est dite. Le masquer choisirait a la place du lecteur lequel des deux
    #: axes est le bon, et rien ici ne permet de trancher ca.
    overlaps: list[Overlap] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        return self.settled == 0

    @property
    def comparable(self) -> bool:
        """Vrai si les deux cotes de la comparaison ont de quoi etre lues."""
        return self.played.settled > 0 and self.skipped.settled > 0

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un regroupement se lise comme une tendance.

        Meme regle que `Feedback.enough`, et il faut les deux conditions : assez
        de selections **et** assez de journees. Ce que la page en fait differe —
        elle continue d'afficher, en disant ce que ces taux decrivent vraiment.
        """
        return self.settled >= self.minimum and self.days >= self.minimum_days

    @property
    def groups(self) -> tuple[list[RateRow], ...]:
        """Tous les regroupements de la vue, dans l'ordre ou la page les rend."""
        return (
            self.by_tier,
            self.by_confidence,
            self.by_sport,
            self.by_category,
            self.by_market,
        )

    @property
    def thin_rows(self) -> int:
        """Regroupements dont le taux mesure surtout le hasard.

        Annonce plutot que laissee a l'oeil : la barre pale se remarque quand on
        la cherche, pas quand on parcourt la page.
        """
        return sum(1 for rows in self.groups for row in rows if row.thin)

    @property
    def undecided_rows(self) -> int:
        """Regroupements dont l'intervalle contient 50 %.

        Compte a part de `thin_rows`, parce que les deux causes sont
        differentes : l'une dit « trop peu de lignes », l'autre « assez de
        lignes, mais l'ecart reste dans le bruit ». Une ligne peut etre l'un
        sans l'autre.
        """
        return sum(1 for rows in self.groups for row in rows if row.inconclusive)

    @property
    def settled_events(self) -> int:
        """Matchs distincts derriere les selections tranchees.

        Compte sur `overall`, donc sur toutes les selections a la fois : c'est
        l'effectif independant de la page entiere. Trois lignes sur le meme
        match — Vainqueur, handicap jeux, total de jeux — sont une seule issue
        comptee trois fois, le joueur qui gagne en deux sets les faisant passer
        ensemble.
        """
        return self.overall.units

    @property
    def clustered_rows(self) -> int:
        """Regroupements ou des selections se partagent des matchs."""
        return sum(1 for rows in self.groups for row in rows if row.clustered)

    @property
    def decided_rows(self) -> int:
        """Regroupements dont l'intervalle exclut 50 % — les seuls qui tranchent."""
        total = sum(len(rows) for rows in self.groups)
        return total - self.undecided_rows

    @property
    def overall(self) -> RateRow:
        """Les deux populations reunies : le taux de l'analyse, tous picks confondus.

        Deduit de `played` et `skipped` plutot que compte a part : deux
        comptages du meme ensemble finiraient par diverger, et il faudrait
        alors arbitrer lequel dit vrai.
        """
        total = RateRow(key="all", label="Toutes")
        for entry in (self.played, self.skipped):
            total.merge(entry)
        return total


@dataclass
class Overlap:
    """Deux regroupements de deux axes differents portant les memes selections.

    La page les presente cote a cote comme deux observations independantes,
    alors qu'ils comptent les memes paris : « Tennis 46 % » et « Masters 1000
    46 % » sont les memes 37 selections, 100 % du tennis en base ayant ete joue
    sur le Canadian Open. Le bloc par niveau de tournoi n'apprend alors rien de
    plus que le bloc par sport.
    """

    left_axis: str
    left_label: str
    right_axis: str
    right_label: str
    shared: int

    @property
    def note(self) -> str:
        return (
            f"{self.left_label} et {self.right_label} décrivent les mêmes {self.shared} sélections"
        )


def _overlaps(axes: list[tuple[str, list[RateRow]]]) -> list[Overlap]:
    """Regroupements de deux axes distincts qui decrivent le meme echantillon.

    Compare des **ensembles d'identifiants** et non des comptes : deux
    regroupements de 37 lignes chacun peuvent n'avoir aucune selection commune,
    et un taux identique de part et d'autre serait alors une coincidence, pas
    une redondance.

    Le seuil de lecture de la page s'applique ici aussi : deux regroupements
    d'une seule selection partagee se recouvrent a 100 % sans rien dire.
    """
    found: list[Overlap] = []
    for index, (left_axis, left_rows) in enumerate(axes):
        for right_axis, right_rows in axes[index + 1 :]:
            for left in left_rows:
                for right in right_rows:
                    if len(left.members) < ANALYSIS_MIN_ROWS:
                        continue
                    if len(right.members) < ANALYSIS_MIN_ROWS:
                        continue
                    # Le recouvrement doit **depasser** le seuil, pas l'atteindre :
                    # 19 selections communes sur 20 font exactement 95 % et
                    # laissent une ligne de difference de chaque cote, ce qui
                    # suffit a en faire deux echantillons distincts.
                    shared = len(left.members & right.members)
                    if shared <= COLLINEAR_SHARE * len(left.members):
                        continue
                    if shared <= COLLINEAR_SHARE * len(right.members):
                        continue
                    found.append(
                        Overlap(
                            left_axis=left_axis,
                            left_label=left.label,
                            right_axis=right_axis,
                            right_label=right.label,
                            shared=shared,
                        )
                    )
    return found


@dataclass
class MixRow:
    """Un niveau d'une echelle d'etiquetage, et la part du volume qu'il porte."""

    key: str
    label: str
    count: int = 0
    total: int = 0

    @property
    def share(self) -> float | None:
        return None if self.total == 0 else self.count / self.total

    @property
    def share_label(self) -> str:
        return "—" if self.share is None else f"{self.share * 100:.0f} %"


@dataclass
class Mix:
    """Repartition des selections sur une echelle d'etiquetage.

    **Le seul bloc de la page qui ne parle pas de resultats**, et le seul qui
    soit concluant au volume actuel : il decrit un comportement — comment je
    note — et non des issues. Il ne souffre donc ni du garde-fou de volume ni
    de celui d'etalement, qui portent tous deux sur des taux.

    Toutes les selections y comptent, tranchees ou non : une confiance annoncee
    est un geste pose au moment de l'analyse, et le resultat n'y change rien.
    """

    key: str
    label: str
    rows: list[MixRow] = field(default_factory=list)
    total: int = 0
    #: Selections ne portant aucune valeur sur cette echelle. Tenues hors du
    #: total : ne pas etiqueter n'est pas un niveau de l'echelle.
    unlabelled: int = 0

    @property
    def levels(self) -> int:
        return len(self.rows)

    @property
    def used(self) -> int:
        """Niveaux effectivement employes au moins une fois."""
        return sum(1 for row in self.rows if row.count)

    @property
    def top(self) -> list[MixRow]:
        """Les niveaux les plus employes, du plus gros au plus petit."""
        return sorted(self.rows, key=lambda row: -row.count)[:CONCENTRATION_LEVELS]

    @property
    def top_share(self) -> float:
        return 0.0 if self.total == 0 else sum(row.count for row in self.top) / self.total

    @property
    def top_share_label(self) -> str:
        return f"{self.top_share * 100:.0f} %"

    @property
    def top_labels(self) -> str:
        return " et ".join(row.label for row in self.top if row.count)

    @property
    def concentrated(self) -> bool:
        """L'echelle est employee comme si elle comptait moins de crans.

        Deux conditions : la part concentree, et une echelle qui compte
        vraiment plus de niveaux que ceux-la. Sur une echelle a deux crans, la
        meme part ne dirait rien — il n'y a pas d'autre facon de s'en servir.
        """
        return (
            self.total > 0
            and self.levels > CONCENTRATION_LEVELS
            and self.top_share > CONCENTRATION_SHARE
        )


def load_bands(settings: Settings | None = None) -> dict[int, Band]:
    """Bandes cibles par niveau de confiance, telles qu'elles sont reglees."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT level, low, high FROM confidence_bands ORDER BY level"
        ).fetchall()
    return {
        int(row["level"]): Band(
            level=int(row["level"]),
            low=float(row["low"]),
            high=None if row["high"] is None else float(row["high"]),
        )
        for row in rows
    }


def labelling(settings: Settings | None = None) -> list[Mix]:
    """Comment les selections sont etiquetees, sans regarder ce qu'elles valent.

    Volontairement separe d'`analysis()` : celle-ci mesure des issues et porte
    tous ses garde-fous d'echantillon, celui-ci decrit un comportement et n'en
    a besoin d'aucun. Les melanger aurait fini par appliquer a l'un les seuils
    de l'autre — donc a taire le seul bloc que le volume actuel autorise.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        tier_labels = _tier_labels(conn)
        tier_order = [row["key"] for row in conn.execute("SELECT key FROM tiers ORDER BY position")]
        rows = conn.execute("SELECT tier, confidence FROM picks").fetchall()

    # Aucune selection : aucune echelle a decrire. Rendre deux echelles a zero
    # ferait lire « je n'emploie aucun niveau » la ou il n'y a rien du tout.
    if not rows:
        return []

    confiance = Mix(key="confidence", label="confiance annoncée")
    niveaux = {str(level): 0 for level in CONFIDENCE_SCALE}
    for row in rows:
        if row["confidence"] is None:
            confiance.unlabelled += 1
        else:
            # Une valeur hors echelle ne peut pas arriver — `add_pick` borne a
            # 1..5 — mais la jeter en silence si elle arrivait ferait mentir le
            # total. Elle rejoint donc les non etiquetees.
            key = str(row["confidence"])
            if key in niveaux:
                niveaux[key] += 1
                confiance.total += 1
            else:
                confiance.unlabelled += 1
    confiance.rows = [
        MixRow(key=key, label=f"confiance {key}", count=count, total=confiance.total)
        for key, count in niveaux.items()
    ]

    palier = Mix(key="tier", label="palier")
    compte = dict.fromkeys(tier_order, 0)
    for row in rows:
        if row["tier"] in compte:
            compte[row["tier"]] += 1
            palier.total += 1
        else:
            palier.unlabelled += 1
    palier.rows = [
        MixRow(key=key, label=tier_labels.get(key, key), count=count, total=palier.total)
        for key, count in compte.items()
    ]

    return [confiance, palier]


def _rate_tally(entries: list[tuple[str, str, str, Any]], minimum: int = 1) -> list[RateRow]:
    """Agrege des quadruplets (cle, libelle, resultat, ligne) en lignes de taux."""
    grouped: dict[str, RateRow] = {}
    for key, label, result, row in entries:
        _count(grouped.setdefault(key, RateRow(key=key, label=label)), result, row)
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
        bands = {
            int(row["level"]): Band(
                level=int(row["level"]),
                low=float(row["low"]),
                high=None if row["high"] is None else float(row["high"]),
            )
            for row in conn.execute("SELECT level, low, high FROM confidence_bands")
        }
        rows = conn.execute(
            "SELECT k.id, k.tier, k.result, k.market, k.confidence, k.played, k.event_id, "
            "       k.created_at, k.price, s.key AS sport_key, c.category FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id"
        ).fetchall()

    report = Analysis()
    if not rows:
        return report

    results = [(row["result"] or "pending") for row in rows]
    report.settled = sum(1 for result in results if result in ("win", "loss"))
    # Les journees se comptent sur les seules selections tranchees, comme le
    # total : les compter sur toutes crediterait d'un etalement que le taux
    # affiche n'a pas.
    report.days = len(
        {
            str(row["created_at"])[:10]
            for row, result in zip(rows, results, strict=True)
            if result in ("win", "loss")
        }
    )

    report.by_tier = _rate_tally(
        [
            (row["tier"], tier_labels.get(row["tier"], row["tier"]), result, row)
            for row, result in zip(rows, results, strict=True)
        ]
    )
    report.by_tier.sort(
        key=lambda item: tier_order.index(item.key) if item.key in tier_order else 99
    )

    report.by_confidence = sorted(
        _rate_tally(
            [
                (str(row["confidence"]), f"confiance {row['confidence']}", result, row)
                for row, result in zip(rows, results, strict=True)
                if row["confidence"] is not None
            ]
        ),
        key=lambda item: item.key,
        reverse=True,
    )
    # La bande cible se rattache ici et nulle part ailleurs : un sport ou un
    # marche ne se fixe pas d'objectif de taux, seule une confiance annoncee le
    # fait — c'est meme sa definition.
    for entry in report.by_confidence:
        entry.band = bands.get(int(entry.key)) if entry.key.isdigit() else None

    report.by_sport = sorted(
        _rate_tally(
            [
                (
                    row["sport_key"] or NO_SPORT,
                    sport_labels.get(row["sport_key"], NO_SPORT),
                    result,
                    row,
                )
                for row, result in zip(rows, results, strict=True)
            ]
        ),
        key=lambda item: item.label,
    )

    # Le niveau de tournoi n'existe que la ou il a ete renseigne : une ligne
    # « non renseigne » ne dirait rien sur les matchs, seulement sur la saisie.
    report.by_category = sorted(
        _rate_tally(
            [
                (row["category"], category_label(row["category"]), result, row)
                for row, result in zip(rows, results, strict=True)
                if row["category"]
            ]
        ),
        key=lambda item: category_rank(item.key),
    )

    markets = [
        (_market_key(row["market"]), (row["market"] or "").strip(), result, row)
        for row, result in zip(rows, results, strict=True)
        if _market_key(row["market"])
    ]
    report.by_market = sorted(
        _rate_tally(markets, ANALYSIS_MIN_MARKET), key=lambda item: (-item.settled, item.label)
    )
    report.hidden_markets = len(_rate_tally(markets)) - len(report.by_market)

    for row, result in zip(rows, results, strict=True):
        _count(report.played if row["played"] else report.skipped, result, row)

    # Le palier n'entre pas dans la comparaison : il est defini par des tranches
    # de cote, donc correle par construction a tout ce qui depend du prix. Le
    # signaler comme une redondance decouverte serait annoncer sa definition.
    report.overlaps = _overlaps(
        [
            ("confidence", report.by_confidence),
            ("sport", report.by_sport),
            ("category", report.by_category),
            ("market", report.by_market),
        ]
    )

    return report


# -- Retour d'experience, pour le prompt ------------------------------------

#: Fenetre du retour : les N derniers picks tranches. Au-dela on parlerait
#: d'une autre saison, d'autres competitions et d'une autre facon de jouer.
FEEDBACK_WINDOW = 60

# Les trois seuils de publication — volume, ligne, etalement — vivent avec ceux
# de la page, plus haut : ils sont communs aux deux surfaces.


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
    #: Journees d'analyse distinctes couvertes par ces selections.
    days: int = 0
    window: int = FEEDBACK_WINDOW
    minimum: int = FEEDBACK_MIN_TOTAL
    minimum_days: int = FEEDBACK_MIN_DAYS
    by_tier: list[FeedbackRow] = field(default_factory=list)
    by_confidence: list[FeedbackRow] = field(default_factory=list)
    by_sport: list[FeedbackRow] = field(default_factory=list)
    #: Niveau de tournoi. Plus fourni que la competition — quatre Masters 1000
    #: font un echantillon la ou chacun pris seul n'en fait aucun.
    by_category: list[FeedbackRow] = field(default_factory=list)
    by_competition: list[FeedbackRow] = field(default_factory=list)
    by_market: list[FeedbackRow] = field(default_factory=list)

    @property
    def empty(self) -> bool:
        """Aucun pari tranche : le bloc disparait entierement du prompt."""
        return self.settled == 0

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un pourcentage veuille dire quelque chose.

        Deux conditions, et il faut les deux : assez de selections, et assez de
        journees. Un lot nombreux mais concentre sur quelques jours mesure ces
        jours-la — un tournoi, une soiree de coupe d'Europe, une meteo — et le
        prompt le presenterait comme un ordre de passage durable.
        """
        return self.settled >= self.minimum and self.days >= self.minimum_days


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
            "SELECT k.tier, k.result, k.market, k.confidence, k.created_at, s.key AS sport_key, "
            "       c.category, COALESCE(c.label, '') AS competition FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE k.result IN ('win', 'loss') "
            + ("AND k.played = 1 " if played_only else "")
            + "ORDER BY k.created_at DESC, k.id DESC LIMIT ?",
            (FEEDBACK_WINDOW,),
        ).fetchall()

    # La journee d'analyse, et non celle du match : ce bloc juge des decisions,
    # et deux paris pris le meme soir sur deux jours de calendrier restent une
    # seule seance de travail.
    report = Feedback(
        settled=len(rows),
        days=len({str(row["created_at"])[:10] for row in rows}),
    )
    if not report.enough:
        # En dessous d'un des deux seuils, on ne publie aucun detail : le prompt
        # dira ce qui manque, ce qui vaut mieux qu'un pourcentage trompeur.
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

    # Par niveau de tournoi : entre le sport et la competition. « Tennis » est
    # trop large, « ATP Canadian Open » trop etroit pour tenir un echantillon —
    # les Masters 1000 pris ensemble en font un.
    report.by_category = sorted(
        _feedback_tally(
            [
                (row["category"], category_label(row["category"]), row["result"])
                for row in rows
                if row["category"]
            ]
        ),
        key=lambda item: category_rank(item.key),
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
        "Retour d'experience : %d pari(s) tranche(s) sur les %d derniers, %d journee(s)",
        report.settled,
        FEEDBACK_WINDOW,
        report.days,
    )
    return report
