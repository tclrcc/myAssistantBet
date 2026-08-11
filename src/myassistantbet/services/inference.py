"""Ce qu'un echantillon permet d'affirmer, et ce qu'il ne permet pas.

**Couche pure : aucune base, aucun reglage, aucun import de service.** Elle ne
connait ni les selections ni les paliers — seulement des couples (reussites,
tranchees). C'est ce qui la rend testable contre des valeurs de reference
publiees, et c'est la seule facon de savoir qu'elle est juste : ces fonctions
decident desormais de ce que la page affirme, et une erreur y serait invisible.

Rien de ce qui en sort ne ressemble a une prevision. Un intervalle dit ce que
**ces tirages-la** permettent d'affirmer, un test dit si un ecart deja constate
tient, et un calcul de puissance dit ce qu'il faudrait accumuler. Aucun ne parle
du prochain pari, aucun ne rencontre une cote. C'est de la statistique
descriptive sur des resultats passes, au sens de la section 9 de SPEC.md.

**Aucune dependance nouvelle.** `scipy` fournirait tout ceci en une ligne, mais
l'ajouter pour six fonctions ferait entrer dans un projet qui tient sur un
processus et un fichier SQLite une bibliotheque de calcul scientifique. Les
implementations directes tiennent en trente lignes chacune et sont verifiees
contre des valeurs connues.

## Trois lecons mesurees, et elles sont l'essentiel de ce module

**1. L'intervalle de Wilson ne remplace pas un test.** Sur la population reelle,
« l'intervalle ecarte 50 % » retenait 6 lignes quand le test exact n'en retenait
que 3. Les trois desaccords vont tous dans le meme sens — l'intervalle affirme
plus que les donnees ne portent : deux lignes a `0/4` dont l'intervalle monte a
48,99 % quand quatre pertes d'affilee arrivent une fois sur huit a pile ou face
(p = 0,125), et une ligne dont la borne basse vaut **50,011 %**, soit un
centieme de point au-dessus du seuil. Une ligne qui bascule sur la troisieme
decimale n'est pas un fait. L'intervalle reste rendu — c'est la precision, et
elle se lit d'un coup d'oeil sur une barre — mais c'est le **test** qui decide.

**2. La reference n'est pas 50 %, c'est le complement.** Un taux de reussite de
50 % n'est un repere pour rien : sur un 1N2 la base tourne autour de 33 %, sur
un handicap asiatique autour de 50 %, sur un total tout depend de la ligne.
Comparer chaque tranche a pile ou face teste une hypothese que personne n'a
formulee. La question actionnable est « cette tranche differe-t-elle de ce que
je fais **par ailleurs** », donc un 2x2 contre le reste de la meme population.
Le changement retourne le verdict la ou il compte : `22/34` passe de p = 0,12 a
p = 0,0013, `18/26` de p = 0,076 a p = 0,0023 — la reference d'origine declarait
non prouve ce que les donnees etablissent.

**3. L'axe se teste avant la ligne.** Un axe est une **partition** : « conf 3
contre le reste » et « conf 4 contre le reste » sont le meme test ecrit deux
fois, et les compter comme deux essais gonfle artificiellement la multiplicite.
Un omnibus par axe, puis decomposition en lignes seulement s'il passe. Mesure de
ce que la regle ecarte : « 1re division — Europe » seule donne p = 0,028, mais
son axe vaut p = 0,083 — elle ne passe pas, et c'est juste.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil, comb, exp, lgamma, log, sqrt

#: z d'un intervalle de confiance a 95 %.
WILSON_Z = 1.96

#: z d'un test bilateral a 5 %, et z de la puissance visee (80 %). Les deux
#: valeurs habituelles : un test qui laisse passer une difference reelle une
#: fois sur cinq est deja peu exigeant, et viser mieux ferait exploser la cible.
TEST_Z_ALPHA = 1.96
TEST_Z_BETA = 0.84

#: Seuil de decision, partout dans ce module.
ALPHA = 0.05

#: Marge de comparaison des densites dans les tests exacts. Deux tirages de
#: probabilite egale doivent tomber du meme cote du seuil ; sans cette tolerance,
#: l'arrondi binaire en exclut un et le test devient asymetrique sur des tables
#: parfaitement symetriques.
_EPSILON = 1e-9


# -- Intervalle -------------------------------------------------------------


def wilson(won: int, settled: int, continuity: bool = False) -> tuple[float, float] | None:
    """Intervalle de Wilson a 95 % sur une proportion observee.

    Choisi plutot que l'intervalle normal, qui donne des bornes hors de [0, 1]
    et une largeur nulle a `x = 0` — soit exactement les deux cas ou la page a
    le plus besoin d'etre juste : `0/4` sur un palier, et les regroupements de
    quelques lignes.

    `continuity` applique la correction de continuite, qui elargit l'intervalle
    pour tenir compte du fait qu'une proportion sur `n` tirages ne prend que
    `n + 1` valeurs. Elle est **hors par defaut** : l'intervalle sert ici a
    montrer une precision, pas a decider — c'est le test exact qui decide — et
    un intervalle deja large n'a pas besoin de l'etre davantage pour se lire.

    None quand rien n'est tranche : il n'y a alors aucune proportion, et rendre
    `(0, 1)` ferait lire une mesure la ou il n'y a pas d'observation.
    """
    if settled <= 0:
        return None
    z_squared = WILSON_Z * WILSON_Z
    observed = won / settled
    denominator = settled + z_squared
    centre = (won + z_squared / 2) / denominator
    spread = settled * observed * (1 - observed) + z_squared / 4
    if continuity:
        # La correction retire un demi-tirage de chaque cote. Les deux bornes se
        # calculent separement : appliquee symetriquement, elle deplacerait le
        # centre au lieu d'elargir.
        low = _wilson_bound(won, settled, -1)
        high = _wilson_bound(won, settled, +1)
        return (max(0.0, low), min(1.0, high))
    half = (WILSON_Z / denominator) * sqrt(spread)
    # Les bornes se rabattent sur [0, 1] : un taux ne sort pas de la, et une
    # borne a -0.03 se lirait comme une grandeur signee.
    return (max(0.0, centre - half), min(1.0, centre + half))


def _wilson_bound(won: int, settled: int, direction: int) -> float:
    """Une borne de Wilson corrigee en continuite, du cote demande.

    Forme canonique de Newcombe. Les deux bornes ne se deduisent pas l'une de
    l'autre par symetrie — la correction ne deplace pas le centre, elle ecarte
    chaque borne — d'ou les deux signes portes par `direction`.
    """
    z, n = WILSON_Z, settled
    observed = won / n
    complement = 1 - observed
    # Une proportion extreme garde sa borne triviale : elle est exacte, et la
    # racine y deviendrait imaginaire.
    if direction < 0 and won == 0:
        return 0.0
    if direction > 0 and won == settled:
        return 1.0
    # Le terme sous la racine porte `+ direction` a l'exterieur et `- direction`
    # a l'interieur : les deux signes sont opposes, et les confondre donne un
    # intervalle asymetrique sur une proportion de 0,5 — ce qui est le symptome.
    inner = z * z + direction * 2 - 1 / n + 4 * observed * (n * complement - direction)
    root = sqrt(max(0.0, inner))
    return (2 * n * observed + z * z + direction * (1 + z * root)) / (2 * (n + z * z))


# -- Tests exacts -----------------------------------------------------------


def binomial_test(won: int, settled: int, reference: float = 0.5) -> float:
    """Test binomial exact bilateral d'une proportion contre une reference fixe.

    Bilateral par la **methode de la densite** : on additionne la probabilite de
    tous les tirages au moins aussi improbables que celui observe. C'est la
    definition qui vaut aussi quand la reference n'est pas 0,5, ou la loi n'est
    plus symetrique et ou doubler la queue donnerait un nombre faux.

    Sert au cas ou une reference **exterieure** existe. Ce n'est pas le cas
    ordinaire de la page — voir `two_proportions`, qui compare une tranche au
    reste de la population et repond a la seule question actionnable.
    """
    if settled <= 0:
        return 1.0
    if not 0.0 < reference < 1.0:
        return 1.0

    def density(k: int) -> float:
        return comb(settled, k) * reference**k * (1 - reference) ** (settled - k)

    observed = density(won)
    return min(
        1.0, sum(d for k in range(settled + 1) if (d := density(k)) <= observed * (1 + _EPSILON))
    )


def two_proportions(
    won: int, settled: int, other_won: int, other_settled: int, directed: bool = False
) -> float:
    """Test exact de Fisher entre une tranche et une autre. Aucune approximation.

    **C'est le test de la page**, parce que c'est sa question : cette tranche
    differe-t-elle du reste de mes selections ? Fisher plutot qu'un chi2 sur une
    table 2x2, parce que les effectifs sont petits — `0/4` contre `30/63` — et
    qu'une approximation normale y est fausse dans le sens qui trompe.

    `directed` rend le test **unilateral**, et ne se pose que sur une hypothese
    dont la direction a ete declaree **avant** les donnees : le gabarit de prompt
    affirme qu'une confiance 4 doit battre une confiance 3. Partout ailleurs le
    test est bilateral — choisir la direction apres avoir vu le resultat
    reviendrait a diviser son seuil par deux en silence.
    """
    a, b, c, d = won, settled - won, other_won, other_settled - other_won
    if min(a, b, c, d) < 0 or (a + b) == 0 or (c + d) == 0:
        return 1.0
    first, second, drawn = a + b, c + d, a + c
    low, high = max(0, drawn - second), min(first, drawn)
    if low >= high:
        # Une marge est degeneree : toutes les tables possibles sont la table
        # observee, et il n'y a rien a tester.
        return 1.0

    def density(k: int) -> float:
        return comb(first, k) * comb(second, drawn - k) / comb(first + second, drawn)

    if directed:
        return min(1.0, sum(density(k) for k in range(a, high + 1)))
    observed = density(a)
    return min(
        1.0, sum(d for k in range(low, high + 1) if (d := density(k)) <= observed * (1 + _EPSILON))
    )


# -- Omnibus ----------------------------------------------------------------


def _upper_gamma(shape: float, x: float) -> float:
    """Fonction gamma incomplete superieure regularisee, Q(s, x).

    Serie pour les petits `x`, fraction continue de Lentz au-dela : c'est la
    decomposition classique, chacune des deux convergeant mal la ou l'autre
    convient. Elle ne sert qu'a rendre une p-valeur de chi2 sans `scipy`.
    """
    if x <= 0:
        return 1.0
    if x < shape + 1:
        term = total = 1.0 / shape
        for index in range(1, 500):
            term *= x / (shape + index)
            total += term
            if abs(term) < abs(total) * 1e-15:
                break
        return max(0.0, 1.0 - total * exp(-x + shape * log(x) - lgamma(shape)))
    tiny = 1e-300
    fraction, coefficient, divisor = tiny, tiny, 0.0
    for index in range(1, 500):
        numerator = 1.0 if index == 1 else -(index - 1) * ((index - 1) - shape)
        denominator = (x + 1 - shape) if index == 1 else (x + 2 * index - 1 - shape)
        divisor = denominator + numerator * divisor or tiny
        coefficient = denominator + numerator / coefficient or tiny
        divisor = 1.0 / divisor
        step = coefficient * divisor
        fraction *= step
        if abs(step - 1) < 1e-15:
            break
    return min(1.0, fraction * exp(-x + shape * log(x) - lgamma(shape)))


@dataclass(frozen=True)
class Omnibus:
    """L'axe entier separe-t-il les resultats, avant qu'on regarde ses lignes.

    **Un axe est une partition**, donc « conf 3 contre le reste » et « conf 4
    contre le reste » sont le meme test ecrit deux fois. Les compter comme deux
    essais gonfle la multiplicite et fait ressortir des lignes par construction.
    Un seul test par axe, puis decomposition s'il passe.
    """

    chi_squared: float
    degrees: int
    p_value: float

    @property
    def separates(self) -> bool:
        return self.p_value < ALPHA


def omnibus(cells: list[tuple[int, int]]) -> Omnibus | None:
    """Test d'homogeneite d'un axe : ses lignes ont-elles le meme taux.

    `cells` est une liste de `(reussites, tranchees)`, une par ligne de l'axe.

    None quand la question ne se pose pas : moins de deux lignes peuplees, ou
    un axe dont **tout** est gagne ou **tout** est perdu — les taux y sont alors
    egaux par construction, et un test dirait « homogene » sans rien mesurer.
    """
    peupled = [(won, settled) for won, settled in cells if settled > 0]
    if len(peupled) < 2:
        return None
    total_won = sum(won for won, _ in peupled)
    total = sum(settled for _, settled in peupled)
    if total_won in (0, total):
        return None
    statistic = 0.0
    for won, settled in peupled:
        for observed, expected in (
            (won, settled * total_won / total),
            (settled - won, settled * (total - total_won) / total),
        ):
            if expected:
                statistic += (observed - expected) ** 2 / expected
    degrees = len(peupled) - 1
    return Omnibus(statistic, degrees, _upper_gamma(degrees / 2, statistic / 2))


def cramers_v(table: list[list[int]]) -> float | None:
    """Association entre deux partitions, de 0 (aucune) a 1 (identiques).

    Repond a une question que le Jaccard par ligne repond mal : deux **axes**
    mesurent-ils la meme chose ? Mesure sur la population reelle — le palier et
    la confiance annoncee donnent V = 0,54, avec 51 selections sur 67 sur la
    diagonale. Leurs deux resultats n'en font qu'un, et la page doit le dire
    plutot que de les presenter comme deux constats independants.

    Le Jaccard, lui, garde son emploi : il compare deux **lignes** de deux axes,
    la ou V compare les axes entiers.
    """
    rows = [row for row in table if sum(row)]
    if len(rows) < 2:
        return None
    columns = [index for index in range(len(rows[0])) if sum(row[index] for row in rows)]
    if len(columns) < 2:
        return None
    # Aucune garde sur un total nul : `rows` ne garde que les lignes de somme
    # non nulle, et des effectifs ne sont jamais negatifs. Une branche qu'aucun
    # appel ne peut atteindre ne se teste pas, donc ne se verifie jamais.
    total = sum(sum(row) for row in rows)
    statistic = 0.0
    for row in rows:
        for index in columns:
            expected = sum(row) * sum(other[index] for other in rows) / total
            if expected:
                statistic += (row[index] - expected) ** 2 / expected
    smallest = min(len(rows), len(columns)) - 1
    return sqrt(statistic / (total * smallest)) if smallest else None


def jaccard(left: set[int], right: set[int]) -> float:
    """Part commune a deux ensembles, sur leur union.

    Zero sur deux ensembles vides plutot qu'une division par zero : deux
    regroupements sans aucune selection ne se recouvrent pas, ils n'existent pas.
    """
    union = left | right
    return len(left & right) / len(union) if union else 0.0


# -- Puissance --------------------------------------------------------------


def required_sample(first: float, second: float) -> int | None:
    """Selections **par groupe** pour qu'un ecart observe devienne testable.

    Repond a la question que la page pose sans jamais y repondre : « SAFE fait
    mieux que FUN » est-il un constat ou du bruit ? Le nombre dit ce qu'il
    faudrait accumuler pour trancher, ce qui est plus utile que de trancher trop
    tot.

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


