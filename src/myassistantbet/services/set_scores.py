"""Le score exact en sets : ce que l'analyse annonce, et ce qui est arrive.

La section D du prompt impose une ligne de score en sets par match de tennis, a
chaque session. Elle n'etait ni enregistree ni verifiee — ecrite dans le rendu,
lue une fois, puis perdue.

C'est pourtant la **seule mesure de la lecture de la maniere qui soit
independante de tout prix** : quatre issues, verifiables sur n'importe quelle
feuille de match. Le reste de la page melange toujours un jugement sportif et un
palier de cote ; ici, non.

Deux grandeurs en sortent, et la seconde est la plus interessante :

- l'**exactitude** — le score annonce est tombe ;
- l'**issue juste, maniere fausse** — le bon vainqueur, le mauvais nombre de
  sets. C'est le cas qui dit qu'un raisonnement sur le rythme n'a pas porte,
  alors meme que la selection a pu gagner.

Aucun calcul financier, aucune cote : The Odds API ne sert pas ce marche, et le
prompt interdit deja d'en inventer une.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .labels import affiche

logger = logging.getLogger(__name__)

#: Les quatre issues, du point de vue du **premier joueur nomme** — comme le
#: handicap jeux et comme le H2H. Deux conventions dans la meme base se liraient
#: a l'envers, et c'est l'erreur la plus couteuse que ce module puisse produire.
SCORES: dict[str, str] = {
    "2-0": "2-0",
    "2-1": "2-1",
    "1-2": "1-2",
    "0-2": "0-2",
}

#: Les scores ou le premier joueur nomme l'emporte. Sert a separer « issue
#: juste » de « maniere juste » — la seule distinction qui rende ce releve utile.
FIRST_WINS = frozenset({"2-0", "2-1"})


class SetScoreError(ValueError):
    """Saisie invalide. Le message est affiche tel quel a l'utilisateur."""


def _clean(value: str | None) -> str | None:
    """Une issue du vocabulaire, ou None. Hors vocabulaire vaut « non renseigne ».

    Meme regle que `angle` et `source_level` : refuser une saisie entiere pour un
    mot inattendu couterait plus que la valeur manquante. La saisie passe de
    toute facon par un menu ferme — ce controle garde la route, pas l'ecran.
    """
    text = (value or "").strip()
    return text if text in SCORES else None


@dataclass
class Row:
    """Un match du lot et son score, annonce puis constate."""

    event_id: int
    label: str
    competition: str
    predicted: str = ""
    alternate: str = ""
    actual: str = ""

    @property
    def saisi(self) -> bool:
        return bool(self.predicted)

    @property
    def exact(self) -> bool | None:
        """Le score annonce est tombe. None tant que le resultat manque."""
        if not self.actual or not self.predicted:
            return None
        return self.predicted == self.actual

    @property
    def alternate_exact(self) -> bool | None:
        """Le **second** scenario est tombe. Compte a part de l'exactitude.

        Le fusionner avec le premier gonflerait le taux d'un joker que le prompt
        rend facultatif : deux scores proposes ne valent pas une lecture deux
        fois plus juste.
        """
        if not self.actual or not self.alternate:
            return None
        return self.alternate == self.actual

    @property
    def issue_ok(self) -> bool | None:
        """Le bon vainqueur, quel que soit le nombre de sets."""
        if not self.actual or not self.predicted:
            return None
        return (self.predicted in FIRST_WINS) == (self.actual in FIRST_WINS)


