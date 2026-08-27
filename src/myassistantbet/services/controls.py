"""Les controles du cadre, comptes a l'import.

**Ils sont calcules depuis toujours et opposes a rien.** Le cadre en enonce dix
« a passer systematiquement, dans l'ordre », et l'application sait deja repondre
a quatre d'entre eux : elle connait les evenements rapproches, les crans, les
niveaux de source, et depuis le lot A la condition d'invalidation. Elle ne l'a
jamais dit.

Mesure du 21/08/2026, avant d'ecrire une ligne — c'est elle qui a leve la
reserve « ne pas opposer un controle avant d'en connaitre le taux de base » :

- **controle 1** — 16 selections de section C portent un match deja pris ;
  **retreci le 27/08/2026**, voir plus bas ;
- **controle 8** — 36 portent une confiance 2, dont la place est en C-bis ;
- **controle 9** — 39 declarent un niveau 3, 4 ou `lecture`.

## Compter, jamais bloquer

**La remediation correcte pour les controles 8 et 9 est le renvoi en C-bis, et
il ne se decide pas a l'import.** Refuser la ligne la ferait disparaitre du lot
sans qu'aucune trace ne dise pourquoi — exactement le rejet silencieux que ce
projet retire partout. Le compte passe donc par la **confirmation explicite**,
comme la ligne `dossiers_ouverts` absente : rien n'est refuse, mais rien ne se
franchit sans avoir ete vu.

C'est la mesure d'A2 qui a decide de cette forme plutot que d'un simple
avertissement : l'avertissement de section manquante a parle **20 fois sur 20**
et les 20 imports ont ete valides quand meme. Un signal qui n'arrete rien ne se
distingue pas d'un signal absent.

## Trois etats, jamais deux

Un controle **muet** n'est pas un controle tenu. Une colonne absente de
l'en-tete et une cellule vide donnent la meme sortie si l'on ne compte que les
violations : le controle 7 dirait « aucune condition d'invalidation » sur un
collage a huit colonnes, c'est-a-dire une violation la ou la question n'a pas
ete posee. Meme vocabulaire que la ligne `Absents` et ses trois etats.

## Le controle 1 compte ce que le gabarit interdit, pas une regle levee

**Il mesurait un ecart au SKILL que le gabarit a leve.** « Une seule selection
par evenement, sans exception » etait la regle du cadre ; le gabarit en autorise
deux « si elles reposent sur des angles reellement independants — et tu le dis
alors explicitement ». Compter toute seconde ligne revenait donc a compter des
lignes conformes, et un compteur qui remonte le comportement voulu cesse d'etre
lu.

Mesure du 27/08/2026, faite avant de retrecir : **13 matchs sur 574 portent deux
selections, 9 en section C** — la permission est utilisee dans 2,3 % des cas, et
elle n'a pas de file d'attente. Ce qui manque n'est pas de la place : **la note
d'independance est renseignee sur 5 lignes sur 18**.

Ce qu'il compte desormais : **les deux lignes portent la meme famille de
marche**. La note d'independance peut alors etre presente et **elle ne peut pas
etre vraie** — deux lignes de la meme famille ne sont pas deux angles
independants, et c'est le seul cas que le collage prouve a lui seul.

**La note manquante n'y entre pas, et ce n'est pas un oubli.** Elle n'existe
nulle part ou ce compte se lit : elle se saisit a l'apercu (`independence_{n}`)
et le rapport se calcule sur le **collage conserve**, qui ne porte aucune colonne
d'independance — la condition ne peut pas voyager par le formulaire qu'elle
garde, meme regle que les sections. Elle est de toute facon deja **bloquante** :
`add_pick` refuse la ligne sans elle et l'apercu la decoche avec son motif. La
compter ici redirait ce qu'un refus dit deja, sur une valeur qu'on ne peut pas
lire.

La famille plutot que la cle fine, parce que c'est elle qui groupe : `O/U 2.5` et
`O/U 3.5` sur le meme match sont le meme angle a une ligne pres, quand `1N2` et
`Handicap` sont deux familles et se defendent. Verifie sur les 9 paires reelles :
huit portent deux familles distinctes, et la neuvieme est **la meme selection
ecrite deux fois** — `Vainqueur` sur Peyton Stearns, notee `c4` puis `c3`. C'est
elle que le controle doit attraper, et c'est la seule.

## Les recouvrements se rendent

`16 + 36 + 39` ne fait pas 91 si les ensembles se croisent, et une ligne qui
viole deux controles ne se repare pas comme deux lignes qui en violent un. Le
compte des lignes **distinctes** ferme l'addition, et les paires sont nommees.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from itertools import combinations
from typing import TYPE_CHECKING, Any

from ..config import Settings, get_settings
from ..db import connect

if TYPE_CHECKING:  # pragma: no cover - references de type seulement
    from .picks_import import ImportPreview, ParsedPick

#: Un controle tenu, viole, ou hors de portee faute de colonne.
HELD = "tenu"
VIOLATED = "viole"
SILENT = "muet"

#: Les niveaux de source qui **dirigent** une ligne du tableau principal. Le
#: cadre : « un angle porte par une source n3 ou n4 descend en C-bis, quelle que
#: soit la conviction ». `lecture` n'en fait evidemment pas partie.
DIRECTING_LEVELS = frozenset({"1", "2"})


@dataclass(frozen=True)
class Control:
    """Un controle du cadre, et ce qui le rend verifiable ici.

    `column` nomme le champ de l'en-tete **sans lequel la question n'est pas
    posee**. Vide, le controle se lit sur autre chose que le tableau — le
    controle 1 se lit sur le rapprochement des matchs, qui ne depend d'aucune
    colonne facultative.
    """

    key: str
    number: int
    label: str
    #: Vrai quand la ligne **viole** le controle.
    breaks: Callable[[ParsedPick], bool]
    #: La colonne sans laquelle le controle est muet.
    column: str = ""
    #: Le controle ne porte que sur le tableau principal.
    main_table_only: bool = False

    def verdict(self, pick: ParsedPick, columns: frozenset[str]) -> str:
        if self.main_table_only and pick.exploratory:
            return HELD
        if self.column and self.column not in columns:
            return SILENT
        return VIOLATED if self.breaks(pick) else HELD


#: Les quatre controles que l'application sait verifier. Les six autres du cadre
#: ne se decident pas ici : la ligne en quart et la cote inventee se lisent sur
#: le bloc du match, l'anteriorite a deja sa propre garde et son propre compte,
#: le H2H seul et « chaque match apparait quelque part » demandent le rendu
#: entier. Les nommer ici sans pouvoir les compter donnerait l'apparence d'une
#: couverture complete.
CONTROLS: tuple[Control, ...] = (
    Control(
        key="c1",
        number=1,
        label="deux sélections d'une même famille sur un match",
        breaks=lambda pick: pick.same_event and pick.same_family,
    ),
    Control(
        key="c7",
        number=7,
        label="condition d'invalidation présente",
        breaks=lambda pick: not pick.invalidation,
        column="invalidation",
    ),
    Control(
        key="c8",
        number=8,
        label="aucune conf 2 dans le tableau principal",
        breaks=lambda pick: pick.confidence == "2",
        column="confidence",
        main_table_only=True,
    ),
    Control(
        key="c9",
        number=9,
        label="dirigée par un fait de niveau 1 ou 2",
        breaks=lambda pick: pick.source not in DIRECTING_LEVELS,
        column="source",
        main_table_only=True,
    ),
)

_BY_KEY = {control.key: control for control in CONTROLS}


@dataclass
class ControlReport:
    """Ce que le collage porte comme ecarts au cadre.

    **Un compte, jamais un taux** : il est juste a tout effectif, et aucun seuil
    ne le garde. Meme regle que le compte des non classees.
    """

    lines: int = 0
    #: Violations par controle, `cle -> compte`.
    violations: dict[str, int] = field(default_factory=dict)
    #: Controles hors de portee, faute de colonne dans l'en-tete.
    silent: dict[str, int] = field(default_factory=dict)
    #: Lignes **distinctes** portant au moins une violation. C'est ce nombre qui
    #: ferme l'addition, jamais la somme des controles.
    flagged: int = 0
    #: Les paires qui se recouvrent, `(cle_a, cle_b, compte)`, de la plus
    #: fournie a la moins. Une paire disjointe n'y figure pas.
    overlaps: list[tuple[str, str, int]] = field(default_factory=list)

    @property
    def blocking(self) -> bool:
        """Y a-t-il quelque chose a voir avant de franchir ?

        **Les muets n'en font pas partie.** Une colonne absente n'est pas un
        ecart au cadre, c'est une question qu'on n'a pas pu poser : la faire
        confirmer ferait cocher une case a chaque collage a huit colonnes, et le
        garde-fou deviendrait le decor qu'il existe pour ne pas etre.
        """
        return self.flagged > 0

    @property
    def note(self) -> str:
        """« 12 ligne(s) sur 19 en écart : contrôle 1 ×2 · contrôle 9 ×11 ». Vide sans rien."""
        if not self.violations and not self.silent:
            return ""
        morceaux = [
            f"contrôle {_BY_KEY[cle].number} ×{compte}"
            for cle, compte in sorted(
                self.violations.items(), key=lambda item: (-item[1], _BY_KEY[item[0]].number)
            )
        ]
        detail = " · ".join(morceaux)
        tete = f"{self.flagged} ligne(s) sur {self.lines} en écart au cadre" if morceaux else ""
        recouvrements = (
            " · dont "
            + ", ".join(
                f"{compte} sur les contrôles {_BY_KEY[a].number} et {_BY_KEY[b].number}"
                for a, b, compte in self.overlaps
            )
            if self.overlaps
            else ""
        )
        muets = (
            " · "
            + ", ".join(
                f"contrôle {_BY_KEY[cle].number} non vérifiable ({compte} ligne(s), "
                "colonne absente)"
                for cle, compte in sorted(self.silent.items())
            )
            if self.silent
            else ""
        )
        return (f"{tete} : {detail}" if morceaux else "").strip() + recouvrements + muets

    def label_for(self, key: str) -> str:
        return f"contrôle {_BY_KEY[key].number} — {_BY_KEY[key].label}"


def read(picks: list[ParsedPick], columns: frozenset[str]) -> ControlReport:
    """Les ecarts au cadre d'une liste de lignes deja lues.

    **Aucun second lecteur.** Les lignes viennent de `picks_import`, le seul
    module qui sache decouper le tableau : une expression reguliere posee a cote
    finirait par ne plus designer les memes lignes, et deux comptes du meme
    collage se contrediraient sans qu'aucun ne soit faux.
    """
    report = ControlReport(lines=len(picks))
    par_ligne: list[set[str]] = []
    for pick in picks:
        casses = set()
        for control in CONTROLS:
            verdict = control.verdict(pick, columns)
            if verdict == VIOLATED:
                casses.add(control.key)
                report.violations[control.key] = report.violations.get(control.key, 0) + 1
            elif verdict == SILENT:
                report.silent[control.key] = report.silent.get(control.key, 0) + 1
        par_ligne.append(casses)
    report.flagged = sum(1 for casses in par_ligne if casses)
    croisements: dict[tuple[str, str], int] = {}
    for casses in par_ligne:
        for paire in combinations(sorted(casses, key=lambda cle: _BY_KEY[cle].number), 2):
            croisements[paire] = croisements.get(paire, 0) + 1
    report.overlaps = [
        (a, b, compte)
        for (a, b), compte in sorted(
            croisements.items(), key=lambda item: (-item[1], _BY_KEY[item[0][0]].number)
        )
    ]
    return report


def for_preview(preview: ImportPreview) -> ControlReport:
    """Le compte d'un apercu en cours."""
    return read(preview.picks, preview.columns)


