"""La repartition de mise : une table appliquee, jamais une esperance.

L'application reste un banc de mesure de predictions. Elle produit desormais
aussi une **repartition de mise deterministe**, calculee a partir d'une table de
configuration et de plafonds ecrits — jamais a partir d'une esperance de gain,
d'un edge, d'une probabilite implicite ou d'un critere de Kelly.

## Les trois proprietes non negociables, et comment chacune est tenue

**1. La mise ne depend jamais de la cote, du palier, ni du cran de confiance.**
Elle est tenue par la **signature** de `plan()` : la fonction ne recoit que la
bankroll, des reperes de bloc et une table. Aucun prix, aucun palier, aucun
cran ne peut l'atteindre, parce qu'ils ne lui sont pas passes. Une consigne se
contourne, un parametre absent non — et `tests/test_mises.py` verifie la
signature plutot que le comportement.

**2. Aucune progression.** Meme garde, et c'est pour elle qu'elle a ete choisie :
`plan()` ne recoit aucun resultat, aucun historique, aucune session anterieure.
La mise ne peut donc pas monter apres une perte ni descendre apres un gain, non
parce que le code s'en abstient mais parce qu'il n'en sait rien.

**3. Le plafond est un refus, pas un avertissement.** `Plan.factor` est applique
a l'ecriture, pas propose a la validation. Le lot 14 a mesure ce que vaut un
avertissement qu'on peut valider : **vingt fois sur vingt**, il a ete valide.

## La table, et d'ou viennent ses nombres

Mesure du 20/08/2026, sur les journees d'analyse de la base servie
(`picks.created_at`, section C seule, C-bis retiree) :

| Regime | n | P50 | P75 | P90 | max |
| --- | ---: | ---: | ---: | ---: | ---: |
| ancien (< 16/08) | 11 | 20,0 | 28,5 | 29,0 | 49 |
| nouveau (>= 16/08) | 4 | 18,0 | 19,5 | **20,4** | 21 |

L'unite se **mesure** au lieu de se choisir : elle vaut le plafond divise par le
90e centile des journees, soit `5 % / 20,4 = 0,245 %`, arrondi a **0,25 %**.

L'arrondi n'est pas cosmetique — il fait tomber le plafond sur un **compte
entier de selections** : au-dela de vingt selections de section C dans la
journee, la reduction s'applique. Un plafond exprime en pour-cent d'un pour-cent
ne se verifie pas de tete ; un compte, si.

**Le nombre est provisoire.** Quatre journees dans le regime actuel, quand la
defendabilite d'un 90e centile en demande une dizaine. A re-mesurer vers le
20/09/2026, apres un mois du nouveau regime.

## Ce que la table du brief proposait, et pourquoi elle ne pouvait pas etre codee

Unite a 1 % et plafond a 5 % : **seize sessions sur seize** atteignaient ou
depassaient le plafond, donc la mise reellement appliquee valait 0,167 % a
1,000 %, mediane 0,264 %. Le plafond ne plafonnait pas, il dimensionnait — et le
nombre ecrit dans la configuration differait de celui applique par le code d'un
facteur quatre en regime courant.

C'est le defaut caracteristique du projet, deplace sur l'argent : « 1 unite
= 1 % » se lit comme un fait pendant que le code en met un quart. Rien ne casse,
l'interface a l'air normale, et l'ecart ne se decouvre qu'en relisant le journal.

## C-bis ne recoit aucune mise

La section exploratoire est produite **sans fait date, sur lecture seule des
blocs**, et c'est la que vivent GIGA FUN et GIGA+. Sa raison d'etre est de
mesurer ce que vaut une lecture seule sur les cotes hautes.

Or la mesure fonctionne sans argent : ces selections sont enregistrees et
tranchees quoi qu'il arrive. Leur mettre une mise paierait une information qu'on
obtient sans payer — et miser sur une population produite sans preuve, a cotes
hautes, est exactement la combinaison qui vide une bankroll.

Le rapport 1 / 0,25 du brief etait une demi-mesure : assez pour couter, pas
assez pour changer quoi que ce soit a ce qu'on apprend. **Ce n'est donc pas un
reglage** — en faire un inviterait a le rouvrir, alors que l'arbitrage est de
principe et non de calibrage.
"""

