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
import re
import unicodedata
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .inference import wilson
from .ingestion import SCHEMA_INVALID, SCORE_SETS, Reject
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


#: Le mot que le gabarit impose quand aucun scenario ne se dessine. **C'est une
#: reponse attendue et non une absence** : la compter au denominateur des lignes
#: produites est ce qui distingue « l'analyse a repondu PASSE sur huit matchs »
#: de « rien n'a ete collé ». Elle ne produit aucune prediction pour autant.
PASS = "PASSE"

#: Un score en sets, tel qu'il s'ecrit dans un rendu. Le tiret peut etre long,
#: court ou insecable selon ce que le rendu a produit et ce que le copier-coller
#: en a fait — comparer le caractere brut ferait echouer la lecture pour une
#: raison typographique, c'est-a-dire pour la mauvaise.
SCORE = re.compile(r"\b([0-2])\s*[-–—−]\s*([0-2])\b")

#: Un repere de bloc, comme partout ailleurs dans l'ingestion.
MARK = re.compile(r"\bM(\d+)\b")


def _fold(text: str) -> str:
    decomposed = unicodedata.normalize("NFKD", text or "")
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    return " ".join(re.sub(r"[^a-z0-9]+", " ", stripped.lower()).split())


@dataclass(frozen=True)
class ParsedScore:
    """Une ligne de score en sets lue dans un rendu, avant tout rapprochement."""

    #: Le repere de bloc (`M4`), quand la ligne en porte un. C'est la cle la plus
    #: sure : elle ne depend d'aucune orthographe d'affiche.
    mark: str = ""
    #: L'affiche telle qu'elle est ecrite. Sert quand le repere manque.
    match_text: str = ""
    predicted: str = ""
    alternate: str = ""

    @property
    def passe(self) -> bool:
        """L'analyse a repondu `PASSE`. **Une reponse, pas une absence.**"""
        return not self.predicted


@dataclass
class ScoreReading:
    """Ce qu'un collage portait comme scores en sets."""

    rows: list[ParsedScore] = field(default_factory=list)
    rejects: list[Reject] = field(default_factory=list)

    @property
    def predictions(self) -> int:
        """Les lignes qui portent vraiment un score. `PASSE` n'en est pas une."""
        return sum(1 for row in self.rows if not row.passe)


def read(raw: str) -> ScoreReading:
    """Les scores en sets d'un rendu, ligne par ligne.

    **Il n'existait aucun lecteur.** `save()` n'etait appele que par la route de
    saisie manuelle, une ligne a la fois : les cinq scores de la base ont ete
    tapes a la main, quand une seule session de huit matchs de tennis en produit
    huit. La section D en impose un par match a chaque session depuis toujours.

    **Limite a connaitre, et elle n'est pas dans ce module** : le gabarit demande
    ces scores en **prose** — « ecris le score, une phrase de justification, et
    rien d'autre » — et pas dans un tableau ni dans un bloc structure. Ce lecteur
    reconnait donc ce qu'une prose disciplinee porte quand meme : un repere de
    bloc ou une affiche, puis un score. Ce qu'il ne peut pas faire est garantir
    qu'il a tout lu, et c'est le compte-rendu d'import qui le dit.

    Une ligne qui porte un repere ou une affiche **et** un score en sets est
    retenue ; `PASSE` est retenu aussi, sans score — c'est une reponse du modele,
    et la compter est ce qui separe « huit fois PASSE » de « rien n'a ete colle ».
    """
    found = ScoreReading()
    seen: set[str] = set()
    for line in (raw or "").splitlines():
        parsed = _line(line)
        if parsed is None:
            continue
        cle = parsed.mark or _fold(parsed.match_text)
        if not cle or cle in seen:
            continue
        seen.add(cle)
        found.rows.append(parsed)
    return found


#: Les colonnes d'un tableau, quel que soit son format de copie — barres
#: verticales dans le Markdown ecrit, tabulations dans ce qu'on copie du rendu.
#: Meme regle que `picks_import._cells`, et pour la meme raison.
_CELLS = re.compile(r"^\s*\|(.+)\|\s*$")


def _cells(line: str) -> list[str]:
    match = _CELLS.match(line)
    if match is not None:
        return [cell.strip() for cell in match.group(1).split("|")]
    if line.count("\t") >= 1:
        return [cell.strip() for cell in line.split("\t")]
    return [line]


