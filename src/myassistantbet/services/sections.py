"""Ce que le prompt a demande, et ce que le collage a rapporte.

**Le trou qui restait apres le lot 2.** La table des rejets attrape ce qui
**leve** ; le collage brut garde ce qui **arrive**. Ni l'un ni l'autre ne dit
qu'une section entiere n'est jamais revenue : une section absente n'est pas un
bloc casse, c'est un bloc qui n'a pas ete colle, et rien ne la reclamait nulle
part.

C'est l'etat exact dans lequel les blocs `conf` sont restes quatre jours, et
celui de la population exploratoire aujourd'hui : un zero qu'on ne sait pas
lire — normal, ou muet.

## Rien n'est stocke, et c'est le coeur du module

Les deux moities dorment deja en base :

- **ce qui etait attendu** se lit dans `prompts.body`, le prompt reellement
  emis. Un lot dont aucun palier haut n'etait atteignable n'a jamais porte de
  section C-bis, et lui reprocher de n'en pas rapporter serait faux ;
- **ce qui est revenu** se lit dans `imports_raw.raw_text`, conserve depuis la
  migration 052.

Une colonne de plus aurait fige un constat que le code courant sait refaire, et
elle aurait menti au premier lecteur corrige — le meme arbitrage que la famille
d'un marche, le niveau d'une competition et le palier calcule a la lecture.

## Les lecteurs ne sont jamais reecrits ici

Chaque section se reconnait dans le collage par **le lecteur qui l'importe**,
jamais par une seconde expression reguliere posee a cote. Deux lectures
paralleles de la meme chose finissent par ne plus designer la meme chose — le
piege deja paye deux fois par l'assembleur de contexte, une fois par le
rapprochement des reperes de `sets:`.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect
from . import picks_import, set_scores
from .combos import read_combos
from .confidence import OPEN_ABSENT, read_blocks, read_opened

#: Ce que le gabarit ecrit quand il demande une section. **Ces motifs se posent
#: sur le prompt emis et non sur le gabarit** : une porte fermee (`{% if
#: tier_scope.high %}`) fait qu'un lot n'a jamais demande sa section C-bis, et
#: c'est cette difference-la qu'on veut lire.
_ASKS_C_BIS = re.compile(r"###\s*c-?bis", re.IGNORECASE)
_ASKS_SETS = re.compile(r"^\s*sets\s*:", re.IGNORECASE | re.MULTILINE)
_ASKS_OPENED = re.compile(r"dossiers_ouverts", re.IGNORECASE)
_ASKS_CONF = re.compile(r"```conf", re.IGNORECASE)
_ASKS_COMBO = re.compile(r"```combo", re.IGNORECASE)


@dataclass(frozen=True)
class Section:
    """Une section attendue du rendu, et comment la reconnaitre des deux cotes.

    `asks` se lit sur le prompt **emis**, `finds` sur le **collage recu**. Les
    deux sont des fonctions et non des motifs, parce que le second cote passe
    par les lecteurs d'import : ce sont eux qui font foi, et une expression
    reguliere ecrite ici pour aller plus vite finirait par diverger d'eux.
    """

    key: str
    label: str
    asks: Callable[[str], bool]
    finds: Callable[[str], bool]


def _finds_c_bis(raw: str) -> bool:
    """La section C-bis se reconnait a son titre, comme a l'import.

    `parse_table` bascule sur ce meme motif : sans lui, aucune ligne du second
    tableau n'est marquee exploratoire, donc la section est absente **en
    pratique** meme si le mot y figure.
    """
    return any(
        picks_import.EXPLORATORY_HEAD.search(picks_import._fold(line))
        for line in (raw or "").splitlines()
    )


def _finds_sets(raw: str) -> bool:
    """La ligne `sets:` — la ligne structuree, pas le filet de prose.

    Le filet reconnait des scores dans des phrases ; il ne prouve pas que la
    ligne demandee a ete collee, et c'est la ligne qui se reclame ici.
    """
    return set_scores.SETS_LINE.search(raw or "") is not None


def _finds_opened(raw: str) -> bool:
    """Trois des quatre etats valent « trouvee ».

    Une ligne **illisible** ou **vide** a bien ete collee : la premiere est un
    defaut de lecteur, la seconde une declaration du modele, et aucune des deux
    n'est un collage qui a laisse la ligne derriere lui. Seule `absente` l'est,
    et c'est elle que ce module reclame — les autres se lisent deja au releve
    d'apercu, qui nomme les quatre.
    """
    return read_opened(raw).state != OPEN_ABSENT


def _finds_conf(raw: str) -> bool:
    return bool(read_blocks(raw).claims)


def _finds_combo(raw: str) -> bool:
    return bool(read_combos(raw).combos)


#: Les cinq sections structurees que le rendu porte. L'ordre est celui du
#: gabarit — on relit un compte-rendu dans l'ordre ou on a colle.
SECTIONS: tuple[Section, ...] = (
    Section("c_bis", "section C-bis", lambda raw: bool(_ASKS_C_BIS.search(raw)), _finds_c_bis),
    Section("conf", "blocs conf", lambda raw: bool(_ASKS_CONF.search(raw)), _finds_conf),
    Section("combo", "blocs combo", lambda raw: bool(_ASKS_COMBO.search(raw)), _finds_combo),
    Section("sets", "ligne sets:", lambda raw: bool(_ASKS_SETS.search(raw)), _finds_sets),
    Section(
        "dossiers_ouverts",
        "ligne dossiers_ouverts",
        lambda raw: bool(_ASKS_OPENED.search(raw)),
        _finds_opened,
    ),
)


@dataclass(frozen=True)
class SessionSections:
    """Le releve d'une session : demande, trouve, et **ni l'un ni l'autre**.

    Le troisieme etat est celui qui manquait. Un zero exploratoire peut vouloir
    dire « le gabarit ne l'a jamais demande », « il l'a demande et le collage
    l'a laisse derriere lui », ou « la section est venue et le modele n'a rien
    produit ». Un compte unique les confond, et c'est la forme la plus couteuse
    qu'un defaut prenne ici — il se lit comme une mesure.
    """

    session_id: int
    #: Vrai quand la session a un collage conserve. Sans collage, rien ne se
    #: conclut : `imports_raw` date de la migration 052, et les sessions
    #: anterieures n'ont pas laisse de texte a relire.
    has_paste: bool = False
    asked: frozenset[str] = frozenset()
    found: frozenset[str] = frozenset()

    @property
    def missing(self) -> tuple[str, ...]:
        """Demandees et jamais revenues. C'est la seule liste actionnable."""
        return tuple(s.key for s in SECTIONS if s.key in self.asked and s.key not in self.found)

    @property
    def missing_labels(self) -> tuple[str, ...]:
        labels = {s.key: s.label for s in SECTIONS}
        return tuple(labels[key] for key in self.missing)

    @property
    def note(self) -> str:
        """La meme chose sans le numero de session, pour le releve d'apercu.

        **C'est la ou elle sert le plus** : au moment du collage, une section
        laissee derriere se reprend en dix secondes. Dite une semaine plus tard
        sur la page, elle ne se repare plus — meme raison que le releve
        d'apercu, et meme endroit.
        """
        if not self.missing:
            return ""
        return "demandée(s) par le prompt et absente(s) du collage : " + ", ".join(
            self.missing_labels
        )

    @property
    def line(self) -> str:
        """« session 14 · 2 attendue(s) et non trouvée(s) : section C-bis, ligne sets: »"""
        if not self.has_paste:
            return f"session {self.session_id} · aucun collage conservé — rien à conclure"
        if not self.asked:
            return f"session {self.session_id} · aucune section structurée demandée par ce lot"
        if not self.missing:
            return (
                f"session {self.session_id} · {len(self.asked)} section(s) demandée(s), "
                "toutes retrouvées dans le collage"
            )
        return (
            f"session {self.session_id} · {len(self.missing)} attendue(s) et non trouvée(s) : "
            + ", ".join(self.missing_labels)
        )


