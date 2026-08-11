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
Un omnibus par axe, puis decomposition en lignes seulement s'il passe.

**4. Et cet omnibus doit etre exact, ce qui a ete appris en se trompant.** La
premiere version employait un chi2 d'homogeneite, dont l'hypothese — des
effectifs attendus au-dessus de 5 — est fausse sur une page qui porte quinze
lignes sous huit paris. Sur l'axe « niveau de competition », le chi2 donne
p = 0,083 : l'axe ne passe pas, et « 1re division — Europe » est demotee comme
un exemple de la regle qui fonctionne. Le test exact donne **p = 0,044** :
l'axe passe, la ligne tient. Un faux negatif silencieux, produit par le repli
asymptotique exactement la ou il ne vaut rien. Le chi2 ne sert plus que
au-dela du budget d'enumeration, et `Omnibus.exact` le dit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
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

#: Marge de book de reference, pour montrer ou un constat de residu cesse de
#: tenir. **Ce n'est pas une estimation du vrai overround** — on ne peut pas le
#: connaitre sans le marche complet — c'est un point de comparaison choisi dans
#: la fourchette ordinaire (3 a 8 % sur les marches concernes), et pris **haut**
#: dans cette fourchette pour que le chiffre affiche soit le moins favorable.
MARGIN_REFERENCE = 0.05

#: Marge de comparaison des densites dans les tests exacts. Deux tirages de
#: probabilite egale doivent tomber du meme cote du seuil ; sans cette tolerance,
#: l'arrondi binaire en exclut un et le test devient asymetrique sur des tables
#: parfaitement symetriques.
_EPSILON = 1e-9


# -- Intervalle -------------------------------------------------------------


def wilson(
    won: int, settled: int, continuity: bool = False, z: float = WILSON_Z
) -> tuple[float, float] | None:
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
    z_squared = z * z
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
    half = (z / denominator) * sqrt(spread)
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


#: Nombre de tables au-dela duquel l'enumeration exacte cede la place au chi2.
#:
#: Mesure sur les axes reels : confiance 27 tables, palier 144, famille 1 078,
#: niveau 3 113 — tous sous la milliseconde. Le marche en compte **777 437**, et
#: son enumeration coute 632 ms, ce qu'une page ne peut pas payer a chaque
#: affichage. Le nombre de tables se compte d'abord, par une recurrence qui ne
#: les construit pas.
EXACT_BUDGET = 200_000


@dataclass(frozen=True)
class Omnibus:
    """L'axe entier separe-t-il les resultats, avant qu'on regarde ses lignes.

    **Un axe est une partition**, donc « conf 3 contre le reste » et « conf 4
    contre le reste » sont le meme test ecrit deux fois. Les compter comme deux
    essais gonfle la multiplicite et fait ressortir des lignes par construction.
    Un seul test par axe, puis decomposition s'il passe.
    """

    p_value: float
    #: Le test etait-il exact. Faux quand la table depassait le budget
    #: d'enumeration et qu'un chi2 a servi de repli. **Rendu, jamais tu** : un
    #: verdict exact et une approximation ne se lisent pas au meme titre, et
    #: c'est precisement leur confusion qui a produit un faux negatif.
    exact: bool
    #: Statistique du chi2 et ses degres de liberte, sur le seul repli. `None`
    #: sur un test exact, qui n'en produit aucune.
    chi_squared: float | None = None
    degrees: int | None = None

    @property
    def separates(self) -> bool:
        return self.p_value < ALPHA


def _table_count(sizes: list[int], target: int) -> int:
    """Combien de tables partagent ces marges. Recurrence, sans les construire.

    Sert a decider si l'enumeration exacte est payable **avant** de la lancer :
    la compter coute quelques microsecondes, la tenter coute une seconde.
    """
    reachable = {0: 1}
    for size in sizes:
        following: dict[int, int] = {}
        for used, ways in reachable.items():
            for taken in range(min(size, target - used) + 1):
                following[used + taken] = following.get(used + taken, 0) + ways
        reachable = following
    return reachable.get(target, 0)