def for_import(
    session_id: int, import_id: str | int | None, settings: Settings | None = None
) -> ControlReport | None:
    """Le compte d'un collage **deja conserve**, relu par son identifiant.

    **La condition ne peut pas voyager par le formulaire qu'elle garde** : un
    champ cache portant le compte serait fourni par la page que le compte
    retient. `imports_raw` fait foi, exactement comme pour les sections.

    Rend `None` quand il n'y a rien a relire — identifiant absent, illisible, ou
    collage d'une autre session. **On ne retient pas sur ce qu'on n'a pas vu** :
    la saisie a la main et le rejeu n'ont pas d'identifiant d'import.
    """
    settings = settings or get_settings()
    try:
        numero = int(str(import_id or "").strip())
    except ValueError:
        return None
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT raw_text FROM imports_raw WHERE id = ? AND session_id = ?",
            (numero, session_id),
        ).fetchone()
    if row is None:
        return None
    # Import differe : `picks_import` importe ce module pour poser le compte sur
    # l'apercu, et le poser en tete ferait un cycle.
    from . import picks_import

    relu = picks_import.build_preview(session_id, str(row["raw_text"] or ""), settings)
    return for_preview(relu)


#: Ce que le refus dit, la ou il est prononce. Il nomme d'abord ce qui repare —
#: le renvoi en C-bis pour les controles 8 et 9 — parce que ce geste-la ne se
#: fait pas a l'import et qu'un refus qui ne propose que de passer outre
#: installerait l'habitude qu'il devait rompre.
BLOCKED_NOTE: Any = (
    "Import retenu : des lignes s'écartent des contrôles du cadre. Les corriger "
    "demande de reprendre le rendu — une conf 2 et un angle porté par une source "
    "n3 ou n4 descendent en C-bis, ce qui ne se décide pas ici. Coche la case pour "
    "importer tel quel."
)