from __future__ import annotations

import logging
import math
import re
from collections.abc import Sequence
from dataclasses import dataclass, field

from ..config import Settings, get_settings
from ..db import connect, utcnow
from .thresholds import value_of as threshold

logger = logging.getLogger(__name__)

#: Les unites que porte une selection de section C. **Le pivot de la table** :
#: tout le reste s'exprime en fraction de celle-ci.
UNITES_SELECTION = 1.0

#: Les unites que porte une selection de section C-bis. **Zero, et c'est une
#: decision de principe, pas un reglage** — voir le mode d'emploi du module.
UNITES_EXPLORATOIRE = 0.0

#: Deux decimales, comme une cote et comme un montant. Arrondir plus finement
#: rendrait des montants qu'aucun bookmaker n'accepte.
DECIMALES = 2


def _au_centime(valeur: float) -> float:
    """Un montant, arrondi **vers le bas** au centime.

    **L'arrondi au plus proche fait depasser le plafond, et c'est mesure** : 21
    selections sur une bankroll de 200 donnent 21 x 0,48 = 10,08 pour un plafond
    a 10,00, soit 5,04 % la ou la table en accorde 5,00. Un plan de mise qui
    franchit son propre refus est exactement le defaut qu'on evite ici — le
    plafond est un refus, pas une cible approchee.

    Le cout de l'arrondi par le bas est d'au plus un centime par ligne, et il ne
    se paie que lorsque la valeur porte plus de deux decimales : au facteur 1 et
    sur une unite ronde, il ne retire rien.
    """
    return math.floor(valeur * 100.0) / 100.0


@dataclass(frozen=True)
class Table:
    """La table de mises, resolue depuis les reglages.

    Les deux premiers champs sont en **centiemes de pour-cent de bankroll**
    (1 bp = 0,01 %), le troisieme en pour-cent d'une unite. Trois entiers, parce
    que le registre des seuils n'en porte pas d'autres et qu'inventer un type
    flottant pour trois valeurs couterait plus que la conversion.
    """

    unite_bp: int
    plafond_bp: int
    combine_pct: int

    @property
    def unite_pct(self) -> float:
        """L'unite en pour-cent de bankroll — `0.25`."""
        return self.unite_bp / 100.0

    @property
    def plafond_pct(self) -> float:
        """Le plafond de journee en pour-cent de bankroll — `5.0`."""
        return self.plafond_bp / 100.0

    @property
    def unites_combine(self) -> float:
        """Les unites que porte un combine — `0.5`."""
        return self.combine_pct / 100.0

    @property
    def plafond_unites(self) -> float:
        """Le plafond exprime en unites — **le nombre qui se verifie de tete**.

        Vingt, au reglage servi : au-dela de vingt selections de section C dans
        la journee, la reduction s'applique. Un plafond en pour-cent d'un
        pour-cent ne se verifie pas ; un compte de selections, si.
        """
        if self.unite_bp <= 0:
            return 0.0
        return self.plafond_bp / self.unite_bp


def table(settings: Settings | None = None) -> Table:
    """La table de mises reglee, ou ses defauts."""
    settings = settings or get_settings()
    return Table(
        unite_bp=threshold("mise_unite_bp", settings),
        plafond_bp=threshold("mise_plafond_bp", settings),
        combine_pct=threshold("mise_combine_pct", settings),
    )


#: Ce qu'une ligne de repartition dote. Le combine est distingue de la selection
#: parce qu'il ne se rattache pas au meme objet en base — `mises.combo_id` contre
#: `mises.pick_id` — et que le rendu les nomme differemment.
SELECTION = "selection"
COMBINE = "combine"