def _freeman_halton(rows: list[tuple[int, int]]) -> float:
    """Test exact de Fisher-Freeman-Halton sur une table r x 2.

    Enumeration complete des tables de memes marges, et somme de celles dont la
    probabilite ne depasse pas celle observee. Le produit est **porte le long de
    la recurrence** plutot que recalcule a chaque feuille : la difference vaut
    un facteur trois sur les grandes tables.
    """
    sizes = [settled for _, settled in rows]
    target = sum(won for won, _ in rows)
    binomials = [[comb(size, taken) for taken in range(size + 1)] for size in sizes]
    observed = 1.0
    for (won, _), table in zip(rows, binomials, strict=True):
        observed *= table[won]
    remaining = [sum(sizes[index + 1 :]) for index in range(len(sizes))]
    last = len(sizes) - 1
    kept = mass = 0.0

    def walk(index: int, left: int, product: float) -> None:
        nonlocal kept, mass
        if index == last:
            if 0 <= left <= sizes[index]:
                weight = product * binomials[index][left]
                mass += weight
                if weight <= observed * (1 + _EPSILON):
                    kept += weight
            return
        low = max(0, left - remaining[index])
        for taken in range(low, min(sizes[index], left) + 1):
            walk(index + 1, left - taken, product * binomials[index][taken])

    walk(0, target, 1.0)
    return kept / mass if mass else 1.0


def omnibus(cells: list[tuple[int, int]]) -> Omnibus | None:
    """Test d'homogeneite d'un axe : ses lignes ont-elles le meme taux.

    `cells` est une liste de `(reussites, tranchees)`, une par ligne de l'axe.

    **Exact par defaut, et ce n'est pas un raffinement.** Le chi2 suppose des
    effectifs attendus au-dessus de 5 ; un axe de la page en porte quinze sous
    huit paris, si bien que l'hypothese est fausse la ou le test compte. Mesure
    du degat sur l'axe « niveau de competition » : chi2 p = 0,083 — l'axe ne
    passe pas et sa ligne « 1re division — Europe » est demotee — quand le test
    exact donne **p = 0,044**, donc l'inverse. Un faux negatif silencieux, sur
    l'axe le plus fourni apres les deux etiquetages.

    Le chi2 ne sert plus que de repli au-dela du budget d'enumeration, et
    `Omnibus.exact` le dit.

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

    sizes = [settled for _, settled in peupled]
    if _table_count(sizes, total_won) <= EXACT_BUDGET:
        return Omnibus(p_value=_freeman_halton(peupled), exact=True)

    statistic = 0.0
    for won, settled in peupled:
        for observed, expected in (
            (won, settled * total_won / total),
            (settled - won, settled * (total - total_won) / total),
        ):
            if expected:
                statistic += (observed - expected) ** 2 / expected
    degrees = len(peupled) - 1
    return Omnibus(
        p_value=_upper_gamma(degrees / 2, statistic / 2),
        exact=False,
        chi_squared=statistic,
        degrees=degrees,
    )


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


# -- Equivalence : conclure qu'il n'y a rien d'assez gros ------------------

#: z d'un intervalle a 90 %, celui d'un test d'equivalence a 5 %.
#:
#: **Et non 1,96.** Une equivalence se conclut par deux tests unilateraux, donc
#: par l'intervalle a `1 - 2α` : prendre celui a 95 % testerait a 2,5 % de chaque
#: cote et rendrait la conclusion plus difficile qu'elle ne doit l'etre.
TOST_Z = 1.645

#: Ecart en dessous duquel deux etiquetages ne meritent pas d'exister tous les
#: deux, en fraction de taux.
#:
#: **C'est une decision produit, prise avant de regarder les donnees, et elle ne
#: bouge pas avec l'echantillon.** C'est tout ce qui la separe de ce qu'elle
#: remplace : un plafond de sessions restantes transformait une propriete de
#: l'agenda de saisie en verdict statistique — il a d'ailleurs bascule d'un
#: « rien a mesurer » a un « atteignable » sur les memes donnees lues a travers
#: deux populations.
#:
#: Sous dix points, deux echelles a saisir, deux jeux de libelles a tenir et le
#: poids de prompt associe ne se justifient pas.
EQUIVALENCE_MARGIN = 0.10


@dataclass(frozen=True)
class Equivalence:
    """Deux etiquetages disent-ils assez peu de choses differentes pour n'en
    garder qu'un.

    **Un test classique ne conclut jamais « il n'y a rien »** : il echoue a
    rejeter, ce qui n'est pas la meme chose. Une equivalence, elle, se conclut
    par l'affirmative — l'ecart residuel tient tout entier dans une marge dont
    on a decide d'avance qu'elle ne vaut pas un second axe.
    """

    #: `(reussites, tranchees)` des deux groupes compares, **a l'interieur d'une
    #: strate de l'autre axe** : c'est l'ecart residuel qui decide, jamais
    #: l'ecart brut, qui recopierait ce que l'axe dit deja tout seul.
    first: tuple[int, int]
    second: tuple[int, int]
    margin: float = EQUIVALENCE_MARGIN

    @property
    def gap(self) -> float | None:
        if not self.first[1] or not self.second[1]:
            return None
        return self.first[0] / self.first[1] - self.second[0] / self.second[1]

    @property
    def interval(self) -> tuple[float, float] | None:
        return difference_interval(*self.first, *self.second, z=TOST_Z)

    @property
    def established(self) -> bool:
        """L'ecart tient **entierement** dans la marge : un seul axe suffit."""
        bounds = self.interval
        return bounds is not None and max(abs(bounds[0]), abs(bounds[1])) < self.margin

    @property
    def interval_label(self) -> str:
        """« [-37 ; +13] pts », en points de taux."""
        bounds = self.interval
        if bounds is None:
            return ""
        return f"[{bounds[0] * 100:+.0f} ; {bounds[1] * 100:+.0f}] pts"


