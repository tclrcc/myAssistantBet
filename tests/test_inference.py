"""Le socle inferentiel, verifie contre des valeurs connues.

**Aucune base, aucun reglage, aucun mock.** C'est le seul module du projet dont
les sorties se comparent a des valeurs publiees, et c'est ce qui le rend
verifiable : il decide desormais de ce que la page affirme, et une erreur y
serait invisible — un intervalle faux de deux points ne casse rien, il fait
seulement lire un constat la ou il n'y en a pas.

Les valeurs de reference viennent de deux sources : les tables usuelles pour le
chi2 et l'intervalle de Wilson, et les selections reelles de la base pour les
cas qui ont motive chaque choix.
"""

from __future__ import annotations

import pytest

from myassistantbet.services.inference import (
    ALPHA,
    Evidence,
    _upper_gamma,
    benjamini_hochberg,
    binomial_test,
    cramers_v,
    evidence,
    jaccard,
    omnibus,
    required_for_gap,
    required_sample,
    two_proportions,
    wilson,
)

TOLERANCE = 1e-3


# -- Intervalle de Wilson ---------------------------------------------------


@pytest.mark.parametrize(
    ("won", "settled", "low", "high"),
    [
        # Les trois valeurs de reference du cahier des charges, prises sur les
        # lignes reelles de la page.
        (0, 7, 0.0, 0.35434),
        # **Borne haute corrigee : 0,7421 et non 0,740.** Le cahier annoncait
        # 0,740, ce qui sort de la tolerance de 1e-3 demandee. Verifie par une
        # seconde methode, independante de l'implementation — les racines du
        # polynome `p²(n+z²) − p(2np̂+z²) + np̂² = 0`, qui est la definition meme
        # de l'intervalle — les deux donnent 0,742095. L'ecart etait un arrondi
        # du cahier, pas un defaut de calcul : l'affichage arrondi de la page
        # (« [47 – 74] ») ne pouvait pas le reveler.
        (29, 47, 0.474263, 0.742095),
        (24, 40, 0.446, 0.736),
    ],
)
def test_wilson_contre_les_valeurs_de_reference(
    won: int, settled: int, low: float, high: float
) -> None:
    bornes = wilson(won, settled)

    assert bornes is not None
    assert bornes[0] == pytest.approx(low, abs=TOLERANCE)
    assert bornes[1] == pytest.approx(high, abs=TOLERANCE)


def test_wilson_garde_une_largeur_a_zero_reussite() -> None:
    """C'est la raison du choix de Wilson.

    L'intervalle normal donne ici une largeur **nulle** — il affirmerait que le
    taux vaut exactement 0 — et c'est precisement le cas ou la page a le plus
    besoin d'etre juste.
    """
    bornes = wilson(0, 7)

    assert bornes is not None
    assert bornes[0] == 0.0
    assert bornes[1] > 0.3


def test_wilson_se_resserre_quand_l_echantillon_grandit() -> None:
    court = wilson(5, 10)
    long = wilson(50, 100)

    assert court is not None and long is not None
    assert (long[1] - long[0]) < (court[1] - court[0])


def test_wilson_sans_rien_de_tranche() -> None:
    """None et non `(0, 1)` : il n'y a pas de proportion, donc pas de mesure."""
    assert wilson(0, 0) is None


def test_la_correction_de_continuite_elargit_sans_deplacer() -> None:
    """Le symptome d'une correction mal ecrite est l'**asymetrie**.

    Sur une proportion de 0,5, l'intervalle corrige doit rester centre : les
    deux signes du terme sous la racine sont opposes, et les confondre decale
    la borne haute de six points sans toucher la basse.
    """
    brut = wilson(5, 10)
    corrige = wilson(5, 10, continuity=True)

    assert brut is not None and corrige is not None
    assert corrige[0] == pytest.approx(0.2014, abs=TOLERANCE)
    assert corrige[1] == pytest.approx(0.7986, abs=TOLERANCE)
    assert corrige[0] + corrige[1] == pytest.approx(1.0, abs=TOLERANCE), "centre sur 0,5"
    assert corrige[0] < brut[0] and corrige[1] > brut[1], "elle elargit, jamais l'inverse"


@pytest.mark.parametrize(("won", "settled"), [(0, 4), (0, 7), (4, 4), (29, 47)])
def test_la_correction_de_continuite_reste_dans_les_bornes(won: int, settled: int) -> None:
    """Une proportion extreme garde sa borne triviale, qui est exacte."""
    corrige = wilson(won, settled, continuity=True)

    assert corrige is not None
    assert 0.0 <= corrige[0] <= corrige[1] <= 1.0


