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
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from math import ceil, sqrt
from typing import Any
from zoneinfo import ZoneInfo

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .competitions import category_label, category_rank
from .labels import affiche, sort_key
from .market_families import family_key, family_label, family_of, family_rank
from .market_families import load as load_families
from .market_families import market_key as _market_key

logger = logging.getLogger(__name__)

RESULTS = ("pending", "win", "loss", "void")
RESULT_LABELS = {
    "pending": "en attente",
    "win": "gagné",
    "loss": "perdu",
    "void": "annulé",
}
NO_SPORT = "—"

#: Nature de l'angle qui porte la selection. **C'est ce mot qui choisit le
#: marche** : un raisonnement sur un rythme, une usure, un desequilibre, qui
#: finit sur un nom de camp, a perdu en route ce qu'il avait compris.
#:
#: Sans accent dans la cle, comme partout : elle voyage dans un formulaire, une
#: URL et une colonne SQLite. Le libelle, lui, s'ecrit correctement.
ANGLES = {
    "issue": "Issue",
    "maniere": "Manière",
}

#: La cle de l'angle qui decrit une **maniere**. Nommee plutot que recopiee :
#: c'est elle que le detecteur de conflit compare a la famille du marche rendu.
ANGLE_MANNER = "maniere"

#: Niveau de la source qui porte le fait principal, sur l'echelle a quatre crans
#: du preambule. `lecture` n'est pas une absence de valeur mais une valeur de
#: l'echelle : l'analyse declare qu'aucun fait date ne porte la selection. La
#: distinguer de « non renseigne » est tout l'objet de la mesure — c'est
#: precisement la comparaison que le regroupement doit permettre.
SOURCE_LEVELS = {
    "1": "1 · officiel",
    "2": "2 · presse",
    "3": "3 · statistiques",
    "4": "4 · agrégateurs",
    "lecture": "Lecture seule",
}
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
    #: Justification d'independance d'une seconde selection sur le meme match.
    #: **Rendue sur la feuille de session**, sans quoi elle serait ecrite et
    #: jamais relue — le sort exact reserve a `/players/squads`, retire faute de
    #: lecteur. C'est en la relisant qu'on voit si deux angles etaient vraiment
    #: independants ou deux facons de dire la meme chose.
    independence_note: str = ""
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


#: z d'un test bilateral a 5 %, et z de la puissance visee (80 %). Les deux
#: valeurs habituelles : un test qui laisse passer une difference reelle une
#: fois sur cinq est deja peu exigeant, et viser mieux ferait exploser la cible.
TEST_Z_ALPHA = 1.96
TEST_Z_BETA = 0.84


def required_sample(first: float, second: float) -> int | None:
    """Selections **par groupe** pour qu'un ecart observe devienne testable.

    Repond a la question que la page pose sans jamais y repondre : « SAFE fait
    mieux que FUN » est-il un constat ou du bruit ? Le nombre dit ce qu'il
    faudrait accumuler pour trancher, ce qui est plus utile que de trancher
    trop tot.

    C'est un calcul de puissance sur des proportions deja observees, pas une
    prevision : rien n'y annonce le prochain pari.

    None quand les deux taux sont egaux — un ecart nul ne devient jamais
    testable, aucun volume n'y suffit.
    """
    gap = first - second
    if gap == 0:
        return None
    spread = first * (1 - first) + second * (1 - second)
    return ceil((TEST_Z_ALPHA + TEST_Z_BETA) ** 2 * spread / (gap * gap))


@dataclass
class Comparison:
    """Deux regroupements que la page invite implicitement a comparer.

    Elle les pose cote a cote sans jamais dire si l'ecart tient : ce bloc
    repond en donnant le volume qui le rendrait testable.
    """

    left: RateRow
    right: RateRow
    required: int | None

    @property
    def observed(self) -> int:
        """Selections tranchees deja accumulees sur les deux groupes."""
        return self.left.settled + self.right.settled

    @property
    def units(self) -> int:
        """Evenements distincts derriere ces deux groupes.

        Compte sur l'union et non par addition : un match portant une selection
        de chaque groupe ne compte qu'une fois.
        """
        return len(self.left.events | self.right.events) + (
            self.left.unattached + self.right.unattached
        )

    @property
    def clustered(self) -> bool:
        return 0 < self.units < self.observed


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


#: Un en-tete de bloc de match dans un prompt archive : `### M12 · TENNIS · …`.
#: Sert **uniquement** a reconstruire le lot des sessions anterieures a la table
#: `prompt_events`. Ce que le motif capture est l'identite du match — sport,
#: competition, affiche, heure — et non le numero de bloc, qui change d'une
#: generation a l'autre pour un meme match.
_BLOCK_HEADER = re.compile(r"^### M\d+ · (.+)$", re.MULTILINE)


@dataclass
class Lot:
    """Les matchs soumis a l'analyse au cours d'une session.

    Ce n'est **pas** la shortlist : celle-ci decrit ou en est le board et se
    vide a mesure qu'on decoche. Mesure sur les donnees reelles — la session du
    09/08 porte 4 lignes de shortlist pour 29 selections, et son premier prompt
    en servait 12.

    C'est l'**union des matchs entres dans un prompt**. Compter des matchs et
    non des prompts est ce qui rend la grandeur juste : regenerer vingt fois le
    meme lot ne l'agrandit pas d'une ligne, il ne grossit que lorsqu'un match
    nouveau apparait — ce que le scan fait plusieurs fois par jour.
    """

    size: int
    #: Lot recalcule depuis les corps de prompts archives, faute d'avoir ete
    #: enregistre a la generation. Dit plutot que tu : c'est la meme grandeur,
    #: mais elle n'a pas la meme garantie — un match ne figure dans le corps
    #: que par son libelle, et deux rencontres homonymes le meme jour n'en
    #: feraient qu'une. Ce chemin s'eteint de lui-meme.
    reconstructed: bool = False