def difference_interval(
    won: int, settled: int, other_won: int, other_settled: int, z: float = WILSON_Z
) -> tuple[float, float] | None:
    """Intervalle de la **difference** de deux proportions, methode de Newcombe.

    Bati sur les intervalles de Wilson de chaque proportion, donc de la meme
    famille que ce que la page affiche deja. L'approximation normale sur la
    difference serait fausse la ou elle compte : sur `6/14` contre `8/26`, elle
    donne des bornes qui sortent de [-1, 1].
    """
    # Une seule garde, et c'est celle de `wilson` : elle rend deja `None` sur un
    # effectif nul. Un controle d'entree en plus rendrait ce repli inatteignable,
    # donc non verifiable — la meme branche morte que deux fois plus haut.
    first = wilson(won, settled, z=z)
    second = wilson(other_won, other_settled, z=z)
    if first is None or second is None:
        return None
    rate, other_rate = won / settled, other_won / other_settled
    gap = rate - other_rate
    low = gap - sqrt((rate - first[0]) ** 2 + (second[1] - other_rate) ** 2)
    high = gap + sqrt((first[1] - rate) ** 2 + (other_rate - second[0]) ** 2)
    return (max(-1.0, low), min(1.0, high))


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


# -- Le residu au prix ------------------------------------------------------


def poisson_binomial(probabilities: list[float]) -> list[float]:
    """Loi exacte du nombre de succes de tirages **de probabilites differentes**.

    Convolution, terme a terme. Aucune approximation normale : les probabilites
    vont de 0,38 a 0,80 sur les selections reelles, et l'approximation y decale
    la p-valeur de plusieurs points — assez pour faire franchir un seuil.
    """
    distribution = [1.0]
    for probability in probabilities:
        following = [0.0] * (len(distribution) + 1)
        for count, mass in enumerate(distribution):
            following[count] += mass * (1 - probability)
            following[count + 1] += mass * probability
        distribution = following
    return distribution