# -- Test binomial exact ----------------------------------------------------


def test_le_binomial_exact_contre_une_reference_fixe() -> None:
    """Sept pertes d'affilee a pile ou face : une fois sur soixante-quatre."""
    assert binomial_test(0, 7) == pytest.approx(0.015625, abs=1e-9)


def test_quatre_pertes_ne_sont_pas_un_constat() -> None:
    """**La mesure qui a fait choisir le test plutot que l'intervalle.**

    L'intervalle de Wilson de `0/4` monte a 48,99 %, donc « ecarte 50 » — et la
    page l'aurait presente comme la ligne la plus informative du lot. Quatre
    pertes d'affilee arrivent une fois sur huit.
    """
    bornes = wilson(0, 4)

    assert bornes is not None and bornes[1] < 0.5, "l'intervalle ecarte la reference"
    assert binomial_test(0, 4) == pytest.approx(0.125, abs=1e-9), "le test, lui, ne conclut pas"


def test_le_binomial_est_bilateral_par_la_densite() -> None:
    """Doubler une queue serait faux des que la reference n'est pas 0,5.

    La loi n'y est plus symetrique : la methode de la densite additionne tous
    les tirages au moins aussi improbables que l'observe, ce qui est la
    definition valable dans les deux cas.
    """
    assert binomial_test(8, 10, 0.3) == pytest.approx(0.00159, abs=1e-4)
    # Le mode de la loi : aucun tirage n'est plus probable, donc tous entrent.
    assert binomial_test(3, 10, 0.3) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize("reference", [0.0, 1.0, -0.5, 2.0])
def test_une_reference_impossible_ne_conclut_rien(reference: float) -> None:
    """1.0 plutot qu'une exception : un seuil mal saisi ne doit pas empecher
    de servir la page, meme regle que les seuils reglables."""
    assert binomial_test(3, 10, reference) == 1.0


def test_le_binomial_sans_rien_de_tranche() -> None:
    assert binomial_test(0, 0) == 1.0


# -- Deux proportions : la tranche contre son complement --------------------


def test_la_reference_est_le_complement_et_pas_une_piece() -> None:
    """**La mesure qui a fait changer la reference du cahier des charges.**

    `22/34` contre pile ou face donne p = 0,12 : non prouve. Contre le reste de
    la population — `8/33` — il donne p = 0,0013. La reference d'origine
    declarait non prouve ce que les donnees etablissent, ce qui est l'inverse
    exact du defaut qu'on corrige.
    """
    assert binomial_test(22, 34) > ALPHA
    assert two_proportions(22, 34, 8, 33) == pytest.approx(0.0013, abs=1e-4)


def test_les_deux_hypotheses_posees_d_avance_par_le_gabarit() -> None:
    """Elles sont **dirigees**, donc unilaterales, et c'est licite : le gabarit
    de prompt affirme qu'une confiance 4 doit battre une confiance 3 avant que
    la moindre donnee existe.

    A deux tests, Bonferroni donne un seuil a 0,025. Les deux passent.
    """
    confiance = two_proportions(18, 26, 12, 41, directed=True)
    palier = two_proportions(22, 34, 8, 29, directed=True)

    assert confiance == pytest.approx(0.00147, abs=1e-5)
    assert palier == pytest.approx(0.00333, abs=1e-5)
    assert max(confiance, palier) < 0.025


def test_un_test_unilateral_est_plus_permissif_que_le_bilateral() -> None:
    """C'est pourquoi la direction doit etre declaree **avant** les donnees :
    la choisir apres coup revient a diviser son seuil par deux en silence."""
    dirige = two_proportions(18, 26, 12, 41, directed=True)
    bilateral = two_proportions(18, 26, 12, 41)

    assert dirige < bilateral


def test_deux_tranches_identiques_ne_disent_rien() -> None:
    assert two_proportions(5, 10, 10, 20) == pytest.approx(1.0, abs=1e-9)


@pytest.mark.parametrize(
    ("won", "settled", "other_won", "other_settled"),
    [(0, 0, 5, 10), (5, 10, 0, 0), (0, 0, 0, 0)],
)
def test_une_tranche_vide_ne_se_compare_a_rien(
    won: int, settled: int, other_won: int, other_settled: int
) -> None:
    assert two_proportions(won, settled, other_won, other_settled) == 1.0


# -- Omnibus : l'axe avant la ligne -----------------------------------------


