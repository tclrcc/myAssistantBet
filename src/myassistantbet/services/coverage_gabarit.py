"""Quelles regles du gabarit ne se declenchent jamais.

**Une regle qui ne se declenche jamais est un cout fixe pur**, paye sur toutes
les sessions. Le gabarit decrit longuement des cas — les trois etats
d'« Absents », les trois mentions d'« Entraineur », les trois etats de « Lieu »,
les trois notes de « Non servis » — et rien ne disait lesquels arrivent
vraiment.

## Ce que ce module ne fait pas, et c'est la consigne

**Il ne supprime rien.** C'est un constat pour arbitrage, et certains de ces cas
sont rares **et** decisifs : un terrain neutre change tout un scenario, et le
projet a deja mesure que trois delocalisations d'une meme semaine relevaient de
la seule categorie qui compte. Un cas a 1 % qui retourne une lecture ne se
compare pas a un cas a 1 % qui n'apprend rien.

## Le compte porte sur les **blocs**, jamais sur le texte entier

Un prompt cite chaque cas une fois dans son mode d'emploi : compter les
occurrences brutes rendrait « GIGA FUN » a 718 sur une base ou aucune selection
n'en porte. Le corps se decoupe donc a ses en-tetes de bloc, et le preambule
sort du denominateur.

**La source est le corps archive**, relu tel qu'il est parti — pas un rendu
recalcule aujourd'hui, qui ne dirait plus ce que les sessions ont reellement vu.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from ..config import Settings, get_settings
from ..db import connect

#: L'en-tete qui ouvre un bloc. Meme forme que partout — `history._BLOCK_HEADER`
#: et `prompt._FIRST_BLOCK` — et ce n'est pas une recopie de trop : celle-ci
#: **decoupe** quand les deux autres reperent, et un `split` a besoin du motif
#: entier. Un test compare les trois sur un meme corps.
_BLOC = re.compile(r"^### M\d+ ", re.MULTILINE)


@dataclass(frozen=True)
class Case:
    """Un cas que le gabarit decrit, et comment le reconnaitre dans un bloc.

    `marker` est cherche **dans le bloc**, pas dans le prompt : le preambule
    nomme chaque cas au moins une fois, et l'y compter rendrait tout present.
    """

    key: str
    label: str
    marker: str
    #: Ce que ce cas apporte quand il arrive. **Il ne se deduit pas du compte**,
    #: et c'est tout l'objet : un cas rare qui retourne une lecture ne se
    #: supprime pas comme un cas rare qui n'apprend rien.
    weight: str


#: Les cas releves. Non exhaustif, et le brief le dit — c'est une liste ouverte,
#: pas un inventaire complet du gabarit.
CASES: tuple[Case, ...] = (
    # **Les marqueurs sont ceux du rendu, jamais ceux du mode d'emploi**, et le
    # premier jet s'y est trompe : le bloc ecrit « aucun signale » sans accent —
    # regle du module, « ni apostrophe ni accent dans une valeur rendue » — quand
    # le chapitre ecrit « aucun absent signalé ». Le compte sortait a **zero** sur
    # un cas qui arrive 153 fois. Un compte faux qui a l'air plausible est
    # exactement ce que ce releve existe pour ne pas produire.
    Case("absents_vus", "Absents — aucun signalé", "aucun signale", "faible"),
    Case("absents_non_interroges", "Absents — non interrogés", "non interrog", "fort"),
    Case("absents_injoignable", "Absents — source injoignable", "injoignable", "moyen"),
    Case("coach_divergence", "Entraîneur — divergence", "divergence", "fort"),
    Case(
        "coach_initiale",
        "Entraîneur — apparié sur l'initiale",
        "apparié sur l'initiale",
        "moyen",
    ),
    Case("lieu_neutre", "Lieu — TERRAIN NEUTRE", "TERRAIN NEUTRE", "décisif"),
    Case(
        "lieu_non_verifiable",
        "Lieu — terrain neutre non vérifiable",
        "pas d'identifiant de stade",
        "moyen",
    ),
    Case("statut", "Statut — reporté, annulé, forfait", "Statut  ", "décisif"),
    Case("non_servis", "Non servis", "Non servis ", "moyen"),
    Case("tour_non_renseigne", "Tour — phase non renseignée", "phase non renseigne", "moyen"),
    Case("a_relever", "A relever", "A relever ", "fort"),
    Case("compos", "Compos — onze publié", "Compos  ", "fort"),
    Case("effectif", "Effectif — absents reconstruits", "Effectif  ", "fort"),
    Case("alerte_handicap", "Alerte — handicap suspect", "Alerte  ", "décisif"),
    Case("meteo_alerte", "Météo — alerte officielle", "ALERTE", "décisif"),
)


@dataclass(frozen=True)
class Hit:
    """Ce qu'un cas a rencontre, sur toute la base."""

    case: Case
    blocks: int
    prompts: int
    sessions: int
    total_blocks: int

    @property
    def share(self) -> float:
        return 0.0 if not self.total_blocks else self.blocks / self.total_blocks

    @property
    def never(self) -> bool:
        return self.blocks == 0