def lot(session_id: int, settings: Settings | None = None) -> list[Row]:
    """Les matchs de tennis **du lot analyse**, avec leur score deja saisi.

    Le lot et non la shortlist : `session_events` se vide a mesure qu'on decoche,
    quand `prompt_events` enregistre ce qui est vraiment parti a l'analyse. C'est
    la meme population que le taux de selection, et c'est bien de ces matchs-la
    que le prompt reclame un score.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.id, e.home, e.away, e.commence_time, "
            "       COALESCE(c.label, '—') AS competition, "
            "       x.predicted, x.alternate, x.actual "
            "FROM prompt_events pe "
            "JOIN prompts p ON p.id = pe.prompt_id "
            "JOIN events e ON e.id = pe.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "LEFT JOIN set_scores x ON x.event_id = e.id AND x.session_id = p.session_id "
            "WHERE p.session_id = ? AND s.key = 'tennis' "
            "ORDER BY e.commence_time, e.id",
            (session_id,),
        ).fetchall()
    return [
        Row(
            event_id=int(row["id"]),
            label=affiche(row["home"], row["away"]),
            competition=row["competition"],
            predicted=row["predicted"] or "",
            alternate=row["alternate"] or "",
            actual=row["actual"] or "",
        )
        for row in rows
    ]


def save(
    session_id: int,
    event_id: int,
    predicted: str = "",
    alternate: str = "",
    actual: str = "",
    settings: Settings | None = None,
) -> None:
    """Enregistre ou corrige le score d'un match. Sans score annonce, la ligne part.

    Un score annonce vide veut dire « pas de scenario sur ce match » — le prompt
    l'autorise explicitement, `PASSE` etant une reponse attendue. Le stocker sous
    forme de ligne vide ferait compter au denominateur un match sur lequel rien
    n'a ete annonce.
    """
    settings = settings or get_settings()
    annonce, second, reel = _clean(predicted), _clean(alternate), _clean(actual)
    if second is not None and second == annonce:
        raise SetScoreError("Le second scénario doit différer du premier.")

    with connect(settings) as conn:
        if annonce is None:
            conn.execute(
                "DELETE FROM set_scores WHERE session_id = ? AND event_id = ?",
                (session_id, event_id),
            )
            return
        conn.execute(
            "INSERT INTO set_scores (session_id, event_id, predicted, alternate, actual, "
            "                        created_at) VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(session_id, event_id) DO UPDATE SET "
            "  predicted = excluded.predicted, alternate = excluded.alternate, "
            "  actual = excluded.actual",
            (session_id, event_id, annonce, second, reel, utcnow()),
        )
    logger.info(
        "Score en sets — session %d, match %d : %s%s%s",
        session_id,
        event_id,
        annonce,
        f" / {second}" if second else "",
        f" → {reel}" if reel else "",
    )


@dataclass
class Report:
    """Ce que la lecture de la maniere vaut, mesuree sans aucun prix."""

    settled: int = 0
    exact: int = 0
    #: Le second scenario tombe, quand le premier n'y etait pas. Compte a part :
    #: deux scores proposes ne valent pas une lecture deux fois plus juste.
    alternate: int = 0
    #: **Le chiffre le plus interessant du bloc** : le bon vainqueur, le mauvais
    #: nombre de sets. Il dit qu'un raisonnement sur le rythme n'a pas porte, et
    #: il le dit meme quand la selection, elle, a gagne.
    issue_only: int = 0
    #: Ni l'un ni l'autre.
    missed: int = 0
    #: Matrice 4x4 : annonce en ligne, constate en colonne.
    matrix: dict[str, dict[str, int]] = field(default_factory=dict)
    #: Scores annonces mais dont le resultat n'a pas encore ete saisi.
    pending: int = 0

    @property
    def empty(self) -> bool:
        return self.settled == 0 and self.pending == 0

    @property
    def exact_rate(self) -> float | None:
        return None if not self.settled else self.exact / self.settled

    @property
    def issue_rate(self) -> float | None:
        """Part des scores dont **au moins** le vainqueur etait bon."""
        if not self.settled:
            return None
        return (self.exact + self.issue_only) / self.settled


def report(settings: Settings | None = None) -> Report:
    """Exactitude, issue juste et matrice de confusion, sur tout l'historique.

    Aucun seuil de publication ici, contrairement aux taux de reussite : ce
    n'est pas un taux par regroupement dont il faudrait se mefier sur trois
    lignes, c'est un compte unique. Le denominateur est ecrit a cote, comme
    partout ailleurs, et c'est lui qui dit ce qu'il vaut.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute("SELECT predicted, alternate, actual FROM set_scores").fetchall()

    found = Report()
    found.matrix = {annonce: dict.fromkeys(SCORES, 0) for annonce in SCORES}
    for row in rows:
        entry = Row(
            event_id=0,
            label="",
            competition="",
            predicted=row["predicted"] or "",
            alternate=row["alternate"] or "",
            actual=row["actual"] or "",
        )
        if not entry.actual:
            found.pending += 1
            continue
        found.settled += 1
        found.matrix[entry.predicted][entry.actual] += 1
        if entry.exact:
            found.exact += 1
        elif entry.alternate_exact:
            found.alternate += 1
            found.issue_only += 1 if entry.issue_ok else 0
        elif entry.issue_ok:
            found.issue_only += 1
        else:
            found.missed += 1
    return found


def matrix_rows(found: Report) -> list[tuple[str, list[int], int]]:
    """La matrice, prete a etre rendue : (annonce, comptes, total de la ligne)."""
    return [
        (
            annonce,
            [found.matrix[annonce][reel] for reel in SCORES],
            sum(found.matrix[annonce].values()),
        )
        for annonce in SCORES
    ]