def lots(settings: Settings | None = None) -> dict[int, Lot]:
    """Taille du lot analyse, par session.

    Deux origines, et la seconde se retire d'elle-meme : ce que `prompt_events`
    a enregistre, et — pour les sessions anterieures a cette table — ce que les
    corps de prompts archives permettent de recompter. La reconstruction n'est
    pas une invention : l'information dormait deja en base, elle n'etait juste
    lue par personne.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        recorded = {
            int(row["session_id"]): int(row["lot"])
            for row in conn.execute(
                "SELECT p.session_id, COUNT(DISTINCT pe.event_id) AS lot "
                "FROM prompts p JOIN prompt_events pe ON pe.prompt_id = p.id "
                "GROUP BY p.session_id"
            )
        }
        # Les corps ne sont relus que pour les sessions qui n'ont rien
        # d'enregistre : sur une base entierement passee a `prompt_events`,
        # cette requete ne rend aucune ligne et rien n'est parcouru.
        archived = conn.execute(
            "SELECT p.session_id, p.body FROM prompts p "
            "WHERE p.session_id NOT IN ("
            "  SELECT DISTINCT session_id FROM prompts WHERE id IN ("
            "    SELECT prompt_id FROM prompt_events))"
        ).fetchall()

    found = {session_id: Lot(size=size) for session_id, size in recorded.items()}
    rebuilt: dict[int, set[str]] = {}
    for row in archived:
        rebuilt.setdefault(int(row["session_id"]), set()).update(
            _BLOCK_HEADER.findall(row["body"] or "")
        )
    for session_id, matches in rebuilt.items():
        found[session_id] = Lot(size=len(matches), reconstructed=True)
    return found


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
        independence_note=_column(row, "independence_note") or "",
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


def _vocabulary(raw: str, allowed: dict[str, str]) -> str | None:
    """Une valeur du vocabulaire, ou None. Tolerante a l'orthographe rendue.

    Claude ecrit « manière » avec son accent, et l'accepter evite de perdre la
    colonne entiere sur un detail de rendu. Une valeur hors vocabulaire vaut
    « non renseigne » plutot qu'une erreur : refuser un import de vingt lignes
    pour un mot inattendu couterait plus que la ligne manquante.

    La normalisation est celle des libelles de marche — minuscules, sans
    accents — parce que c'est exactement le meme probleme : du texte ecrit a la
    main dont seule la forme varie.
    """
    value = _market_key(raw)
    if not value:
        return None
    return value if value in allowed else None


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
    angle: str = "",
    source_level: str = "",
    independence_note: str = "",
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
    # Les deux dimensions du « pourquoi » sont facultatives : cent selections
    # deja en base n'en portent aucune, et une valeur inconnue vaut « non
    # renseigne » plutot qu'un refus — le seul effet est une ligne de moins dans
    # les statistiques, comme pour un niveau de competition.
    angle_value = _vocabulary(angle, ANGLES)
    source_value = _vocabulary(source_level, SOURCE_LEVELS)

    attached = int(event_id) if str(event_id).strip().isdigit() else None
    with connect(settings) as conn:
        known = {row["key"] for row in conn.execute("SELECT key FROM tiers")}
        if tier not in known:
            raise HistoryError(f"Palier inconnu : {tier}")

        # Une seconde selection sur le meme match reclame sa justification
        # d'independance. **Seul controle bloquant du module**, et c'est
        # deliberе : ailleurs une valeur manquante vaut « non renseigne », ici
        # elle vaudrait « je ne me suis pas pose la question ». Le prompt nomme
        # precisement ce cas — multiplier les lignes d'un meme match pour
        # atteindre un quota est une facon deguisee de remplir un palier avec du
        # vide — et rien d'autre ne permet de distinguer deux angles vraiment
        # independants de deux facons de dire la meme chose.
        note = (independence_note or "").strip()
        if attached is not None and not note:
            already = conn.execute(
                "SELECT COUNT(*) AS n FROM picks WHERE session_id = ? AND event_id = ?",
                (session_id, attached),
            ).fetchone()["n"]
            if already:
                raise HistoryError(
                    "Ce match porte déjà une sélection. Une seconde ne se justifie que "
                    "sur un angle réellement indépendant : dis lequel, en une ligne."
                )

        cursor = conn.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, stake, result, angle, source_level, "
            "                   independence_note, created_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                session_id,
                attached,
                tier,
                market.strip(),
                selection.strip(),
                price_value,
                int(confidence_value) if confidence_value is not None else None,
                1 if played else 0,
                stake_value,
                result,
                angle_value,
                source_value,
                note or None,
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
class SessionRate:
    """Une session : ce qu'elle a vu, ce qu'elle a retenu, ce que ca a donne.

    Le seul bloc de la page qui mesure le **tri** plutot que les selections.
    Passer est annonce par le prompt comme un resultat valable et attendu sur
    une partie du lot ; sans denominateur, cette phrase n'etait ni verifiable
    ni suivie.
    """

    session_id: int
    label: str
    day: str
    #: Sports du lot, tels qu'ils titrent la ligne. Resolus a la lecture depuis
    #: les matchs, jamais recopies sur la session : reclasser un sport ou
    #: corriger un rattachement doit se voir ici sans reprise de donnees.
    sports: str = ""
    #: Matchs entres dans un prompt. `None` quand la session n'en a genere
    #: aucun : elle n'a rien soumis a l'analyse, et lui preter un lot de zero
    #: ferait lire un taux de selection la ou il n'y a pas de mesure.
    lot: int | None = None
    reconstructed: bool = False
    #: Matchs distincts portant au moins une selection. C'est **lui** et non le
    #: nombre de lignes qui repond a « ai-je passe ce match ? » : deux
    #: selections sur la meme rencontre sont un match retenu, pas deux.
    covered: int = 0
    picks: int = 0
    rates: RateRow = field(default_factory=lambda: RateRow("session", ""))
    #: Le prompt le plus lourd de la session. Sert de garde-fou de poids, pas
    #: de mesure de qualite.
    tokens: int = 0

    @property
    def selection_rate(self) -> float | None:
        return None if not self.lot else self.covered / self.lot

    @property
    def selection_label(self) -> str:
        return "—" if self.selection_rate is None else f"{self.selection_rate * 100:.0f} %"

    @property
    def passed(self) -> int | None:
        """Matchs du lot qu'aucune selection ne porte — le PASSE, enfin compte.

        Jamais negatif : une selection rattachee a un match hors du lot — le
        voisinage propose au rattachement en offre — fait monter `covered`
        au-dela du lot sans qu'aucun match n'ait ete passe.
        """
        return None if self.lot is None else max(0, self.lot - self.covered)

    @property
    def outside(self) -> int:
        """Selections portant sur un match absent du lot.

        Dites plutot que rabotees : elles signalent soit un rattachement au
        voisinage, soit un lot sous-enregistre, et les deux meritent d'etre vus.
        """
        return 0 if self.lot is None else max(0, self.covered - self.lot)


@dataclass
class AxisGap:
    """Un axe dont l'addition ne retombe pas sur le total tranche.

    Un regroupement qui perd des lignes en silence est la panne la plus
    couteuse que cette page puisse avoir : elle ne se voit pas, elle fait
    seulement baisser un compte que personne ne recompte. Chaque axe declare
    donc ce qu'il laisse dehors — `uncategorised`, `unlabelled_angle`, etc. — et
    ce controle verifie que la somme retombe juste.
    """

    axis: str
    missing: int
    reason: str

    @property
    def line(self) -> str:
        return f"{self.axis} : {self.missing} sélection(s) hors du compte — {self.reason}"


@dataclass
class Family:
    """Une famille de marches, et le detail fin qu'elle regroupe.

    Le detail est **entier**, sans le seuil qui filtre la carte « Par marché » :
    c'est tout l'interet du groupement — un libelle vu deux fois ne dit rien
    seul, il dit quelque chose sous sa famille, et la somme du deplie doit
    tomber juste sur le total de la ligne.
    """

    rates: RateRow
    markets: list[RateRow] = field(default_factory=list)


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
    #: Niveau de competition. Un Grand Chelem et un 250 ne se jouent ni sur le
    #: meme format ni contre les memes joueurs, une Ligue des champions et une
    #: deuxieme division non plus : leurs taux ne se melangent pas.
    by_category: list[RateRow] = field(default_factory=list)
    #: Selections tranchees qu'aucun niveau ne porte — competition non classee,
    #: ou selection sans match rattache. **Comptees, jamais mises en barre** :
    #: un taux sur cet ensemble melangerait des competitions qui n'ont en commun
    #: que de n'avoir pas ete saisies, ce qui ne dit rien des matchs. Le compte,
    #: lui, ferme l'addition — sans lui, des selections quittaient le
    #: regroupement sans qu'une seule ligne ne le signale, et c'est ainsi que
    #: tout le football est reste invisible cent paris durant.
    uncategorised: int = 0
    by_market: list[RateRow] = field(default_factory=list)
    #: Marches groupes par famille. Rendu **avant** le detail fin : neuf
    #: regroupements dont six vus une seule fois ne se lisaient pas, la ou trois
    #: familles passent le seuil.
    by_family: list[Family] = field(default_factory=list)
    #: Libelles de marche qu'aucune famille ne porte. Jamais ranges d'office
    #: dans « Autre » : le regroupement se lirait comme une decision alors que
    #: ce serait un oubli, et un marche nouveau qu'on essaie serait le premier a
    #: disparaitre dans le fourre-tout.
    unclassified_markets: int = 0
    #: **Sur quoi** la selection reposait, et non de quoi elle avait l'air. Les
    #: autres axes sont des etiquettes de forme — un palier est une bande de
    #: cote, un marche un libelle. Ces deux-la portent la seule question dont la
    #: reponse changerait la methode : une selection adossee a un fait date de
    #: niveau 1-2 tient-elle mieux qu'une lecture ?
    by_angle: list[RateRow] = field(default_factory=list)
    by_source: list[RateRow] = field(default_factory=list)
    #: Selections tranchees qui ne portent ni l'une ni l'autre. Meme role que
    #: `uncategorised` : fermer l'addition plutot que de laisser des lignes
    #: quitter un regroupement sans un mot. Les cent premieres selections sont
    #: dans ce cas, les colonnes n'existant pas encore.
    unlabelled_angle: int = 0
    unlabelled_source: int = 0
    #: Selections tranchees sans confiance annoncee. Le regroupement les ecarte
    #: — une confiance absente n'est pas un niveau de l'echelle — et le compte
    #: ferme l'addition, comme partout ailleurs.
    unlabelled_confidence: int = 0
    #: Selections tranchees dont le libelle de marche est vide. Impossible par
    #: `add_pick`, qui l'exige ; comptees quand meme, parce qu'un import ancien
    #: ou une base modifiee a la main les ferait sinon disparaitre des deux
    #: regroupements de marche sans un mot.
    unlabelled_market: int = 0
    #: Total lu **directement dans `picks`**, sans aucune jointure ni filtre.
    #: C'est le seul chiffre de la page qu'aucun regroupement ne peut faire
    #: baisser : il sert de temoin a tous les autres.
    recorded: int = 0
    #: Axes dont l'addition ne retombe pas sur `recorded`. Vide en marche
    #: normale ; non vide, la page le dit en clair plutot que d'afficher un
    #: denominateur amputé.
    gaps: list[AxisGap] = field(default_factory=list)
    #: Une ligne par session, la plus recente d'abord. Tenu hors de `groups` :
    #: les autres axes decoupent **les selections**, celui-ci decoupe le
    #: **travail**, et il porte une grandeur qu'aucun autre ne porte — le taux
    #: de selection. Le passer au detecteur de recouvrement n'aurait rien dit :
    #: une session ne recouvre par construction aucun palier ni aucun sport.
    by_session: list[SessionRate] = field(default_factory=list)
    played: RateRow = field(default_factory=lambda: RateRow("played", "Jouées"))
    skipped: RateRow = field(default_factory=lambda: RateRow("skipped", "Écartées"))
    #: Marches ecartes faute d'echantillon. Annonce plutot que tue en silence.
    hidden_markets: int = 0
    #: Regroupements de deux axes differents qui portent les memes selections.
    #: Signales, jamais masques : le bloc reste affiche, c'est sa redondance qui
    #: est dite. Le masquer choisirait a la place du lecteur lequel des deux
    #: axes est le bon, et rien ici ne permet de trancher ca.
    overlaps: list[Overlap] = field(default_factory=list)
    #: Selections dont l'angle declare est une **maniere** et dont le marche
    #: rendu appartient a la famille `Issue`. Le prompt demandait a l'analyse de
    #: compter ces lignes elle-meme ; les deux colonnes etant en base, le compte
    #: se fait ici — et se mesure enfin dans le temps.
    conflicts: Conflict = field(default_factory=lambda: Conflict())

    @property
    def empty(self) -> bool:
        return self.settled == 0

    @property
    def consistent(self) -> bool:
        """Tout ce qui est tranche en base est compte ici, et dans chaque axe."""
        return self.settled == self.recorded and not self.gaps

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
            self.by_angle,
            self.by_source,
            [entry.rates for entry in self.by_family],
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

    @staticmethod
    def _compare(rows: list[RateRow]) -> Comparison | None:
        """Les deux plus gros regroupements d'un axe, s'ils se lisent.

        Les deux plus gros et non deux cles fixees : c'est ce que l'oeil compare
        sur un graphique a barres, et la paire suit le lot au lieu de rester
        collee a des paliers qui pourraient ne plus etre les plus employes.
        """
        candidates = sorted((row for row in rows if row.settled), key=lambda row: -row.settled)
        if len(candidates) < 2:
            return None
        first, second = candidates[0], candidates[1]
        # Le meilleur taux en premier : l'ecart se lit alors dans le sens ou la
        # phrase le raconte, « X fait mieux que Y ».
        if (first.rate or 0) < (second.rate or 0):
            first, second = second, first
        return Comparison(
            left=first,
            right=second,
            required=required_sample(first.rate or 0.0, second.rate or 0.0),
        )

    @property
    def tier_comparison(self) -> Comparison | None:
        return self._compare(self.by_tier)

    @property
    def confidence_comparison(self) -> Comparison | None:
        return self._compare(self.by_confidence)

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


#: Famille de marches qui ne retient d'un raisonnement que le nom d'un camp.
#: Un angle declare sur une **maniere** — un rythme, une usure, un desequilibre
#: au service — qui sort en `Issue` a perdu en route ce qu'il avait compris.
ISSUE_FAMILY = "issue"


@dataclass
class Conflict:
    """Selections dont l'angle declare et le marche rendu ne s'accordent pas.

    Le prompt demandait a l'analyse de s'auto-auditer : « compte tes lignes
    avant de rendre, si plus de la moitie du tableau porte sur le vainqueur,
    relis-les avec leur colonne Type ». Or les deux colonnes sont **en base** —
    `angle` depuis la migration 026, la famille du marche depuis la 027 — et le
    conflit se detecte en une requete. Une regle deterministe laissee au modele
    coute des tokens, se refait a chaque session et ne se mesure jamais.

    **C'est une mesure de la qualite du rendu, jamais un blocage** : un angle de
    maniere rendu en vainqueur reste une selection valable, simplement moins
    fidele a son propre raisonnement. On la compte, on ne la refuse pas.
    """

    #: Selections tranchees portant `maniere` et rendues dans la famille `Issue`.
    count: int = 0
    #: Selections tranchees portant `maniere`, quel que soit le marche rendu.
    labelled: int = 0
    #: Le meme compte par sport, puis par session — « dans le temps » etant la
    #: seule facon de voir si la consigne porte, ou si elle s'use.
    by_sport: list[tuple[str, int, int]] = field(default_factory=list)
    by_session: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def known(self) -> bool:
        """Au moins une selection declare son angle. Sinon rien ne se mesure."""
        return self.labelled > 0

    @property
    def rate(self) -> float | None:
        return None if not self.labelled else self.count / self.labelled


def conflicts(rows: list[Any], families: dict[str, str], tz: str) -> Conflict:
    """Compte les selections `maniere` rendues dans la famille `Issue`.

    Calcule **a la lecture**, jamais recopie sur la selection : c'est la regle du
    module — reclasser un marche reclasse tout l'historique, sans migration ni
    reprise de donnees. Stocker le conflit figerait la taxonomie du jour ou la
    ligne a ete saisie.
    """
    report = Conflict()
    par_sport: dict[str, list[int]] = {}
    par_session: dict[str, list[int]] = {}
    for row in rows:
        if row["result"] not in ("win", "loss") or row["angle"] != ANGLE_MANNER:
            continue
        report.labelled += 1
        heurte = family_of(row["market"] or "", families) == ISSUE_FAMILY
        report.count += 1 if heurte else 0
        sport = _column(row, "sport_key") or NO_SPORT
        jour = _local(str(row["created_at"]), tz).strftime("%d/%m")
        for cle, table in ((sport, par_sport), (jour, par_session)):
            compte = table.setdefault(cle, [0, 0])
            compte[0] += 1 if heurte else 0
            compte[1] += 1
    report.by_sport = sorted((cle, *valeurs) for cle, valeurs in par_sport.items())
    report.by_session = [(cle, *par_session[cle]) for cle in sorted(par_session, reverse=True)]
    return report


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
    #: Sessions ou ce niveau n'a **pas** ete employe, sur celles qui portent au
    #: moins une selection. C'est la mesure de la **vacance** : une part de
    #: volume a zero dit qu'un niveau ne sert jamais, elle ne dit pas si c'est
    #: parce qu'un lot ne l'offrait pas ou parce qu'on ne l'a jamais cherche.
    #: Compte des sessions, la ou la part compte des lignes.
    absent_sessions: int = 0

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
    #: Sessions portant au moins une selection — le denominateur de la vacance.
    sessions: int = 0

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


def _count_vacancy(block: Mix, rows: list[Any], column: str) -> None:
    """Compte, par niveau, les sessions qui ne l'ont pas employe.

    Le brief demandait de compter les lignes de vacance ecrites sous le tableau ;
    l'application ne lit pas la prose du rendu, seulement le tableau des
    selections. Mais la vacance **est** dans les donnees : un palier qu'aucune
    selection de la session ne porte est un palier laisse vide ce jour-la.
    C'est la meme grandeur, mesuree sans rien parser.

    Une part de volume a zero dit qu'un niveau ne sert jamais. Ce compte-ci dit
    **a quel rythme** : un palier absent de cinq sessions sur cinq n'est pas dans
    le meme etat qu'un palier employe une fois sur deux, et c'est cette
    difference qui decidera un jour de reduire l'echelle.
    """
    sessions: dict[str, set[int]] = {}
    seen: set[int] = set()
    for row in rows:
        session_id = int(row["session_id"])
        seen.add(session_id)
        value = row[column]
        if value is not None:
            sessions.setdefault(str(value), set()).add(session_id)
    block.sessions = len(seen)
    for entry in block.rows:
        entry.absent_sessions = block.sessions - len(sessions.get(entry.key, set()))


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
        rows = conn.execute("SELECT session_id, tier, confidence FROM picks").fetchall()

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
    _count_vacancy(confiance, rows, "confidence")

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
    _count_vacancy(palier, rows, "tier")

    return [confiance, palier]


def _rate_tally(entries: list[tuple[str, str, str, Any]], minimum: int = 1) -> list[RateRow]:
    """Agrege des quadruplets (cle, libelle, resultat, ligne) en lignes de taux."""
    grouped: dict[str, RateRow] = {}
    for key, label, result, row in entries:
        _count(grouped.setdefault(key, RateRow(key=key, label=label)), result, row)
    return [row for row in grouped.values() if row.settled >= minimum]


def _by_family(tally: list[RateRow], known: dict[str, str]) -> tuple[list[Family], int]:
    """Groupe les marches par famille. Rend aussi ce qui n'en a aucune.

    Une cle inconnue **ne tombe pas dans « Autre »** : `autre` est une decision
    prise marche par marche, pas le fourre-tout de ce qu'on n'a pas regarde. Le
    compte des non classes est rendu a part, et les reglages les reclament.
    """
    grouped: dict[str, Family] = {}
    orphans = 0
    for entry in tally:
        family = known.get(family_key(entry.key))
        if family is None:
            orphans += entry.settled
            continue
        target = grouped.setdefault(
            family, Family(rates=RateRow(key=family, label=family_label(family)))
        )
        target.rates.merge(entry)
        target.markets.append(entry)

    found = [entry for entry in grouped.values() if entry.rates.settled]
    for entry in found:
        entry.markets.sort(key=lambda item: (-item.settled, item.label))
    found.sort(key=lambda item: family_rank(item.rates.key))
    return found, orphans


def _by_session(
    sessions: list[Any],
    picks: list[Any],
    known: dict[int, Lot],
    sport_labels: dict[str, str],
    tz: str,
) -> list[SessionRate]:
    """Une ligne par session : le lot vu, les matchs retenus, ce que ca a donne.

    Les sessions sans lot connu sont **gardees et marquees**, jamais retirees :
    une session qui n'a genere aucun prompt n'a rien soumis a l'analyse, et la
    faire disparaitre laisserait croire qu'elle n'a pas eu lieu.
    """
    grouped: dict[int, list[Any]] = {}
    for row in picks:
        grouped.setdefault(int(row["session_id"]), []).append(row)

    found = []
    for row in sessions:
        session_id = int(row["id"])
        lot = known.get(session_id)
        mine = grouped.get(session_id, [])
        # Le lot dit les sports quand il est enregistre ; sinon les selections
        # les disent, ce qui rate seulement une session ou l'on n'a rien retenu.
        # Les deux valent mieux qu'une colonne vide sur tout l'historique.
        sports = [name for name in (row["sports"] or "").split(",") if name] or sorted(
            {sport_labels[key] for pick in mine if (key := pick["sport_key"]) in sport_labels}
        )
        entry = SessionRate(
            session_id=session_id,
            label=row["label"] or f"Session {session_id}",
            day=_local(row["created_at"], tz).strftime("%d/%m"),
            sports=" · ".join(sports),
            lot=None if lot is None else lot.size,
            reconstructed=bool(lot and lot.reconstructed),
            # Les matchs distincts, et non les lignes : deux selections sur la
            # meme rencontre sont un match retenu, pas deux.
            covered=len({row["event_id"] for row in mine if row["event_id"]}),
            picks=len(mine),
            rates=RateRow(key=str(session_id), label=f"Session {session_id}"),
            tokens=int(row["tokens"] or 0),
        )
        for pick in mine:
            _count(entry.rates, pick["result"] or "pending", pick)
        found.append(entry)
    return found


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
            "SELECT k.id, k.session_id, k.tier, k.result, k.market, k.confidence, k.played, "
            "       k.event_id, k.created_at, k.price, k.angle, k.source_level, "
            "       s.key AS sport_key, c.category FROM picks k "
            "LEFT JOIN events e ON e.id = k.event_id "
            "LEFT JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id"
        ).fetchall()
        # Le temoin : compte direct, sans jointure ni filtre. C'est lui qui
        # rend le denominateur verifiable — une regression de jointure le
        # laisserait intact et ferait bouger tout le reste.
        recorded = conn.execute(
            "SELECT COUNT(*) AS n FROM picks WHERE result IN ('win', 'loss')"
        ).fetchone()["n"]
        sessions = conn.execute(
            "SELECT s.id, s.label, s.created_at, "
            "  (SELECT COALESCE(MAX(p.token_estimate), 0) FROM prompts p "
            "     WHERE p.session_id = s.id) AS tokens, "
            "  (SELECT GROUP_CONCAT(DISTINCT sp.label) FROM prompts p "
            "     JOIN prompt_events pe ON pe.prompt_id = p.id "
            "     JOIN events ev ON ev.id = pe.event_id "
            "     JOIN sports sp ON sp.id = ev.sport_id "
            "     WHERE p.session_id = s.id) AS sports "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC"
        ).fetchall()

    report = Analysis()
    report.recorded = int(recorded)
    report.by_session = _by_session(sessions, rows, lots(settings), sport_labels, settings.tz)
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

    # Le niveau n'existe que la ou il a ete renseigne : une **barre** « non
    # renseigne » ne dirait rien sur les matchs, seulement sur la saisie — un
    # taux moyen de tout ce qui n'a pas ete classe n'a aucune coherence
    # sportive. Le **compte**, lui, est une information juste, et il ferme
    # l'addition : la somme des niveaux plus `uncategorised` vaut `settled`.
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
    report.uncategorised = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and not row["category"]
    )

    # Le « pourquoi ». L'ordre suit l'echelle et non l'effectif : « Manière »
    # apres « Issue », les sources du plus officiel au plus incertain, et
    # « Lecture seule » ferme la marche — c'est la lecture qu'on veut comparer
    # au reste, et la voir a sa place dans une echelle vaut mieux que de la
    # voir en tete parce qu'elle est la plus nombreuse.
    report.by_angle = sorted(
        _rate_tally(
            [
                (row["angle"], ANGLES[row["angle"]], result, row)
                for row, result in zip(rows, results, strict=True)
                if row["angle"] in ANGLES
            ]
        ),
        key=lambda item: list(ANGLES).index(item.key),
    )
    report.by_source = sorted(
        _rate_tally(
            [
                (row["source_level"], SOURCE_LEVELS[row["source_level"]], result, row)
                for row, result in zip(rows, results, strict=True)
                if row["source_level"] in SOURCE_LEVELS
            ]
        ),
        key=lambda item: list(SOURCE_LEVELS).index(item.key),
    )
    report.unlabelled_angle = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["angle"] not in ANGLES
    )
    report.unlabelled_source = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["source_level"] not in SOURCE_LEVELS
    )
    report.unlabelled_confidence = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and row["confidence"] is None
    )
    report.unlabelled_market = sum(
        1
        for row, result in zip(rows, results, strict=True)
        if result in ("win", "loss") and not _market_key(row["market"])
    )

    markets = [
        (_market_key(row["market"]), (row["market"] or "").strip(), result, row)
        for row, result in zip(rows, results, strict=True)
        if _market_key(row["market"])
    ]
    # Un seul comptage, deux vues : la carte fine applique son seuil, le deplie
    # d'une famille non. Les recalculer separement les aurait fait diverger, et
    # la somme du deplie n'aurait plus tombe juste sur sa ligne de famille.
    tally = _rate_tally(markets)
    report.by_market = sorted(
        (entry for entry in tally if entry.settled >= ANALYSIS_MIN_MARKET),
        key=lambda item: (-item.settled, item.label),
    )
    report.hidden_markets = len(tally) - len(report.by_market)
    report.by_family, report.unclassified_markets = _by_family(tally, load_families(settings))

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
            # Le « pourquoi » entre dans le detecteur comme les autres, et il en
            # a besoin plus qu'eux : un lot ou toutes les manieres se traduisent
            # en totaux ferait de « Manière » et « O/U » deux noms du meme
            # echantillon, et la page les presenterait comme deux constats.
            ("angle", report.by_angle),
            ("source", report.by_source),
        ]
    )

    # Le conflit entre l'angle declare et le marche rendu. Calcule a la lecture
    # comme la famille elle-meme : reclasser un marche reclasse tout
    # l'historique, et le figer sur la selection perdrait cette propriete.
    report.conflicts = conflicts(rows, load_families(settings), settings.tz)

    report.gaps = _audit(report, tally)
    return report


def _audit(report: Analysis, tally: list[RateRow]) -> list[AxisGap]:
    """Verifie que chaque axe additionne bien toutes les selections tranchees.

    Le total, lui, ne peut pas baisser : il est compte sur les lignes brutes,
    sans jointure. Ce sont les **regroupements** qui peuvent perdre du monde —
    une cle inconnue, un champ nul, une jointure devenue stricte — et ils le
    font sans bruit. Le controle rend le silence impossible.

    `by_market` s'audite sur le comptage **complet** et non sur les lignes
    affichees : la carte en ecarte volontairement les marches vus une seule
    fois, et les compter comme perdus ferait crier a la panne sur une regle.
    """
    total = report.recorded
    axes = [
        ("Palier", sum(row.settled for row in report.by_tier), 0, "palier inconnu"),
        (
            "Confiance",
            sum(row.settled for row in report.by_confidence),
            report.unlabelled_confidence,
            "confiance non annoncée",
        ),
        ("Sport", sum(row.settled for row in report.by_sport), 0, "sport non résolu"),
        (
            "Niveau",
            sum(row.settled for row in report.by_category),
            report.uncategorised,
            "compétition à classer",
        ),
        (
            "Marché",
            sum(row.settled for row in tally),
            report.unlabelled_market,
            "libellé de marché vide",
        ),
        (
            "Famille",
            sum(entry.rates.settled for entry in report.by_family),
            report.unclassified_markets + report.unlabelled_market,
            "marché à classer",
        ),
        (
            "Type d'angle",
            sum(row.settled for row in report.by_angle),
            report.unlabelled_angle,
            "type non renseigné",
        ),
        (
            "Niveau de source",
            sum(row.settled for row in report.by_source),
            report.unlabelled_source,
            "source non renseignée",
        ),
    ]
    return [
        AxisGap(axis=nom, missing=total - compte - declares, reason=motif)
        for nom, compte, declares, motif in axes
        if compte + declares != total
    ]


# -- Retour d'experience, pour le prompt ------------------------------------

#: Fenetre du retour : les N derniers picks tranches. Au-dela on parlerait
#: d'une autre saison, d'autres competitions et d'une autre facon de jouer.
FEEDBACK_WINDOW = 60

#: Sessions sur lesquelles se lit le taux de selection median injecte au prompt.
FEEDBACK_SESSIONS = 10

#: Sous ce nombre de sessions dotees d'un lot, aucun taux de selection n'est
#: publie. Meme regle que partout : une mediane sur deux valeurs est la moyenne
#: des deux, et sur une seule c'est cette session-la. Trois est le premier
#: nombre ou le mot « mediane » decrit autre chose que l'echantillon entier.
FEEDBACK_MIN_SESSIONS = 3

# Les trois seuils de publication — volume, ligne, etalement — vivent avec ceux
# de la page, plus haut : ils sont communs aux deux surfaces.


@dataclass
class FeedbackRow:
    """Un regroupement et son taux. Ni mise, ni gain, ni esperance.

    **Aucun champ de prix ici, et c'est deliberе** : l'ecart au taux implicite
    existe sur la page, ou il se lit a cote d'autres ecarts. L'injecter dans le
    prompt rapprocherait un taux de reussite d'une cote, c'est a dire calculerait
    une esperance — interdit de la section 9, et le fait que le chiffre vienne de
    l'historique de l'utilisateur n'y change rien.
    """

    key: str
    label: str
    won: int = 0
    lost: int = 0
    #: Bande cible, sur les seuls regroupements par confiance. C'est le seul
    #: referentiel du bloc qui parle de la **notation** plutot que des matchs :
    #: un sport ne se fixe pas d'objectif de taux, une confiance annoncee si —
    #: c'est meme sa definition.
    band: Band | None = None

    @property
    def settled(self) -> int:
        return self.won + self.lost

    @property
    def rate(self) -> float | None:
        return None if self.settled == 0 else self.won / self.settled

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def gap(self) -> float | None:
        """Ecart en points a la borne la plus proche de la bande.

        None quand le taux tombe **dans** la bande : il n'y a alors rien a
        corriger, et ecrire « écart 0 pt » ferait chercher un probleme absent.
        """
        if self.band is None or self.rate is None:
            return None
        observed = self.rate * 100
        if observed < self.band.low:
            return observed - self.band.low
        if self.band.high is not None and observed > self.band.high:
            return observed - self.band.high
        return None

    @property
    def off_band(self) -> bool:
        """L'ecart est **confirme par l'intervalle**, pas seulement observe.

        Meme regle que la page, et elle compte plus encore ici : au volume
        courant presque chaque intervalle couvre plusieurs bandes, et faire
        resserrer une notation sur du bruit orienterait plus surement qu'aucun
        chiffre. La ligne dit alors l'ecart sans le mot.
        """
        bounds = self.interval
        return self.band is not None and bounds is not None and self.band.excludes(bounds)

    #: Largeur du libelle dans le prompt. Un nom de competition depasse volontiers
    #: la largeur d'un palier : sans troncature, une seule ligne longue casse
    #: l'alignement de tout le bloc et le rend penible a lire.
    LABEL_WIDTH = 20

    @property
    def line(self) -> str:
        """`🔴 GIGA FUN         2/14    14 %`, aligne comme le reste du prompt.

        Les lignes de confiance portent en plus leur bande cible et l'ecart :
        `confiance 3          25/63   40 %   cible 50 – 60 %, écart -10 pts`.
        """
        if self.rate is None:
            return self.label
        label = self.label
        if len(label) > self.LABEL_WIDTH:
            label = label[: self.LABEL_WIDTH - 1] + "…"
        compte = f"{self.won}/{self.settled}"
        line = f"{label:<{self.LABEL_WIDTH}} {compte:<7} {self.rate * 100:.0f} %"
        if self.band is None:
            return line
        line += f"   cible {self.band.label}"
        if self.gap is not None:
            line += f", écart {self.gap:+.0f} pts"
            if self.off_band:
                line += ", hors bande"
        return line


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
    #: Selections tranchees **de tout l'historique**. Sans elle, `settled`
    #: plafonne a `window` et se lit comme un total : « 60 selections tranchees
    #: enregistrees » sur une base qui en porte cent a fait croire a une perte
    #: de donnees. Les deux nombres cote a cote rendent la fenetre visible.
    recorded: int = 0
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
    #: Part mediane du lot effectivement selectionnee sur les dernieres
    #: sessions. C'est la seule grandeur du bloc qui ne parle pas de resultats
    #: mais du **tri** : le prompt annonce que passer est un resultat valable et
    #: attendu sur une partie du lot, et rien ne disait jusqu'ici ce qu'il en
    #: etait. Elle se donne comme un constat, jamais comme un objectif — se
    #: fixer un quota de passes ferait ecarter un match pour remplir un compte.
    selection_median: float | None = None
    #: Sessions derriere cette mediane. Le compte accompagne toujours le taux.
    selection_sessions: int = 0

    @property
    def empty(self) -> bool:
        """Rien a dire du tout : le bloc disparait entierement du prompt.

        Le taux de selection entre dans la condition parce qu'il ne depend
        d'aucun resultat : une installation qui a monte trois sessions sans
        encore saisir un seul resultat a bien quelque chose a dire sur son tri.
        """
        return self.settled == 0 and self.selection_median is None

    @property
    def scope_line(self) -> str:
        """« mes 60 dernières tranchées, sur 100 enregistrées ».

        Le second nombre n'est la que pour empecher de lire le premier comme un
        total. Il disparait tant que la fenetre ne mord pas.
        """
        base = f"mes {self.settled} dernière(s) sélection(s) tranchée(s)"
        return base if self.recorded <= self.settled else f"{base}, sur {self.recorded} au total"

    @property
    def missing_note(self) -> str:
        """Ce qui manque **exactement** : le volume, l'etalement, ou les deux.

        Le texte annoncait les deux seuils quel que soit celui qui bloquait, si
        bien qu'un bloc de 60 selections lisait « il en faudrait au moins 40 » —
        une phrase qui se contredit et fait chercher une panne la ou il n'y a
        qu'un etalement trop court.
        """
        volume = self.settled < self.minimum
        etalement = self.days < self.minimum_days
        if volume and etalement:
            return f"il en faudrait {self.minimum}, réparties sur {self.minimum_days} journées"
        if volume:
            return (
                f"l'étalement suffit ; c'est le volume qui manque — il en faudrait {self.minimum}"
            )
        return (
            "le volume suffit ; c'est l'étalement qui manque — il faudrait "
            f"{self.minimum_days} journées d'analyse distinctes"
        )

    @property
    def selection_line(self) -> str:
        """« 36 % en médiane, sur 6 sessions ». Vide sous le seuil.

        Le compte accompagne le taux, comme partout ailleurs : une mediane sur
        trois sessions et une mediane sur trente ne disent pas la meme chose.
        """
        if self.selection_median is None:
            return ""
        pluriel = "s" if self.selection_sessions > 1 else ""
        return (
            f"{self.selection_median * 100:.0f} % en médiane, "
            f"sur {self.selection_sessions} session{pluriel}"
        )

    @property
    def enough(self) -> bool:
        """Assez de recul pour qu'un pourcentage veuille dire quelque chose.

        Deux conditions, et il faut les deux : assez de selections, et assez de
        journees. Un lot nombreux mais concentre sur quelques jours mesure ces
        jours-la — un tournoi, une soiree de coupe d'Europe, une meteo — et le
        prompt le presenterait comme un ordre de passage durable.
        """
        return self.settled >= self.minimum and self.days >= self.minimum_days


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


def _selection_median(settings: Settings) -> tuple[float | None, int]:
    """Part mediane du lot selectionnee sur les `FEEDBACK_SESSIONS` dernieres.

    La mediane et non la moyenne : une session ou l'on n'a rien retenu — il y en
    a une dans l'historique reel, 0 sur 34 — tirerait une moyenne vers le bas
    au point de decrire une prudence qui n'existe pas le reste du temps.

    Seules les sessions **dotees d'un lot** comptent : une session qui n'a
    genere aucun prompt n'a rien soumis a l'analyse, et lui preter un lot de
    zero inventerait un taux.
    """
    known = lots(settings)
    if not known:
        return None, 0
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT s.id, "
            "  (SELECT COUNT(DISTINCT k.event_id) FROM picks k "
            "     WHERE k.session_id = s.id AND k.event_id IS NOT NULL) AS covered "
            "FROM sessions s ORDER BY s.created_at DESC, s.id DESC LIMIT ?",
            (FEEDBACK_SESSIONS,),
        ).fetchall()

    # Borne a 1 : une selection rattachee a un match hors du lot — le voisinage
    # propose au rattachement en offre — ferait sinon une part de lot au-dessus
    # de cent pour cent, qui ne veut rien dire. La page, elle, montre l'ecart.
    parts = sorted(
        min(1.0, int(row["covered"]) / lot.size)
        for row in rows
        if (lot := known.get(int(row["id"]))) and lot.size
    )
    if len(parts) < FEEDBACK_MIN_SESSIONS:
        return None, len(parts)
    middle = len(parts) // 2
    median = (
        parts[middle] if len(parts) % 2 else (parts[middle - 1] + parts[middle]) / 2  # noqa: E501
    )
    return median, len(parts)


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
    with connect(settings) as conn:
        recorded = conn.execute(
            "SELECT COUNT(*) AS n FROM picks WHERE result IN ('win', 'loss')"
            + (" AND played = 1" if played_only else "")
        ).fetchone()["n"]

    report = Feedback(
        settled=len(rows),
        days=len({str(row["created_at"])[:10] for row in rows}),
        recorded=int(recorded),
    )
    # Le taux de selection est publie **hors** des trois garde-fous ci-dessous,
    # et ce n'est pas un oubli : eux protegent des taux de reussite, qui
    # mesurent des issues. Celui-ci decrit un comportement — comment je trie —
    # et une part du lot ne devient pas trompeuse parce que les resultats
    # manquent. Meme exemption que `labelling()` sur la page.
    report.selection_median, report.selection_sessions = _selection_median(settings)

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
    # La bande cible se rattache ici et nulle part ailleurs, comme sur la page :
    # un sport ou un marche ne se fixe pas d'objectif de taux. Sans elle,
    # « confiance 4 » n'etait qu'un nombre sans referentiel, et le prompt
    # affirmait pourtant qu'un ecart disait la derive de la notation.
    bands = load_bands(settings)
    for entry in report.by_confidence:
        entry.band = bands.get(int(entry.key)) if entry.key.isdigit() else None

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