@dataclass(frozen=True)
class Residual:
    """Les victoires observees comparees a ce que les prix annoncaient.

    **La seule grandeur de la page qui soit interpretable en elle-meme.** Un
    taux de reussite ne distingue pas une methode qui bat le marche d'une
    methode qui prend des favoris courts — c'est exactement ainsi qu'un coin de
    tableau a 82 % a failli devenir un resultat, alors que ses prix en
    annoncaient deja 69 %.

    **N'ouvre aucun interdit de la section 9.** Aucun devig, aucun marche
    complet, aucune projection, aucune mise : des issues **tranchees** comparees
    a des prix **deja enregistres**, meme statut que le taux lui-meme. Rien n'en
    sort qui parle du prochain pari.
    """

    observed: int
    #: `1/cote` de chaque selection tranchee, dans l'ordre. Conserve plutot que
    #: somme : la loi exacte a besoin de chaque terme, et l'overround
    #: d'annulation se relit dessus.
    implied: list[float] = field(default_factory=list)
    #: Marge supposee du book, en fraction. Les probabilites implicites sont
    #: divisees par `1 + marge` — `1/cote` la porte, donc surestime la
    #: probabilite vraie.
    margin: float = 0.0

    @property
    def settled(self) -> int:
        return len(self.implied)

    @property
    def probabilities(self) -> list[float]:
        return [value / (1 + self.margin) for value in self.implied]

    @property
    def expected(self) -> float:
        """Victoires que les prix annoncaient."""
        return sum(self.probabilities)

    @property
    def gap(self) -> float | None:
        """Victoires observees moins victoires annoncees. Negatif = deficit."""
        return None if not self.settled else self.observed - self.expected

    @property
    def p_value(self) -> float:
        """`P(X <= observe)` : la probabilite d'un deficit au moins aussi grand.

        Unilaterale vers le bas. C'est le sens qui interesse — le cadre fait-il
        **moins** bien que ses propres prix — et le test est **conservateur par
        construction** : `1/cote` porte la marge, donc la barre est trop haute.
        La franchir est un constat solide ; ne pas la franchir n'accuse de rien.
        """
        if not self.settled:
            return 1.0
        return sum(poisson_binomial(self.probabilities)[: self.observed + 1])

    @property
    def annulling_overround(self) -> float | None:
        """Marge qu'il faudrait au book pour que l'ecart disparaisse.

        **La statistique qui ecarte la marge sans reconstruire le marche.** On
        ne peut pas devigger sans toutes les issues, et on n'en a pas besoin :
        ce facteur dit exactement ce que la marge peut ou ne peut pas expliquer.
        Mesure : 26,6 % sur la population reelle, quand les books tournent entre
        3 et 8 %.

        `None` quand rien n'est observe — aucune marge ne ramene un attendu sur
        zero — et quand l'observe depasse deja l'attendu : il n'y a alors rien a
        annuler.
        """
        if not self.observed or not self.settled:
            return None
        factor = sum(self.implied) / self.observed
        return factor - 1 if factor > 1 else None

    @property
    def expected_label(self) -> str:
        """« 44,3 » — la virgule decimale, comme partout dans l'interface."""
        return f"{self.expected:.1f}".replace(".", ",")

    @property
    def overround_label(self) -> str:
        """« 26,6 % », et rien quand il n'y a pas d'ecart a annuler."""
        overround = self.annulling_overround
        return "" if overround is None else f"{overround * 100:.1f} %".replace(".", ",")

    @property
    def p_label(self) -> str:
        """« 0,053 ». Trois decimales : le constat bascule entre 0,05 et 0,06,
        et deux decimales feraient lire deux fois le meme chiffre de part et
        d'autre du seuil."""
        return f"{self.p_value:.3f}".replace(".", ",")

    def with_margin(self, margin: float) -> Residual:
        """Le meme releve, lu sous une marge de book supposee.

        Sert a montrer **ou le constat cesse de tenir** plutot qu'a choisir une
        marge : a 0 % il vaut p = 0,016, a 5 % p = 0,053. La frontiere est a
        ~4 %, et une page qui n'afficherait que le premier chiffre durcirait un
        resultat que l'effectif ne porte pas.
        """
        return Residual(observed=self.observed, implied=self.implied, margin=margin)

    @property
    def fragility(self) -> int | None:
        """Victoires de plus qu'il faudrait pour que le constat perde son seuil.

        **Recalculee a chaque lecture, jamais figee.** Un verdict qui bouge d'un
        facteur deux sur six resultats saisis n'est pas un verdict, et cette
        page est servie sur une base vivante.

        Elle est **asymetrique**, et le libelle doit le dire : autant de defaites
        de plus le **renforceraient**. C'est un instantane qui bougera vite dans
        les deux sens, ce qui plaide pour l'horodater et non pour le taire.

        `None` quand le constat ne tient deja pas : il n'y a rien a effacer.
        """
        if not self.settled or self.p_value >= ALPHA:
            return None
        distribution = poisson_binomial(self.probabilities)
        # La somme complete de la loi vaut 1, donc depasse ALPHA : le compte
        # existe toujours des lors que le constat tient. Un repli en fin de
        # boucle serait une branche qu'aucun appel ne peut atteindre, donc
        # qu'aucun test ne peut verifier.
        return next(
            extra
            for extra in range(1, self.settled - self.observed + 1)
            if sum(distribution[: self.observed + extra + 1]) >= ALPHA
        )


def clustered_p_value(groups: list[list[float]], observed: int, margin: float = 0.0) -> float:
    """`P(X <= observe)` quand les issues d'un meme groupe tombent **ensemble**.

    **Borne conservatrice, pas une estimation.** La loi de Poisson-binomiale
    suppose l'independance ; deux selections sur la meme rencontre ne sont pas
    independantes — sur les donnees reelles, quatre des cinq paires sont tombees
    du meme cote. La verite est entre les deux, et ce calcul donne le pire cas :
    chaque groupe devient un tirage unique rendant tous ses succes ou aucun, de
    probabilite moyenne. L'esperance ne bouge pas, la variance monte.

    Mesure : 0,0161 devient 0,0227 a marge nulle. L'effet est modeste ici — le
    verdict tient dans les deux lectures — mais il se dit, et il grossira si les
    selections multiples se multiplient.
    """
    distribution = [1.0]
    for group in groups:
        scaled = [value / (1 + margin) for value in group]
        if len(scaled) == 1:
            steps = {0: 1 - scaled[0], 1: scaled[0]}
        else:
            mean = sum(scaled) / len(scaled)
            steps = {0: 1 - mean, len(scaled): mean}
        following = [0.0] * (len(distribution) + max(steps))
        for count, mass in enumerate(distribution):
            for step, weight in steps.items():
                following[count + step] += mass * weight
        distribution = following
    return sum(distribution[: observed + 1])


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