@dataclass(frozen=True)
class Line:
    """Une mise proposee, avant toute comparaison avec ce que le rendu a ecrit."""

    #: Le repere de bloc (`M3`) pour une selection, `combine_court` /
    #: `combine_long` pour un combine.
    mark: str
    kind: str
    #: Les unites **avant** reduction. Gardees a cote du montant : c'est sur
    #: elles que le plafond se raisonne, et un montant seul ne dit pas de
    #: combien il a ete rabote.
    unites: float
    montant: float


@dataclass(frozen=True)
class Plan:
    """La repartition d'une journee, plafond applique.

    `demandees` et `accordees` sont gardees toutes les deux, et c'est la garde
    demandee au §1a : une reduction doit etre **annoncee nommement** — combien
    d'unites demandees, combien accordees — jamais absorbee en silence.
    """

    bankroll: float
    table: Table
    lines: tuple[Line, ...] = ()
    demandees: float = 0.0
    accordees: float = 0.0

    @property
    def reduit(self) -> bool:
        """Le plafond a-t-il mordu ?"""
        return self.accordees < self.demandees - 1e-9

    @property
    def facteur(self) -> float:
        """Ce par quoi chaque mise a ete multipliee. `1.0` quand rien ne mord."""
        if self.demandees <= 0:
            return 1.0
        return self.accordees / self.demandees

    @property
    def total(self) -> float:
        """La somme reellement engagee."""
        return round(sum(line.montant for line in self.lines), DECIMALES)

    @property
    def sous_le_centime(self) -> tuple[Line, ...]:
        """Les lignes dont la mise tombe a zero une fois arrondie.

        **Une mise a 0,00 n'est pas une mise, et elle ne doit pas se lire comme
        une decision.** Le cas se produit sur une bankroll trop petite pour le
        nombre de selections du jour — a 1 EUR et vingt selections, l'unite vaut
        un quart de centime. Le nommer plutot que le rendre est la regle du
        projet : un echec ne doit pas avoir la meme sortie que le cas ordinaire.
        """
        return tuple(line for line in self.lines if line.montant <= 0.0)

    @property
    def part_bankroll(self) -> float:
        """Ce que la journee pese, en pour-cent de la bankroll."""
        if self.bankroll <= 0:
            return 0.0
        return 100.0 * self.total / self.bankroll

    def montant_de(self, mark: str) -> float | None:
        """Le montant propose pour un repere, ou `None` s'il n'en porte pas."""
        for line in self.lines:
            if line.mark == mark:
                return line.montant
        return None

    @property
    def reduction_line(self) -> str:
        """La reduction, dite nommement. Vide quand le plafond ne mord pas.

        **Rien quand tout va bien** : une ligne « reduction 0 % » a chaque
        session ferait chercher un rabot absent, et cesserait d'informer le jour
        ou il y en a vraiment un.
        """
        if not self.reduit:
            return ""
        return (
            f"plafond atteint — {_nombre(self.demandees)} unités demandées, "
            f"{_nombre(self.accordees)} accordées "
            f"(× {self.facteur:.3f})"
        )


def _pourcent(valeur: float) -> str:
    """Un pour-cent, a la francaise. `0,25` et non `0.25`.

    Le point decimal est reserve aux **cotes** dans ce projet, ou il vient du
    fournisseur ; un pour-cent dans une phrase francaise porte une virgule, et
    melanger les deux dans le meme prompt fait lire un prix la ou il y a une
    proportion.
    """
    return f"{valeur:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _nombre(valeur: float) -> str:
    """Un nombre d'unites, sans decimale inutile. `20` et non `20.0`."""
    if abs(valeur - round(valeur)) < 1e-9:
        return str(int(round(valeur)))
    return f"{valeur:.2f}".rstrip("0").rstrip(".")