def required_for_gap(reference: float, gap: float) -> int | None:
    """Selections par groupe pour detecter un ecart de `gap` a `reference`.

    Variante de la precedente pour une cible que l'on **se fixe** plutot que
    pour un ecart deja observe : « combien faudrait-il pour voir dix points
    d'ecart ». Elle sert au panneau de cout de la granularite, ou la question
    est posee avant d'avoir les donnees.
    """
    other = reference + gap
    if not 0.0 <= other <= 1.0 or gap == 0:
        return None
    return required_sample(other, reference)


# -- Ce qu'une ligne porte --------------------------------------------------


@dataclass(frozen=True)
class Evidence:
    """Ce qu'une ligne agregee permet d'affirmer, et contre quoi.

    Construit par `evidence()`, jamais a la main : les six champs se deduisent
    des quatre comptes, et les laisser saisir separement les ferait diverger.
    """

    won: int
    settled: int
    #: Le reste de la population — l'axe moins cette ligne. C'est **lui** la
    #: reference, et non 50 % : un taux de reussite de 50 % n'est un repere pour
    #: rien, la base variant avec le marche joue.
    other_won: int
    other_settled: int
    p_value: float
    #: Le test etait-il unilateral, c'est-a-dire l'hypothese dirigee et posee
    #: d'avance. Rendu pour que la page ne presente pas au meme titre un
    #: resultat confirmatoire et une trouvaille.
    directed: bool = False

    @property
    def rate(self) -> float | None:
        return None if self.settled == 0 else self.won / self.settled

    @property
    def reference(self) -> float | None:
        """Taux du complement, celui contre lequel la ligne se lit."""
        return None if self.other_settled == 0 else self.other_won / self.other_settled

    @property
    def interval(self) -> tuple[float, float] | None:
        return wilson(self.won, self.settled)

    @property
    def discriminant(self) -> bool:
        """La ligne s'ecarte de son complement plus que le hasard ne l'explique.

        **Le test, jamais l'intervalle** : sur la population reelle, « l'IC
        ecarte la reference » retenait deux lignes a `0/4` (p = 0,12) et une
        ligne dont la borne franchissait le seuil de 0,011 point.
        """
        return self.settled > 0 and self.other_settled > 0 and self.p_value < ALPHA

    @property
    def required(self) -> int | None:
        """Selections par ligne pour que l'ecart observe devienne testable."""
        rate, reference = self.rate, self.reference
        if rate is None or reference is None:
            return None
        return required_sample(rate, reference)