#: Ce qui **ferme** la section des blocs. Les sections de sortie et le chapitre
#: « COMMENT LIRE LES BLOCS » viennent apres, et le second nomme chaque cas au
#: moins une fois.
#:
#: **Trois cas sortaient a exactement 29 blocs sur 29 prompts** avant cette
#: borne — la signature d'un marqueur capte une fois par prompt, donc hors bloc.
#: Le decoupage sans borne haute versait tout le chapitre dans le **dernier**
#: bloc, et « TERRAIN NEUTRE », « aucun absent » et « injoignable » y sont
#: definis. Un compte faux qui a l'air plausible est exactement ce que ce
#: chantier existe pour ne pas produire.
_FIN_DES_BLOCS = re.compile(r"^## (CE QUE L'HISTORIQUE DIT|SORTIE ATTENDUE)", re.MULTILINE)


def blocks_of(body: str) -> list[str]:
    """Les blocs d'un prompt, **preambule et sections de sortie exclus**.

    Le premier morceau du decoupage est tout ce qui precede le premier en-tete —
    le mode d'emploi, qui nomme chaque cas au moins une fois. Et tout ce qui suit
    la section MATCHS est coupe, pour la meme raison : sans cette borne, le
    chapitre « COMMENT LIRE LES BLOCS » tombait dans le dernier bloc.
    """
    texte = body or ""
    fin = _FIN_DES_BLOCS.search(texte)
    if fin is not None:
        texte = texte[: fin.start()]
    morceaux = _BLOC.split(texte)
    return morceaux[1:] if len(morceaux) > 1 else []


def survey(settings: Settings | None = None) -> tuple[Hit, ...]:
    """Combien de blocs declenchent chacun des cas decrits par le gabarit.

    Lu sur les corps **archives** : c'est ce que les sessions ont reellement vu,
    et un rendu recalcule aujourd'hui dirait autre chose.
    """
    settings = settings or get_settings()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT id, session_id, body FROM prompts WHERE body IS NOT NULL ORDER BY id"
        ).fetchall()

    tally: dict[str, dict[str, set[int] | int]] = {
        case.key: {"blocks": 0, "prompts": set(), "sessions": set()} for case in CASES
    }
    total = 0
    for row in rows:
        blocs = blocks_of(str(row["body"]))
        total += len(blocs)
        for case in CASES:
            touches = sum(1 for bloc in blocs if case.marker in bloc)
            if not touches:
                continue
            compte = tally[case.key]
            compte["blocks"] = int(compte["blocks"]) + touches
            compte["prompts"].add(int(row["id"]))  # type: ignore[union-attr]
            compte["sessions"].add(int(row["session_id"]))  # type: ignore[union-attr]

    return tuple(
        Hit(
            case=case,
            blocks=int(tally[case.key]["blocks"]),
            prompts=len(tally[case.key]["prompts"]),  # type: ignore[arg-type]
            sessions=len(tally[case.key]["sessions"]),  # type: ignore[arg-type]
            total_blocks=total,
        )
        for case in CASES
    )


def never_seen(settings: Settings | None = None) -> tuple[Hit, ...]:
    """Les cas que **aucun bloc archive** n'a jamais declenches.

    C'est le tableau que le brief demande, et il s'arrete la : **ne rien
    supprimer sur cette foi**. Un cas rare peut etre decisif — un terrain neutre
    retourne un scenario entier — et son `weight` le dit a cote du compte.
    """
    return tuple(hit for hit in survey(settings) if hit.never)