def test_l_omnibus_d_un_axe_qui_separe() -> None:
    """Le palier reel, sur la population a anteriorite etablie.

    Exact : `p = 0,0016`. Le chi2 donnait `0,0023` — meme verdict ici, ce qui
    est le cas ordinaire. Le chi2 ne trompe que sur les axes a petits
    effectifs, et c'est justement la qu'on ne peut pas s'en apercevoir.
    """
    resultat = omnibus([(22, 34), (8, 29), (0, 4)])

    assert resultat is not None
    assert resultat.exact
    assert resultat.chi_squared is None, "un test exact ne produit aucune statistique"
    assert resultat.p_value == pytest.approx(0.0016, abs=1e-4)
    assert resultat.separates


def test_l_omnibus_du_niveau_separe_et_le_chi2_disait_l_inverse() -> None:
    """**Le faux negatif qui a fait passer l'omnibus a l'exact.**

    Cet axe reel porte quinze lignes sous huit paris, donc des effectifs
    attendus tres au-dessous de 5 : le chi2 y donne p = 0,083 — l'axe ne passe
    pas, et « 1re division — Europe » est demotee. Le test exact donne
    p = 0,044 : l'axe passe et la ligne tient.

    L'erreur ne cassait rien. Elle retirait une ligne de la page en presentant
    son retrait comme une regle qui fonctionne — la forme la plus couteuse
    qu'un defaut puisse prendre ici.
    """
    niveau = [(17, 32), (2, 13), (0, 2), (1, 1), (1, 1), (9, 18)]
    axe = omnibus(niveau)

    assert axe is not None
    assert axe.exact
    assert axe.p_value == pytest.approx(0.0443, abs=1e-3)
    assert axe.separates
    assert two_proportions(2, 13, 28, 54) < ALPHA, "et la ligne passe dans son axe"