def plan(
    bankroll: float,
    selections: Sequence[str],
    combines: Sequence[str] = (),
    table_: Table | None = None,
) -> Plan:
    """La repartition d'une journee.

    **La signature est le garde-fou, et c'est delibere.** Elle ne recoit ni cote,
    ni palier, ni cran de confiance, ni resultat anterieur : les quatre axes que
    le brief interdit ne peuvent pas atteindre ce calcul, parce qu'ils ne lui
    sont pas passes. Un test verifie la signature plutot que le comportement —
    une consigne se contourne, un parametre absent non.

    `selections` porte les reperes de **section C uniquement**. Les selections
    exploratoires n'y figurent pas : elles valent zero unite, et les faire
    entrer pour les multiplier par zero ferait apparaitre des lignes a 0,00 dans
    le rendu, ce qui se lit comme une mise oubliee plutot que comme une decision.
    """
    table_ = table_ or Table(unite_bp=25, plafond_bp=500, combine_pct=50)

    brutes: list[tuple[str, str, float]] = [
        (mark, SELECTION, UNITES_SELECTION) for mark in selections
    ]
    brutes += [(mark, COMBINE, table_.unites_combine) for mark in combines]

    demandees = sum(unites for _, _, unites in brutes)
    plafond = table_.plafond_unites
    accordees = min(demandees, plafond) if plafond > 0 else 0.0
    facteur = (accordees / demandees) if demandees > 0 else 1.0

    valeur_unite = bankroll * table_.unite_pct / 100.0
    lines = tuple(
        Line(
            mark=mark,
            kind=kind,
            unites=unites,
            montant=_au_centime(unites * facteur * valeur_unite),
        )
        for mark, kind, unites in brutes
    )
    return Plan(
        bankroll=bankroll,
        table=table_,
        lines=lines,
        demandees=demandees,
        accordees=accordees,
    )


# -- La ligne `mises:` --------------------------------------------------------
#
# **Une ligne a plat, jamais un bloc cloture**, et c'est la regle la plus chere
# du depot. Mesure du 17/08/2026 : `picks.claim_raw_json` etait NULL sur 235
# selections sur 235, parce que les blocs ```conf perdaient leur cloture au
# collage. `sets:` et `dossiers_ouverts:`, qui vivent hors de tout bloc de code,
# n'ont jamais pose de probleme. Ce format-ci reprend la meme forme, et entre au
# banc de transport comme les six autres.

#: La ligne entiere. Le corps s'arrete a la fin de ligne : une repartition ne
#: s'ecrit pas sur deux lignes, et en accepter une seconde ferait avaler la prose
#: qui suit.
MISES_LINE = re.compile(r"mises\s*:(?P<corps>[^\n]*)", re.IGNORECASE)

#: La bankroll declaree en tete de ligne.
BANKROLL_ENTRY = re.compile(r"bankroll\s*=\s*(?P<montant>[0-9]+(?:[.,][0-9]+)?)", re.IGNORECASE)

#: Une entree : `M3=0.50`, ou `combine_court=0.25`. La virgule decimale est
#: acceptee — un rendu francais l'ecrit, et refuser la ligne entiere pour un
#: separateur serait echouer pour la mauvaise raison.
MISES_ENTRY = re.compile(
    r"(?P<mark>M\d+|combin[eé]?_court|combin[eé]?_long)\s*=\s*(?P<montant>[0-9]+(?:[.,][0-9]+)?)",
    re.IGNORECASE,
)


def _montant(brut: str) -> float:
    return float(brut.replace(",", "."))


def _normalise(mark: str) -> str:
    """Un repere, sous sa forme canonique. `M3`, `combine_court`, `combine_long`.

    Le rendu peut ecrire `combine_court` ou `combiné_court` selon ce que le
    collage a fait des accents ; les deux designent la meme chose, et comparer
    la graphie brute ferait echouer la lecture pour une raison typographique.
    """
    plat = mark.strip().lower()
    if plat.startswith("m"):
        return "M" + plat[1:]
    return "combine_court" if "court" in plat else "combine_long"


@dataclass(frozen=True)
class ParsedStakes:
    """Ce qu'un collage portait comme repartition, avant tout recalcul."""

    bankroll: float | None = None
    #: Repere canonique -> montant declare.
    montants: dict[str, float] = field(default_factory=dict)
    #: L'endroit du collage brut d'ou la ligne vient.
    start: int | None = None
    end: int | None = None

    @property
    def present(self) -> bool:
        """La ligne etait-elle la ? **Distinct d'une ligne vide** : le modele qui
        omet la ligne et celui qui ecrit `mises:` sans rien produisent le meme
        dictionnaire vide, et ni la meme cause ni le meme correctif."""
        return self.start is not None