def evidence(
    won: int, settled: int, other_won: int, other_settled: int, directed: bool = False
) -> Evidence:
    """Assemble le constat d'une ligne contre son complement."""
    return Evidence(
        won=won,
        settled=settled,
        other_won=other_won,
        other_settled=other_settled,
        p_value=two_proportions(won, settled, other_won, other_settled, directed=directed),
        directed=directed,
    )


def benjamini_hochberg(p_values: list[float], alpha: float = ALPHA) -> int:
    """Nombre de tests retenus par la procedure de Benjamini-Hochberg.

    Sert a **dire** la multiplicite, jamais a filtrer : appliquee aux lignes
    d'une page qui en porte trente, elle les retirerait toutes et reinstallerait
    le defaut qu'on corrige — masquer la seule ligne qui affirme quelque chose.
    Le compte se lit a cote du nombre de lignes repliees.

    Elle ne s'applique qu'a l'**exploratoire**. Les hypotheses posees d'avance
    par le gabarit forment un lot separe de deux tests, ou c'est Bonferroni qui
    tranche — melanger les deux ferait passer pour du bruit le seul resultat que
    cette base ait etabli.
    """
    if not p_values:
        return 0
    ranked = sorted(p_values)
    return max(
        (rank for rank, value in enumerate(ranked, 1) if value <= rank / len(ranked) * alpha),
        default=0,
    )