@dataclass
class Survey:
    """Le releve de toutes les sessions qui portent un collage."""

    rows: list[SessionSections] = field(default_factory=list)

    @property
    def missing_total(self) -> int:
        return sum(len(row.missing) for row in self.rows)

    @property
    def concerned(self) -> list[SessionSections]:
        """Les seules sessions qui aient quelque chose a dire.

        **Un compte-rendu qui liste tout ne se lit pas.** Meme regle que le
        bandeau des competitions non rattachees : ce qui manque se nomme, ce qui
        va bien se compte.
        """
        return [row for row in self.rows if row.missing]

    @property
    def empty(self) -> bool:
        return not self.rows


def read(raw: str, prompt: str) -> tuple[frozenset[str], frozenset[str]]:
    """Les sections demandees par le prompt, et celles retrouvees dans le collage.

    Rendue a part de la lecture en base pour que le rapprochement se teste sans
    session ni migration : c'est la fonction qui porte la regle.
    """
    asked = {section.key for section in SECTIONS if section.asks(prompt or "")}
    found = {section.key for section in SECTIONS if section.finds(raw or "")}
    return frozenset(asked), frozenset(found)


def for_paste(session_id: int, raw: str, settings: Settings | None = None) -> SessionSections:
    """Le releve d'un collage **en cours**, contre le dernier prompt de la session.

    **Le dernier prompt fait foi**, comme pour `prompt_odds` : une session en
    genere jusqu'a vingt, et c'est le plus recent dont l'etat est le plus proche
    de ce qui vient d'etre colle. Sans prompt archive, rien n'est demande donc
    rien ne manque — se taire vaut mieux qu'accuser un collage d'apres un
    gabarit qu'on n'a pas.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT body FROM prompts WHERE session_id = ? ORDER BY id DESC LIMIT 1",
            (session_id,),
        ).fetchone()
    asked, found = read(raw, str(row["body"]) if row else "")
    return SessionSections(session_id=session_id, has_paste=True, asked=asked, found=found)


def survey(settings: Settings | None = None) -> Survey:
    """Ce que chaque session a demande et n'a pas recu.

    **Le prompt retenu est le dernier de la session**, comme `prompt_odds`
    retient le dernier releve : c'est celui dont l'etat est le plus proche de la
    decision, et une session en genere jusqu'a vingt. Les collages sont fondus
    ensemble — une session peut etre collee en plusieurs fois, et exiger que
    chaque morceau porte tout ferait crier au manque sur un import en deux
    temps.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        collages: dict[int, list[str]] = {}
        for row in conn.execute(
            "SELECT session_id, raw_text FROM imports_raw ORDER BY session_id, id"
        ):
            collages.setdefault(int(row["session_id"]), []).append(str(row["raw_text"] or ""))
        prompts = {
            int(row["session_id"]): str(row["body"] or "")
            for row in conn.execute("SELECT session_id, body FROM prompts ORDER BY session_id, id")
        }
    rows: list[SessionSections] = []
    for session_id in sorted(collages):
        asked, found = read("\n".join(collages[session_id]), prompts.get(session_id, ""))
        rows.append(
            SessionSections(session_id=session_id, has_paste=True, asked=asked, found=found)
        )
    return Survey(rows=rows)