def read(raw: str) -> ParsedStakes:
    """La ligne `mises:` d'un collage.

    Rend un objet **absent** quand la ligne n'y est pas, et un objet present et
    vide quand elle y est sans entree. Les confondre reproduirait le defaut
    caracteristique du projet : une sortie identique pour l'echec et pour le cas
    ordinaire.
    """
    trouve = MISES_LINE.search(raw or "")
    if trouve is None:
        return ParsedStakes()
    corps = trouve.group("corps")
    bankroll = BANKROLL_ENTRY.search(corps)
    montants: dict[str, float] = {}
    for entree in MISES_ENTRY.finditer(corps):
        montants[_normalise(entree.group("mark"))] = _montant(entree.group("montant"))
    return ParsedStakes(
        bankroll=_montant(bankroll.group("montant")) if bankroll else None,
        montants=montants,
        start=trouve.start(),
        end=trouve.end(),
    )


@dataclass(frozen=True)
class Gap:
    """Un ecart entre ce que le rendu a ecrit et ce que la table accorde."""

    mark: str
    declare: float | None
    propose: float | None


def gaps(declared: ParsedStakes, computed: Plan) -> list[Gap]:
    """Les ecarts entre la ligne `mises:` et la repartition recalculee.

    **Ni l'un ni l'autre ne fait autorite sur le montant** : c'est le recalcul
    qui s'ecrit dans `mises.montant`, la declaration dans `mises.montant_declare`,
    et l'ecart est ce qui se lit — exactement comme la cote declaree d'un combine
    et le cran annonce d'une selection.
    """
    reperes = sorted({line.mark for line in computed.lines} | set(declared.montants))
    ecarts: list[Gap] = []
    for mark in reperes:
        propose = computed.montant_de(mark)
        declare = declared.montants.get(mark)
        if propose is None or declare is None or abs(propose - declare) >= 0.01:
            ecarts.append(Gap(mark=mark, declare=declare, propose=propose))
    return ecarts


# -- La bankroll d'une journee -----------------------------------------------


def set_bankroll(
    journee: str,
    montant: float,
    devise: str = "EUR",
    note: str | None = None,
    settings: Settings | None = None,
) -> None:
    """Le montant de depart d'une journee. **Saisi a la main, jamais deduit** :
    aucune source ne connait la bankroll, et la deduire d'un historique de mises
    supposerait que tout ce qui a ete propose a ete joue."""
    moment = utcnow()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO bankroll_journee (journee, montant, devise, note, created_at, updated_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(journee) DO UPDATE SET montant = excluded.montant, "
            "  devise = excluded.devise, note = excluded.note, updated_at = excluded.updated_at",
            (journee, float(montant), devise, note, moment, moment),
        )
    logger.info("Bankroll %s : %.2f %s", journee, montant, devise)