def _line(line: str) -> ParsedScore | None:
    """Une ligne de rendu, si elle porte un score en sets. Sinon rien.

    **Le repere prime sur l'affiche**, et l'affiche sur rien : un `M4` ne depend
    d'aucune orthographe, alors qu'une affiche recopiee peut differer d'un accent.
    Une ligne sans l'un ni l'autre est refusee plutot que rattachee au hasard —
    en cas de doute, rien, comme partout dans ce projet.
    """
    cells = _cells(line)
    text = " ".join(cells)
    folded = _fold(text)
    if not folded:
        return None
    # Une ligne de separation de tableau, ou l'entete lui-meme.
    if set(text.replace("|", "").replace(" ", "")) <= {"-", ":"}:
        return None
    if any(
        header in folded for header in ("second scenario", "score en sets")
    ) and not SCORE.search(text):
        return None

    mark = MARK.search(text)
    scores = SCORE.findall(text)
    passe = PASS.lower() in folded
    if not scores and not passe:
        return None
    label = _label(cells, mark.group(0) if mark else "")
    if not mark and not label:
        return None
    return ParsedScore(
        mark=mark.group(0).upper() if mark else "",
        match_text=label,
        predicted=f"{scores[0][0]}-{scores[0][1]}" if scores else "",
        alternate=f"{scores[1][0]}-{scores[1][1]}" if len(scores) > 1 else "",
    )


#: Une affiche : deux noms separes par un tiret, **des lettres des deux cotes**.
#: C'est ce qui separe « Learner Tien – Sebastian Baez » d'une phrase quelconque
#: qui porterait un score. Sans ce controle, « Le score le plus probable est
#: 2-1. » devenait une affiche et se serait rapprochee au hasard — l'erreur la
#: plus couteuse que ce module puisse produire.
_NOM = r"[^\W\d_][\w.'’\-]*(?:\s+[\w.'’\-]+)*"
AFFICHE = re.compile(rf"{_NOM}\s*[-–—]\s*{_NOM}")


def _label(cells: list[str], mark: str) -> str:
    """La cellule qui porte l'affiche, ou rien.

    On prend la **premiere** cellule qui ressemble a une affiche : deux noms de
    part et d'autre d'un tiret. Prendre la premiere cellule qui porte des lettres
    ferait entrer une justification a la place d'une affiche, et une ligne de
    prose entiere quand le tableau n'existe pas.
    """
    for cell in cells:
        propre = SCORE.sub(" ", cell.replace(mark, "")).strip(" |·")
        found = AFFICHE.search(propre)
        if found is not None:
            return found.group(0).strip()
    return ""


def rejected(kind: str, detail: str, payload: str = "") -> Reject:
    """Un score en sets qui n'a pas pu entrer, nomme pour ce qu'il est."""
    return Reject(
        block_type=SCORE_SETS, reason=kind or SCHEMA_INVALID, detail=detail, payload=payload
    )


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

    def _interval_label(self, won: int) -> str:
        """« [34 – 100] » — l'etendue que ces tirages-la ne permettent pas
        d'ecarter."""
        bounds = wilson(won, self.settled)
        return "" if bounds is None else f"[{bounds[0] * 100:.0f} – {bounds[1] * 100:.0f}]"

    def _flip_label(self, won: int) -> str:
        """« un résultat retourné le ramènerait à 50 % ».

        **La fragilite, dans la meme phrase que le chiffre.** « 100 % » sur deux
        constats se lit comme un fait ; l'effectif seul ne suffit pas a le
        corriger — c'est le nombre de bascules qui le dit. Meme regle que les
        lignes portees par les regroupements, et meme defaut corrige : ce bloc
        annoncait deux « 100 % » en gros caracteres, sur une page qui
        avertissait dix lignes plus haut de son propre manque de recul.
        """
        if not self.settled or not won:
            return ""
        return f"un résultat retourné le ramènerait à {(won - 1) / self.settled * 100:.0f} %"

    @property
    def exact_interval_label(self) -> str:
        return self._interval_label(self.exact)

    @property
    def exact_flip_label(self) -> str:
        return self._flip_label(self.exact)

    @property
    def issue_interval_label(self) -> str:
        return self._interval_label(self.exact + self.issue_only)

    @property
    def issue_flip_label(self) -> str:
        return self._flip_label(self.exact + self.issue_only)


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