def test_le_chi2_reste_un_repli_declare_au_dela_du_budget(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'exact est gratuit sur cinq axes reels et coute 632 ms sur le sixieme.

    Au-dela du budget, le chi2 reprend la main — mais la sortie **le dit**. Un
    verdict exact et une approximation ne se lisent pas au meme titre, et c'est
    leur confusion qui a produit le faux negatif ci-dessus.
    """
    monkeypatch.setattr("myassistantbet.services.inference.EXACT_BUDGET", 10)
    axe = omnibus([(17, 32), (2, 13), (0, 2), (1, 1), (1, 1), (9, 18)])

    assert axe is not None
    assert not axe.exact
    assert axe.chi_squared == pytest.approx(9.73, abs=0.01)
    assert axe.degrees == 5
    assert axe.p_value == pytest.approx(0.0832, abs=1e-3)


def test_le_cout_de_l_exact_se_compte_avant_de_l_engager() -> None:
    """Compter les tables coute des microsecondes, les enumerer une seconde.

    Les comptes reels : confiance 27, palier 144, famille 1 078, niveau 3 113 —
    et le marche 777 437, seul axe au-dela du budget.
    """
    from myassistantbet.services.inference import _table_count

    assert _table_count([26, 41], 30) == 27
    assert _table_count([34, 29, 4], 30) == 144
    assert _table_count([32, 13, 2, 1, 1, 18], 30) == 3113
    # Le marche ne totalise que 29 reussites, la ou les autres axes en portent
    # 30 : la ligne « Handicap » y est a 0/4 et la marge n'est pas la meme.
    assert _table_count([23, 13, 6, 6, 4, 4, 3, 3, 2], 29) == 777_437


def test_l_omnibus_se_tait_sur_un_axe_qui_ne_pose_pas_la_question() -> None:
    """Moins de deux lignes peuplees, ou un axe dont tout est gagne ou tout est
    perdu : les taux y sont egaux par construction, et un test dirait
    « homogene » sans avoir rien mesure."""
    assert omnibus([]) is None
    assert omnibus([(5, 10)]) is None
    assert omnibus([(5, 10), (0, 0)]) is None
    assert omnibus([(0, 10), (0, 20)]) is None
    assert omnibus([(10, 10), (20, 20)]) is None


@pytest.mark.parametrize(
    ("chi", "degrees"),
    # Valeurs critiques a 5 % des tables usuelles du chi2.
    [(3.8415, 1), (5.9915, 2), (7.8147, 3), (11.0705, 5), (18.3070, 10)],
)
def test_la_p_valeur_du_chi2_retombe_sur_les_tables(chi: float, degrees: int) -> None:
    """La gamma incomplete est ecrite a la main faute de `scipy` : elle se
    verifie donc contre les seuils publies, et sur toute la plage de degres que
    la page peut produire."""
    assert _upper_gamma(degrees / 2, chi / 2) == pytest.approx(0.05, abs=1e-4)


@pytest.mark.parametrize(
    ("chi", "degrees", "attendu"),
    # Valeurs medianes et courantes : elles empruntent l'**autre** branche de la
    # gamma incomplete — la serie — et c'est celle qui sert le plus, puisqu'elle
    # calcule la p-valeur des axes qui **ne** separent pas.
    [(0.4549, 1, 0.50), (1.3863, 2, 0.50), (2.3660, 3, 0.50), (1.0, 1, 0.3173)],
)
def test_la_p_valeur_du_chi2_hors_de_la_zone_critique(
    chi: float, degrees: int, attendu: float
) -> None:
    """La branche en serie n'etait couverte par aucun seuil critique.

    Toutes les valeurs publiees a 5 % passent par la fraction continue : les
    tester seules laissait sans verification la moitie de la fonction — celle
    qui decide du sort de sport, marche, famille et niveau.
    """
    assert _upper_gamma(degrees / 2, chi / 2) == pytest.approx(attendu, abs=1e-3)


def test_un_chi2_nul_vaut_une_p_valeur_de_un() -> None:
    """Le repli asymptotique peut tomber sur un axe parfaitement homogene :
    la statistique est alors nulle, et la fonction ne doit pas y prendre le
    logarithme de zero."""
    assert _upper_gamma(0.5, 0.0) == 1.0
    assert _upper_gamma(2.5, 0.0) == 1.0


def test_un_axe_parfaitement_homogene_ne_separe_rien() -> None:
    """Chi2 nul, donc p = 1. C'est l'axe « type d'angle » de la base reelle :
    deux lignes a 1/2, et rien a en tirer."""
    resultat = omnibus([(1, 2), (1, 2)])

    assert resultat is not None
    assert resultat.p_value == 1.0
    assert not resultat.separates


# -- Association entre deux axes --------------------------------------------


def test_le_v_de_cramer_sur_le_couple_palier_confiance() -> None:
    """**Deux axes, un phenomene.**

    Sur la population filtree, 51 des 67 selections tombent sur la diagonale :
    le palier et la confiance annoncee s'accordent trois fois sur quatre. Leurs
    deux resultats confirmatoires n'en font donc qu'un, et les presenter comme
    deux constats independants reviendrait a compter deux fois la meme chose.
    """
    valeur = cramers_v([[22, 12], [4, 25], [0, 4]])

    assert valeur is not None
    assert valeur == pytest.approx(0.543, abs=TOLERANCE)


def test_le_v_de_cramer_aux_deux_extremes() -> None:
    identiques = cramers_v([[10, 0], [0, 10]])
    independants = cramers_v([[10, 10], [10, 10]])

    assert identiques == pytest.approx(1.0, abs=TOLERANCE)
    assert independants == pytest.approx(0.0, abs=TOLERANCE)


def test_le_v_de_cramer_se_tait_sur_une_table_degeneree() -> None:
    assert cramers_v([]) is None
    assert cramers_v([[5, 3]]) is None
    assert cramers_v([[5, 0], [3, 0]]) is None, "une seule colonne peuplee"


def test_le_jaccard_compare_deux_lignes_et_non_deux_axes() -> None:
    """Il garde son emploi la ou V ne convient pas : deux **lignes** de deux
    axes, comme « Tennis » et « Masters 1000 » qui portent les memes 32
    selections."""
    assert jaccard({1, 2, 3}, {1, 2, 3}) == 1.0
    assert jaccard({1, 2, 3}, {4, 5}) == 0.0
    assert jaccard({1, 2}, {2, 3}) == pytest.approx(1 / 3)


def test_le_jaccard_de_deux_ensembles_vides_ne_divise_pas_par_zero() -> None:
    """Deux regroupements sans aucune selection ne se recouvrent pas : ils
    n'existent pas."""
    assert jaccard(set(), set()) == 0.0


# -- Puissance ---------------------------------------------------------------


def test_le_volume_requis_par_un_ecart_observe() -> None:
    assert required_sample(0.63, 0.44) == 105, "ceil(104.14)"


def test_un_ecart_plus_etroit_demande_plus_de_volume() -> None:
    assert required_sample(0.63, 0.60) > required_sample(0.63, 0.44)


def test_un_ecart_nul_ne_devient_jamais_testable() -> None:
    """Aucun volume n'y suffit : None, et non un nombre gigantesque qui se
    lirait comme une cible atteignable un jour."""
    assert required_sample(0.5, 0.5) is None


def test_le_filtre_d_anteriorite_met_le_contraste_de_confiance_a_portee() -> None:
    """**Ce que le recalcul sur la population filtree a revele.**

    Le meme contraste demandait ~92 selections par ligne sur les 104, pour 40 et
    63 disponibles — hors d'atteinte. Sur les 67, il en demande ~21 pour 26 et
    41 : il est deja testable.
    """
    avant = required_sample(24 / 40, 25 / 63)
    apres = required_sample(18 / 26, 12 / 41)

    assert avant is not None and apres is not None
    assert avant == 92
    assert apres == 21
    assert apres < min(26, 41), "le contraste est atteint"


def test_le_volume_requis_pour_un_ecart_qu_on_se_fixe() -> None:
    """Variante posee **avant** d'avoir les donnees, pour le panneau de cout de
    la granularite : « combien faudrait-il pour voir dix points d'ecart »."""
    assert required_for_gap(0.45, 0.10) == required_sample(0.55, 0.45)
    assert required_for_gap(0.45, 0.0) is None
    assert required_for_gap(0.95, 0.10) is None, "un taux ne depasse pas 100 %"


# -- Ce qu'une ligne porte ---------------------------------------------------


def test_le_constat_d_une_ligne_assemble_les_quatre_comptes() -> None:
    constat = evidence(22, 34, 8, 33)

    assert constat.rate == pytest.approx(22 / 34)
    assert constat.reference == pytest.approx(8 / 33)
    assert constat.p_value == pytest.approx(0.0013, abs=1e-4)
    assert constat.discriminant
    assert constat.required == required_sample(22 / 34, 8 / 33)
    assert constat.interval is not None


def test_une_ligne_dont_l_intervalle_ecarte_mais_dont_le_test_ne_conclut_pas() -> None:
    """Les deux lectures divergent, et c'est le test qui tranche."""
    constat = evidence(0, 4, 30, 63)

    assert constat.interval is not None and constat.interval[1] < 0.5
    assert not constat.discriminant


def test_une_ligne_sans_complement_ne_discrimine_rien() -> None:
    """Un axe d'une seule ligne : il n'y a rien contre quoi la lire.

    Ni verdict, ni cible de volume — un ecart contre rien ne devient pas
    testable en accumulant.
    """
    seule = evidence(5, 10, 0, 0)

    assert not seule.discriminant
    assert seule.reference is None
    assert seule.required is None


def test_deux_tranches_sans_aucune_reussite_ne_se_departagent_pas() -> None:
    """Marge degeneree : toutes les tables possibles sont la table observee.

    Le cas se produit sur un axe entierement perdu — deux lignes a `0/2` — et
    doit rendre 1.0 plutot que de diviser par zero.
    """
    assert two_proportions(0, 2, 0, 2) == 1.0


def test_un_constat_dirige_porte_sa_marque() -> None:
    """La page ne doit pas presenter au meme titre un resultat confirmatoire et
    une trouvaille : le drapeau voyage avec le constat."""
    assert evidence(18, 26, 12, 41, directed=True).directed
    assert not evidence(18, 26, 12, 41).directed


def test_un_constat_se_construit_par_sa_fonction() -> None:
    """`Evidence` est gele et ses six champs se deduisent des quatre comptes :
    les laisser saisir separement les ferait diverger."""
    with pytest.raises(AttributeError):
        evidence(1, 2, 3, 4).p_value = 0.01  # type: ignore[misc]
    assert Evidence(1, 2, 3, 4, p_value=1.0).rate == 0.5


# -- Multiplicite ------------------------------------------------------------


def test_benjamini_hochberg_compte_sans_filtrer() -> None:
    assert benjamini_hochberg([0.001, 0.02, 0.4, 0.6]) == 2
    assert benjamini_hochberg([0.4, 0.6, 0.8]) == 0
    assert benjamini_hochberg([]) == 0


def test_benjamini_hochberg_ne_retient_rien_sur_les_lignes_de_la_page() -> None:
    """**Et c'est pourquoi il informe au lieu de filtrer.**

    Vingt-neuf lignes testees a 5 % laissent attendre ~1,5 ligne significative
    par pur hasard. Appliquer la correction aux lignes retirerait tout et
    reinstallerait le defaut qu'on corrige — masquer la seule qui affirme
    quelque chose. Le compte se lit a cote du nombre de lignes repliees.
    """
    lignes = [0.0115, 0.0225, 0.0241] + [0.2] * 26

    assert benjamini_hochberg(lignes) == 0
    assert sum(1 for valeur in lignes if valeur < ALPHA) == 3


def test_les_deux_hypotheses_d_avance_survivent_a_leur_propre_correction() -> None:
    """Le lot confirmatoire est separe, et c'est tout l'enjeu : melange aux
    vingt-neuf autres, le seul resultat que cette base ait etabli passerait pour
    du bruit."""
    assert benjamini_hochberg([0.00147, 0.00333]) == 2