def bankroll_of(journee: str, settings: Settings | None = None) -> float | None:
    """Le montant d'une journee, ou `None` — jamais zero, qui se lirait comme
    une bankroll vide plutot que comme une absence de saisie."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT montant FROM bankroll_journee WHERE journee = ?", (journee,)
        ).fetchone()
    return float(row["montant"]) if row else None


# -- Ce que le prompt annonce ------------------------------------------------


@dataclass(frozen=True)
class Brief:
    """La table telle qu'elle descend dans le prompt.

    **En unites et jamais en monnaie**, et c'est la consequence directe du fait
    que le montant est saisi **au collage** : l'application ne connait pas la
    bankroll au moment ou elle rend le prompt. Elle annonce donc des unites, le
    modele convertit avec le montant qu'on a tape, et l'application recalcule a
    l'import en relisant `bankroll=` sur la ligne rendue.

    C'est aussi ce qui rend le plafond de **journee** tenable a travers plusieurs
    rendus : chaque prompt annonce ce qu'il **reste**, pas le plafond nu. Sans
    cela, quatre rendus dans la journee auraient chacun cru disposer du plafond
    entier — exactement le contournement par decoupage que le plafond par
    journee existe pour fermer.
    """

    table: Table
    #: Les unites deja engagees par les rendus precedents de la journee.
    engagees: float = 0.0

    @property
    def unite_pct(self) -> str:
        return _pourcent(self.table.unite_pct)

    @property
    def plafond_pct(self) -> str:
        return _pourcent(self.table.plafond_pct)

    @property
    def combine_unites(self) -> str:
        return _nombre(self.table.unites_combine)

    @property
    def plafond_unites(self) -> str:
        return _nombre(self.table.plafond_unites)

    @property
    def restantes(self) -> str:
        return _nombre(max(0.0, self.table.plafond_unites - self.engagees))


def engaged_units(journee: str, settings: Settings | None = None) -> float:
    """Les unites deja engagees dans la journee, tous rendus confondus.

    **C'est ce qui ferme le contournement par decoupage.** Un plafond par
    session se contournerait en generant quatre prompts ; le decoupage doit
    rester gratuit, parce que c'est une bonne pratique d'analyse — quatre
    prompts, quatre budgets de dossiers — et le coupler au garde-fou d'argent en
    ferait un multiplicateur d'exposition.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT COALESCE(SUM(unites), 0) AS total FROM mises WHERE journee = ?",
            (journee,),
        ).fetchone()
    return float(row["total"]) if row else 0.0


def brief(journee: str, settings: Settings | None = None) -> Brief:
    """La table de mises d'une journee, prete a descendre dans le prompt."""
    settings = settings or get_settings()
    return Brief(table=table(settings), engagees=engaged_units(journee, settings))


# -- Le journal --------------------------------------------------------------


@dataclass(frozen=True)
class Entry:
    """Une mise a enregistrer. `pick_id` et `combo_id` sont exclusifs."""

    unites: float
    montant: float | None = None
    montant_declare: float | None = None
    pick_id: int | None = None
    combo_id: int | None = None


def record(
    journee: str,
    session_id: int,
    entries: Sequence[Entry],
    settings: Settings | None = None,
) -> int:
    """Ecrit le journal des mises d'un import. Rend le nombre de lignes ecrites.

    **Pas de `@writes(...)`, et ce n'est pas un oubli.** Le registre des chemins
    d'ecriture garde les tables ou se pose une **prediction** — `picks`,
    `combos`, `combo_legs`, `set_scores` — parce que ce qui s'y perd est une
    mesure. Une mise perdue n'est pas une prediction perdue : elle se ressaisit,
    et l'y declarer melangerait les deux journaux que ce chantier separe.

    L'ecriture est **idempotente** sur la selection ou le combine vises : un
    second import du meme collage remplace la ligne au lieu d'en creer une
    seconde, ce qui ferait compter deux fois la meme mise dans le plafond de la
    journee.
    """
    moment = utcnow()
    ecrites = 0
    with connect(settings) as conn:
        for entry in entries:
            if (entry.pick_id is None) == (entry.combo_id is None):
                logger.warning("Mise ignoree : ni selection ni combine, ou les deux (%r)", entry)
                continue
            # **La clause `WHERE` de l'index partiel doit etre reprise ici** :
            # SQLite n'apparie un `ON CONFLICT` a un index partiel que si le
            # predicat est identique. Sans elle : « ON CONFLICT clause does not
            # match any PRIMARY KEY or UNIQUE constraint ».
            cible = "pick_id" if entry.pick_id is not None else "combo_id"
            conn.execute(
                "INSERT INTO mises (journee, session_id, pick_id, combo_id, unites, "
                "                   montant, montant_declare, created_at, updated_at) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
                f"ON CONFLICT({cible}) WHERE {cible} IS NOT NULL "
                "DO UPDATE SET unites = excluded.unites, "
                "  montant = excluded.montant, montant_declare = excluded.montant_declare, "
                "  updated_at = excluded.updated_at",
                (
                    journee,
                    session_id,
                    entry.pick_id,
                    entry.combo_id,
                    entry.unites,
                    entry.montant,
                    entry.montant_declare,
                    moment,
                    moment,
                ),
            )
            ecrites += 1
    if ecrites:
        logger.info("Journal des mises : %d ligne(s) pour la journee %s", ecrites, journee)
    return ecrites


def set_played(pick_id: int, montant: float | None, settings: Settings | None = None) -> None:
    """Ce qui a **reellement** ete pose chez le bookmaker, pour une selection.

    Se saisit a la main : le relever tout seul serait une integration
    transactionnelle avec un bookmaker, interdit n 7. `None` efface la saisie —
    se tromper doit pouvoir s'annuler, meme regle que le marquage d'un forfait.
    """
    with connect(settings) as conn:
        conn.execute(
            "UPDATE mises SET montant_joue = ?, updated_at = ? WHERE pick_id = ?",
            (montant, utcnow(), pick_id),
        )


@dataclass(frozen=True)
class JournalRow:
    """Une ligne du journal, telle qu'elle se lit.

    **La cote obtenue et le resultat viennent d'une jointure sur `picks`**, ils
    ne sont pas recopies ici : une valeur dupliquee diverge, et le projet l'a
    paye sur le niveau d'une competition, la famille d'un marche et le palier
    d'une cote. Le journal ne porte que l'argent.
    """

    journee: str
    label: str
    unites: float
    montant: float | None
    montant_declare: float | None
    montant_joue: float | None
    price_real: float | None
    result: str | None


def journal(journee: str | None = None, settings: Settings | None = None) -> list[JournalRow]:
    """Le journal d'une journee, ou de tout l'historique."""
    clause = "WHERE m.journee = ?" if journee else ""
    args = (journee,) if journee else ()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT m.journee, m.unites, m.montant, m.montant_declare, m.montant_joue, "
            "       p.price_real, p.result, "
            "       COALESCE(p.market || ' — ' || p.selection, 'combiné') AS label "
            "  FROM mises m "
            "  LEFT JOIN picks p ON p.id = m.pick_id "
            f" {clause} "
            " ORDER BY m.journee DESC, m.id",
            args,
        ).fetchall()
    return [
        JournalRow(
            journee=row["journee"],
            label=row["label"],
            unites=float(row["unites"]),
            montant=row["montant"],
            montant_declare=row["montant_declare"],
            montant_joue=row["montant_joue"],
            price_real=row["price_real"],
            result=row["result"],
        )
        for row in rows
    ]


@dataclass(frozen=True)
class SessionRow:
    """La mise d'une selection, telle que la feuille de session la rend.

    **Assemblee au bord et jamais dans `history.worksheet()`** : celui-la
    produit la mesure d'analyse, et un test lit sa source pour verifier qu'il ne
    connait aucun montant. La jointure se fait dans la couche qui assemble une
    page, jamais dans celle qui calcule un taux.
    """

    pick_id: int
    unites: float
    montant: float | None
    montant_declare: float | None
    montant_joue: float | None

    @property
    def ecart(self) -> float | None:
        """Ce que le rendu a ecrit, moins ce que la table accorde.

        `None` quand l'un des deux manque — un ecart calcule sur une absence
        serait une affirmation que la donnee ne porte pas.
        """
        if self.montant is None or self.montant_declare is None:
            return None
        return round(self.montant_declare - self.montant, 2)


def rows_for_session(session_id: int, settings: Settings | None = None) -> list[SessionRow]:
    """Les mises d'une session, rangees par selection."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT pick_id, unites, montant, montant_declare, montant_joue "
            "  FROM mises WHERE session_id = ? AND pick_id IS NOT NULL",
            (session_id,),
        ).fetchall()
    return [
        SessionRow(
            pick_id=int(row["pick_id"]),
            unites=float(row["unites"]),
            montant=row["montant"],
            montant_declare=row["montant_declare"],
            montant_joue=row["montant_joue"],
        )
        for row in rows
    ]
