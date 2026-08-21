"""L'anteriorite, le residu au prix, et le compteur qui les rend saisissables.

Le fil de ces tests : **un taux n'est pas interpretable, un residu au prix
l'est**. Un coin de tableau a 82 % a failli devenir un resultat du projet alors
que ses prix en annoncaient deja 69 % — le controle manquait, et rien dans la
page ne pouvait le signaler.

Mais un residu ne vaut que si le prix est un prix d'**avant-match**. D'ou
l'anteriorite, qui n'est pas un filtre de proprete : c'est ce qui fait du prix
un prix.
"""

from __future__ import annotations

from collections.abc import Iterator
from unittest import mock

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import history as history_module
from myassistantbet.services.history import (
    LATE_REASONS,
    HistoryError,
    Horizon,
    RateRow,
    _overlaps,
    add_pick,
    analysis,
    feedback,
    late,
    overlap_matrix,
    populations,
    refresh_late,
    set_result,
    worksheet,
)
from myassistantbet.services.inference import MARGIN_REFERENCE, Residual
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt, save_prompt
from myassistantbet.services.thresholds import COUPON_TRACKING, save_toggle

LOIN = "2099-01-01"


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, home: str, cote: str = "2.00") -> int:
    return save(
        build(
            "football",
            "Match amical",
            home,
            f"Adv {home}",
            LOIN,
            "20:45",
            f"{home} {cote}\nAdv {home} {cote}",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _pick(
    settings: Settings,
    session_id: int,
    event_id: int,
    *,
    price: str,
    result: str,
    apres_coup_denvoi: bool = False,
) -> int:
    pick_id = add_pick(
        session_id,
        "safe",
        "1N2",
        "Domicile",
        event_id=str(event_id),
        price=price,
        settings=settings,
    )
    set_result(pick_id, result, settings)
    if apres_coup_denvoi:
        # Le coup d'envoi est repousse **avant** l'heure de saisie plutot que
        # l'inverse : `created_at` est ecrit par le service et le reecrire
        # testerait la fixture au lieu de la regle.
        db.execute(
            "UPDATE events SET commence_time = '2000-01-01T00:00:00Z' WHERE id = ?",
            (event_id,),
            settings=settings,
        )
        # **Et le retard se recalcule**, comme le scan le fait des qu'un coup
        # d'envoi bouge. Sans cet appel la fixture testerait un etat que la
        # production ne produit pas : une colonne `tardive` desynchronisee de
        # l'horaire du match.
        refresh_late(event_id, settings)
    return pick_id


def _lot(settings: Settings, lignes: list[tuple[str, str, bool]]) -> int:
    """Une session portant ces (cote, resultat, saisie tardive)."""
    session_id = 0
    for index, (price, result, tardif) in enumerate(lignes):
        event_id = _match(settings, f"Club {index}")
        session_id = board_service.toggle_selection(event_id, True, settings)
        _pick(
            settings,
            session_id,
            event_id,
            price=price,
            result=result,
            apres_coup_denvoi=tardif,
        )
    return session_id


# -- Les deux surfaces portent la meme clause ---------------------------------


def test_le_bloc_du_prompt_ecarte_les_selections_tardives(migrated: Settings) -> None:
    """**`feedback()` n'appliquait aucune clause d'antériorité, et la marge tenait
    à quatre lignes** — la première tardive au rang 64 sur une fenêtre de 60.

    Une clause absente mais inoffensive est invisible : rien ne l'aurait
    signalée, et personne n'aurait pensé à l'ajouter le jour où
    `FEEDBACK_SUSPENDED` tombe. Le prédicat est celui d'`analysis()`, écrit une
    seule fois — deux écritures de la même règle auraient divergé.
    """
    session_id = _lot(
        migrated,
        [("2.00", "win", False), ("2.00", "loss", False), ("2.00", "win", True)],
    )
    assert session_id

    bloc = feedback(settings=migrated)

    assert bloc.settled == 2, "la tardive sort du dénominateur"
    assert analysis(settings=migrated).settled == 2, "les deux surfaces s'accordent"


def test_la_fenetre_se_prend_apres_le_filtre_et_non_avant(migrated: Settings) -> None:
    """**Un `LIMIT` posé côté SQL prendrait N lignes puis en retirerait les
    tardives.** La fenêtre se rétrécirait à proportion du retard, et le bloc
    annoncerait « les N dernières » sur moins que N. Elle porte des sélections
    **éligibles**, pas des lignes lues."""
    lignes = [("2.00", "win", True)] * 3 + [("2.00", "win", False)] * 3
    assert _lot(migrated, lignes)

    with mock.patch.object(history_module, "FEEDBACK_WINDOW", 3):
        bloc = feedback(settings=migrated)

    assert bloc.settled == 3, "trois éligibles, et non trois lues dont trois tardives"


# -- L'anteriorite -----------------------------------------------------------


def test_une_selection_enregistree_avant_le_coup_d_envoi(migrated: Settings) -> None:
    session_id = _lot(migrated, [("2.00", "win", False)])

    assert worksheet(session_id, migrated).picks[0].antecedence


def test_une_selection_enregistree_apres_ne_l_etablit_pas(migrated: Settings) -> None:
    session_id = _lot(migrated, [("2.00", "win", True)])

    assert not worksheet(session_id, migrated).picks[0].antecedence


def test_sans_match_rattache_l_anteriorite_n_est_pas_etablie(migrated: Settings) -> None:
    """Aucun coup d'envoi, donc rien contre quoi dater la saisie. L'absence de
    preuve ne se lit pas comme une preuve d'absence, mais elle ne se lit pas non
    plus comme une anteriorite."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    add_pick(session_id, "safe", "1N2", "Sans match", price="2.00", settings=migrated)

    orpheline = next(p for p in worksheet(session_id, migrated).picks if not p.event_id)
    assert not orpheline.antecedence


def test_le_libelle_ne_dit_jamais_enregistre_apres_coup(migrated: Settings) -> None:
    """**Sens unique, et le vocabulaire doit le respecter.** `created_at` est
    l'heure d'enregistrement dans l'application, pas celle de la decision : une
    saisie tardive d'une analyse faite a temps ressemble a un pari pose apres le
    match. La base peut prouver l'anteriorite, jamais son absence.
    """
    session_id = _lot(migrated, [("2.00", "win", True)])

    ligne = worksheet(session_id, migrated).coverage_line

    assert "antériorité établie" in ligne
    assert "après coup" not in ligne
    assert "après le match" not in ligne


# -- Le residu au prix -------------------------------------------------------


def test_le_residu_compare_les_victoires_aux_prix(migrated: Settings) -> None:
    """Quatre selections a 2.00 annoncent deux victoires. Une seule tombe."""
    _lot(migrated, [("2.00", "win", False)] + [("2.00", "loss", False)] * 3)

    residu = analysis(migrated).residual

    assert residu.settled == 4
    assert residu.observed == 1
    assert residu.expected == pytest.approx(2.0)
    assert residu.gap == pytest.approx(-1.0)


def test_un_taux_eleve_sur_des_favoris_courts_ne_produit_aucun_residu(
    migrated: Settings,
) -> None:
    """**Le controle qui manquait, et qui a retire un resultat du projet.**

    Huit selections a 1.25 — donc 80 % de probabilite implicite — dont sept
    gagnent. Le taux affiche 87 %, ce qui se lit comme un excellent
    etiquetage ; les prix en annoncaient 80 %, et l'ecart n'est rien.
    """
    _lot(migrated, [("1.25", "win", False)] * 7 + [("1.25", "loss", False)])

    residu = analysis(migrated).residual

    assert residu.observed / residu.settled == pytest.approx(0.875)
    assert residu.expected == pytest.approx(6.4)
    assert residu.p_value > 0.05, "87 % sur des favoris a 1.25 n'etablit rien"


def test_les_deux_populations_ne_se_melangent_jamais(migrated: Settings) -> None:
    """Deux residus, deux chiffres, **jamais additionnes**.

    Leur difference est le diagnostic : sur les donnees reelles, le residu est
    nul sur les selections sans anteriorite etablie — 20 victoires pour 20,25
    payees — quand il vaut -9,31 sur les autres. Un prix qui colle a ce point au
    resultat est un prix releve en le connaissant.
    """
    _lot(
        migrated,
        [("2.00", "loss", False)] * 4 + [("2.00", "win", True)] * 4,
    )

    report = analysis(migrated)

    assert (report.residual.settled, report.residual.observed) == (4, 0)
    assert (report.residual_late.settled, report.residual_late.observed) == (4, 4)
    assert report.settled == 4, "seules les selections decrites par la page"
    assert report.without_antecedence == 4


def test_une_selection_sans_cote_sort_des_deux_residus(migrated: Settings) -> None:
    """Et de ceux-la seulement : elle compte partout ailleurs. Le compte ferme
    l'addition, comme partout sur cette page."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    # Sans match : une cote absente et un evenement absent sont deux manques
    # differents, et c'est le premier qu'on teste ici.
    pick_id = add_pick(session_id, "safe", "1N2", "Sans cote", settings=migrated)
    set_result(pick_id, "win", migrated)

    report = analysis(migrated)

    assert report.unpriced == 1
    assert report.residual.settled == 1
    # La selection sans match reste comptee : son retard n'est pas demontre,
    # faute de coup d'envoi contre quoi la dater.
    assert report.settled == 2


# -- La marge, ecartee sans reconstruire le marche ---------------------------


def test_l_overround_qui_annulerait_le_constat(migrated: Settings) -> None:
    """**La statistique qui ecarte la marge sans devigger.** On ne peut pas
    reconstruire le marche complet, et on n'en a pas besoin : ce facteur dit ce
    que la marge peut ou ne peut pas expliquer.

    Quatre selections a 2.00 annoncent 2 victoires, une seule tombe : il
    faudrait 100 % de marge pour que l'attendu descende a 1.
    """
    _lot(migrated, [("2.00", "win", False)] + [("2.00", "loss", False)] * 3)

    assert analysis(migrated).residual.annulling_overround == pytest.approx(1.0)


def test_aucun_overround_quand_l_observe_depasse_l_attendu(migrated: Settings) -> None:
    """Il n'y a alors rien a annuler, et rendre un nombre negatif ferait lire une
    marge a l'envers."""
    _lot(migrated, [("2.00", "win", False)] * 3 + [("2.00", "loss", False)])

    assert analysis(migrated).residual.annulling_overround is None


def test_la_marge_de_reference_affaiblit_le_constat(migrated: Settings) -> None:
    """Le constat doit etre lu **avec** son point de comparaison : `1/cote`
    porte la marge du book, donc l'attendu brut est trop haut. Sur les donnees
    reelles, p passe de 0,016 a 0,053 — le seuil bascule entre les deux."""
    _lot(migrated, [("1.50", "loss", False)] * 6 + [("1.50", "win", False)] * 4)

    residu = analysis(migrated).residual
    sous_marge = residu.with_margin(MARGIN_REFERENCE)

    assert sous_marge.expected < residu.expected
    assert sous_marge.p_value > residu.p_value
    assert sous_marge.observed == residu.observed, "seul le referentiel bouge"


def test_la_fragilite_dit_ce_qui_effacerait_le_constat(migrated: Settings) -> None:
    """**Recalculee a chaque lecture, jamais figee.** Un verdict qui bouge d'un
    facteur deux sur six resultats saisis n'est pas un verdict, et cette page est
    servie sur une base vivante."""
    _lot(migrated, [("1.25", "loss", False)] * 8 + [("1.25", "win", False)] * 4)

    residu = analysis(migrated).residual

    assert residu.p_value < 0.05
    assert residu.fragility is not None and residu.fragility >= 1


def test_aucune_fragilite_quand_le_constat_ne_tient_pas(migrated: Settings) -> None:
    """Il n'y a rien a effacer, et afficher un nombre ferait croire a un
    resultat qui tiendrait a peu."""
    _lot(migrated, [("2.00", "win", False)] * 2 + [("2.00", "loss", False)] * 2)

    assert analysis(migrated).residual.fragility is None


def test_le_residu_ne_porte_aucun_champ_financier() -> None:
    """Meme garde-fou que `FeedbackRow` : aucune mise, aucun gain, aucune
    esperance. Le residu compare des issues tranchees a des prix deja
    enregistres — il ne projette rien."""
    interdits = ("stake", "mise", "profit", "roi", "gain", "bankroll", "yield", "ev")
    champs = {champ.name.lower() for champ in Residual.__dataclass_fields__.values()}

    assert not champs & set(interdits)


# -- Le compteur vivant ------------------------------------------------------


def test_le_compteur_annonce_les_deux_couvertures(migrated: Settings) -> None:
    """« 3 sur 8 sans antériorité établie · 5 sur 8 sans cote obtenue ».

    Un filtre dit ce qui a ete perdu, un compteur evite de le perdre : c'est le
    seul endroit du produit ou l'information arrive assez tot pour changer
    quelque chose.
    """
    session_id = _lot(migrated, [("2.00", "win", True)] + [("2.00", "win", False)] * 2)
    # **La cote obtenue n'est reclamee que si le suivi de l'argent est ouvert** :
    # elle ne peut venir que d'une mise. Il l'est par defaut depuis le 20/08 —
    # l'usage a change — et l'eteindre retire la reclamation plutot que de
    # demander une valeur qui n'existera jamais.
    ligne = worksheet(session_id, migrated).coverage_line

    assert "1 sur 3 sans antériorité établie" in ligne
    assert "3 sur 3 sans cote obtenue" in ligne

    save_toggle(COUPON_TRACKING, "0", migrated)
    assert "cote obtenue" not in worksheet(session_id, migrated).coverage_line


def test_le_compteur_se_tait_quand_tout_est_couvert(migrated: Settings) -> None:
    """Un compteur a zero sur chaque session serait du bruit : c'est le manque
    qui doit se voir."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    pick_id = worksheet(session_id, migrated).picks[0].pick_id
    db.execute("UPDATE picks SET price_real = 1.95 WHERE id = ?", (pick_id,), settings=migrated)
    save_toggle(COUPON_TRACKING, "1", migrated)

    assert worksheet(session_id, migrated).coverage_line == ""


def test_le_compteur_est_rendu_sur_la_feuille_de_session(
    migrated: Settings, client: TestClient
) -> None:
    session_id = _lot(migrated, [("2.00", "win", True)])

    page = client.get(f"/history/{session_id}").text

    assert "sans antériorité établie" in page
    assert "n'existent qu'à la saisie" in page


# -- Le bloc de tete ---------------------------------------------------------


def test_le_bloc_de_tete_porte_le_residu_et_sa_reserve(
    migrated: Settings, client: TestClient
) -> None:
    """**Le fait et la reserve a la meme taille de caractere.** C'est le seul
    endroit de la page ou une reserve n'a pas le droit d'etre en note : le
    constat ne tient qu'a marge basse, et quelques resultats l'effacent."""
    _lot(migrated, [("1.25", "loss", False)] * 8 + [("1.25", "win", False)] * 4)

    page = client.get("/stats").text

    assert "payée(s) par les prix" in page
    assert "de marge pour expliquer l'écart" in page
    assert f"À {MARGIN_REFERENCE * 100:.0f} % de marge" in page
    assert "suffiraient à l'effacer" in page


def test_le_bloc_de_tete_n_affirme_aucune_competence(
    migrated: Settings, client: TestClient
) -> None:
    """Trois interdits, et le premier est le plus tentant : « les prix disaient
    X, tu as fait Y » affirmerait une comparaison de competence que rien
    n'etablit. Le second interdit le present general — ce sont **ces**
    selections-la, pas une propriete de la methode."""
    _lot(migrated, [("1.25", "loss", False)] * 8 + [("1.25", "win", False)] * 4)

    page = client.get("/stats").text

    assert "les prix disaient" not in page
    assert "tes sélections perdent" not in page
    assert "tu bats" not in page and "tu ne bats pas" not in page


def test_la_page_est_datee(migrated: Settings, client: TestClient) -> None:
    """Un verdict qui bouge d'un facteur deux sur six saisies n'en est pas un :
    l'axe « niveau de competition » est passe de p = 0,0443 a p = 0,0195 sur six
    resultats, la base etant servie en continu."""
    _lot(migrated, [("2.00", "win", False)])

    page = client.get("/stats").text

    assert "arrêté au" in page
    assert analysis(migrated).as_of_label


def test_la_completude_du_lot_ne_filtre_rien(migrated: Settings, client: TestClient) -> None:
    """`reconstructed` porte sur le **denominateur du taux de selection**, pas
    sur la valeur des selections. Le confondre avec l'anteriorite ferait ecarter
    des sessions parfaitement mesurables — deux natives seulement portent des
    resultats, un tel filtre blanchirait la page."""
    _lot(migrated, [("2.00", "win", False)])

    page = client.get("/stats").text

    if "reconstruit" in page:
        assert "complétude du lot" in page


def test_le_bloc_de_tete_porte_sa_date(migrated: Settings, client: TestClient) -> None:
    """**Dans le bloc, pas en pied de page.** Un lecteur qui revient trois jours
    plus tard doit savoir sans chercher que les quelques resultats qui effacent
    le constat sont peut-etre deja tombes. Un chiffre de tete date ailleurs est
    un chiffre de tete non date."""
    _lot(migrated, [("1.25", "loss", False)] * 8 + [("1.25", "win", False)] * 4)

    bloc = client.get("/stats").text.split("residual-head")[1].split("</div>")[0]

    assert "as-of" in bloc
    assert analysis(migrated).as_of_label[:5] in bloc


def test_ni_le_code_ni_la_page_n_affirment_le_mecanisme(
    migrated: Settings, client: TestClient
) -> None:
    """La page dit ce qu'elle **voit** — un residu nul d'un cote, negatif de
    l'autre. Que le prix ait ete releve en connaissant l'issue est une
    **inference** : plausible, ecrite au conditionnel dans `CLAUDE.md`, et
    affirmee nulle part ailleurs.
    """
    _lot(migrated, [("2.00", "win", True), ("2.00", "loss", False)])

    page = client.get("/stats").text

    for affirmation in ("en le connaissant", "après le résultat", "relevé après"):
        assert affirmation not in page


# -- Ce que la section des regroupements porte, et ce qu'elle replie ---------
#
# **Le critere d'acceptation est une propriete, jamais un nombre.** Il valait
# « 1 ligne portee sur 30 » a 104 selections, « 3 sur 29 » a 67, et la base bouge
# chaque jour : un nombre ecrit ici serait faux le jour ou on le recette. Ces
# tests montent donc leur propre lot et verifient la **regle**.


def _axe_tranchant(settings: Settings) -> None:
    """Un lot dont un axe separe nettement : SAFE gagne, FUN perd."""
    for index in range(12):
        event_id = _match(settings, f"Sûr {index}")
        session_id = board_service.toggle_selection(event_id, True, settings)
        _pick(settings, session_id, event_id, price="1.30", result="win")
    for index in range(12):
        event_id = _match(settings, f"Risqué {index}")
        session_id = board_service.toggle_selection(event_id, True, settings)
        pick_id = add_pick(
            session_id,
            "fun",
            "1N2",
            "Domicile",
            event_id=str(event_id),
            price="2.00",
            settings=settings,
        )
        set_result(pick_id, "loss", settings)


def test_aucune_ligne_n_est_portee_sur_un_intervalle_de_wilson(migrated: Settings) -> None:
    """**La regle qui a change le socle.** Sur la population reelle, « l'IC
    ecarte 50 % » retenait deux lignes a `0/4` (p = 0,12) et une dont la borne
    franchissait le seuil de 0,011 point. Une ligne portee doit passer les trois
    conditions, pas la plus permissive."""
    _lot(migrated, [("2.00", "loss", False)] * 4 + [("2.00", "win", False)] * 4)

    for row in analysis(migrated).carried_rows:
        assert row.axis_separates and row.axis_survives
        assert row.evidence.discriminant


def test_une_ligne_portee_affiche_toujours_sa_fragilite(migrated: Settings) -> None:
    """**Bloquante.** Un chiffre sans son effectif se lit comme un fait, et
    l'effectif ne suffit pas : une ligne a quarante paris peut tenir a un seul
    resultat. Sans elle, on retombe sur le « SCORE EXACT 100 % sur 2 »."""
    _axe_tranchant(migrated)

    portees = analysis(migrated).carried_rows

    assert portees, "ce lot doit porter au moins une ligne"
    for row in portees:
        assert row.fragility is not None and row.fragility >= 1


def test_une_ligne_repliee_n_a_pas_de_fragilite(migrated: Settings) -> None:
    """Il n'y a rien a faire tomber, et un nombre ferait croire a un verdict."""
    _lot(migrated, [("2.00", "win", False), ("2.00", "loss", False)])

    for rows in analysis(migrated).groups:
        for row in rows:
            if not row.carried:
                assert row.fragility is None


def test_la_fragilite_compte_bien_des_bascules(migrated: Settings) -> None:
    """Retourner ce nombre de resultats doit **effectivement** faire tomber le
    verdict : le calcul refait les deux tests, celui de l'axe et celui de la
    ligne, l'axe pouvant ceder le premier."""
    _axe_tranchant(migrated)
    ligne = next(row for row in analysis(migrated).carried_rows if row.settled > 4)

    assert ligne.fragility is not None
    assert ligne.fragility < ligne.settled, "une ligne ne tient jamais a tout son effectif"


def test_la_section_annonce_ce_qu_elle_replie(migrated: Settings, client: TestClient) -> None:
    """**Ce n'est plus une section de resultats, c'est un compteur de
    progression.** Le compte de repliees n'est pas un aveu d'echec : c'est le
    contenu de la section, et l'en-tete doit le dire plutot que le laisser
    deviner."""
    _lot(migrated, [("2.00", "win", False), ("2.00", "loss", False)])

    page = client.get("/stats").text

    assert "groups-fold" in page
    assert "ne s'écarte de sa référence" in page


def test_l_en_tete_porte_les_horizons(migrated: Settings, client: TestClient) -> None:
    """Le seul texte utile du bloc : la distance au moment ou il conclura."""
    _axe_tranchant(migrated)

    page = client.get("/stats").text

    assert "Ce que ces regroupements diront, et quand" in page
    assert "nécessaires" in page


def test_un_horizon_planifie_et_ne_conclut_rien(migrated: Settings) -> None:
    """**Un rythme de saisie n'est pas un resultat.**

    Une version precedente declarait une question tranchee au-dela d'un plafond
    de sessions : ca transformait une propriete de l'agenda en verdict
    statistique, et ca a bascule d'un « rien a mesurer » a un « atteignable »
    sur les memes donnees lues a travers deux populations. Un horizon dit
    seulement quand regarder a nouveau.
    """
    lointain = Horizon(question="x", have=10, need=5000)

    assert lointain.sessions > 0
    assert not hasattr(lointain, "reachable"), "aucun verdict ne sort d'un horizon"
    assert not hasattr(lointain, "undetectable")


def test_une_question_deja_tranchee_ne_reclame_plus_rien(migrated: Settings) -> None:
    atteint = Horizon(question="x", have=100, need=50)

    assert atteint.missing == 0
    assert atteint.sessions == 0


# -- La garde a l'ecriture ---------------------------------------------------


def _a_venir(settings: Settings) -> tuple[int, int]:
    """Une session portant un match a venir — le cas ordinaire."""
    event_id = _match(settings, "À venir")
    return board_service.toggle_selection(event_id, True, settings), event_id


def _match_commence(settings: Settings) -> tuple[int, int]:
    """Une session portant un match dont le coup d'envoi est passe."""
    event_id = _match(settings, "Déjà joué")
    session_id = board_service.toggle_selection(event_id, True, settings)
    db.execute(
        "UPDATE events SET commence_time = '2000-01-01T00:00:00Z' WHERE id = ?",
        (event_id,),
        settings=settings,
    )
    return session_id, event_id


#
# **Le compteur informait, la garde empeche**, et l'information seule n'a pas
# suffi : le couple horaire etait deja sous les yeux au moment de la saisie, et
# 37 des 110 selections tranchees ont ete posees apres le coup d'envoi.


def test_une_selection_sur_un_match_commence_entre_en_population_tardive(
    migrated: Settings,
) -> None:
    """**La garde ne refuse plus, et c'est une décision datée du 17/08/2026.**

    Elle réclamait un motif et se laissait contourner : 37 sélections tardives
    sur 52 n'ont jamais rien déclaré. Surtout, refuser ferait **disparaître la
    population qui porte la mesure du biais** — les tardives sont au-dessus de
    leur prix là où les antérieures sont en dessous, et l'écart entre les deux
    est la meilleure estimation disponible de ce que coûte une sélection écrite
    en connaissant le début du match.
    """
    session_id, event_id = _match_commence(migrated)

    pick_id = add_pick(
        session_id, "safe", "1N2", "Domicile", event_id=str(event_id), settings=migrated
    )

    ligne = db.query_one("SELECT tardive FROM picks WHERE id = ?", (pick_id,), settings=migrated)
    assert ligne["tardive"] == 1
    assert late(migrated).undeclared == 0, "en attente, donc pas encore tranchée"


def test_le_motif_reste_en_base_comme_information(migrated: Settings) -> None:
    """**Le motif n'est plus une autorisation, c'est une information.** Sans lui
    on saurait combien de selections sont tardives et jamais pourquoi — or les
    deux cas ne se ressemblent pas : une decision anterieure mal saisie porte une
    etiquette valide et un prix douteux, un pari pris en direct n'a ni l'une ni
    l'autre."""
    session_id, event_id = _match_commence(migrated)

    add_pick(
        session_id,
        "safe",
        "1N2",
        "Domicile",
        event_id=str(event_id),
        late_reason="differee",
        settings=migrated,
    )

    ligne = db.query_one("SELECT late_reason FROM picks ORDER BY id DESC", settings=migrated)
    assert ligne["late_reason"] == "differee"


@pytest.mark.parametrize("motif", ["differee", "live"])
def test_les_deux_motifs_sont_acceptes(migrated: Settings, motif: str) -> None:
    """Deux valeurs, et pas de texte libre : une decision prise a temps mais
    saisie tard porte une etiquette **valide** et un prix douteux ; un pari pris
    en cours de match porte les deux comme invalides."""
    assert motif in LATE_REASONS


def test_un_motif_inconnu_ne_s_ecrit_pas(migrated: Settings) -> None:
    """Un troisieme choix, ou un champ libre, ferait retomber dans le melange
    que cette colonne existe pour defaire. Il ne bloque plus l'ecriture — plus
    rien ne la bloque — mais il n'entre pas en base : la ligne compte alors parmi
    les tardives **sans motif declare**, ce qu'elle est."""
    session_id, event_id = _match_commence(migrated)

    pick_id = add_pick(
        session_id,
        "safe",
        "1N2",
        "Domicile",
        event_id=str(event_id),
        late_reason="parce que",
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT late_reason, tardive FROM picks WHERE id = ?", (pick_id,), settings=migrated
    )
    assert ligne["late_reason"] is None
    assert ligne["tardive"] == 1


def test_une_selection_a_venir_n_a_rien_a_justifier(migrated: Settings) -> None:
    """Le cas ordinaire : aucun geste de plus, et `late_reason` reste vide."""
    session_id, event_id = _a_venir(migrated)
    add_pick(
        session_id,
        "safe",
        "O/U",
        "Over",
        event_id=str(event_id),
        independence_note="angles indépendants",
        settings=migrated,
    )

    ligne = db.query_one("SELECT late_reason FROM picks ORDER BY id DESC", settings=migrated)
    assert ligne["late_reason"] is None


def test_une_selection_sans_match_echappe_a_la_garde(migrated: Settings) -> None:
    """Aucun coup d'envoi contre quoi la dater : la garde ne peut rien
    demontrer, et elle n'ecarte que ce qui est demontre."""
    session_id, _ = _a_venir(migrated)

    add_pick(session_id, "safe", "1N2", "Sans match", settings=migrated)

    ligne = db.query_one("SELECT late_reason FROM picks ORDER BY id DESC", settings=migrated)
    assert ligne["late_reason"] is None


# -- Le reste de E : incident, correlation, cout du cadre --------------------


def test_un_lot_entierement_passe_est_signale(migrated: Settings) -> None:
    """**Passer est un resultat, passer tout est un incident.** Le cas s'est
    produit — 34 matchs partis pour aucune selection — et la ligne se confondait
    avec une journee severe. Zero sur un lot parti ne se distingue pas d'un rendu
    jamais colle ni d'un import oublie."""
    session_id, _ = _a_venir(migrated)
    save_prompt(session_id, build_prompt(session_id, settings=migrated), migrated)

    ligne = next(r for r in analysis(migrated).by_session if r.session_id == session_id)

    assert ligne.lot and ligne.picks == 0
    assert ligne.degenerate


def test_une_session_qui_produit_n_est_pas_degeneree(migrated: Settings) -> None:
    session_id, event_id = _a_venir(migrated)
    save_prompt(session_id, build_prompt(session_id, settings=migrated), migrated)
    add_pick(session_id, "safe", "1N2", "Domicile", event_id=str(event_id), settings=migrated)

    ligne = next(r for r in analysis(migrated).by_session if r.session_id == session_id)

    assert not ligne.degenerate


def test_la_correlation_entre_paris_est_mesuree_et_bornee(migrated: Settings) -> None:
    """**Le residu suppose l'independance, et deux selections sur la meme
    rencontre ne le sont pas.** La borne conservatrice fait tomber les issues
    d'un meme match ensemble : l'esperance ne bouge pas, la variance monte, donc
    la p-valeur aussi. Sur les donnees reelles elle passe de 0,0161 a 0,0227 —
    modeste, et c'est pourquoi elle se mentionne au lieu de tout changer.
    """
    session_id = _lot(migrated, [("2.00", "win", False)])
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]
    for selection in ("Over", "Under"):
        pick_id = add_pick(
            session_id,
            "safe",
            "O/U",
            selection,
            event_id=str(event_id),
            price="2.00",
            independence_note="angles indépendants",
            settings=migrated,
        )
        set_result(pick_id, "win", migrated)

    report = analysis(migrated)

    assert report.clustered_selections == 2, "trois selections sur un match"
    assert report.clustered_p_value() >= report.residual.p_value


def test_sans_selection_partagee_la_borne_ne_bouge_pas(migrated: Settings) -> None:
    """Chaque pari sur son match : la borne conservatrice **est** la loi exacte,
    et la mention ne s'affiche pas."""
    _lot(migrated, [("2.00", "win", False), ("2.00", "loss", False)])

    report = analysis(migrated)

    assert report.clustered_selections == 0
    assert report.clustered_p_value() == pytest.approx(report.residual.p_value)


def test_le_cout_du_cadre_se_lit_par_match_et_porte_son_regime(
    migrated: Settings, client: TestClient
) -> None:
    """Une serie de poids qui melange trois regimes ne dit rien : le bloc de
    retour d'experience a ete servi sur trois sessions puis suspendu, et la garde
    d'anteriorite ne vaut que pour ce qui vient apres elle."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    save_prompt(session_id, build_prompt(session_id, settings=migrated), migrated)

    ligne = next(r for r in analysis(migrated).by_session if r.session_id == session_id)
    page = client.get("/stats").text

    assert ligne.tokens_per_match and ligne.tokens_per_match > 0
    # Les deux colonnes : le total et le rapporte au lot. Sans le premier, on ne
    # sait pas si le cadre s'est alourdi ou si le lot a retreci — et depuis la
    # garde d'anteriorite, le lot retrecira mecaniquement.
    assert ">/match<" in page.replace(" ", "")
    assert ligne.tokens > 0
    assert "Sél./match" in page
    # Le regime est dit sur la ligne : sans lui, une serie de poids melangerait
    # le bloc de retour d'experience servi, sa suspension et la garde.
    assert not ligne.feedback_active, "aucun prompt de ce lot n'a transmis de taux"
    assert "où le régime change" in page


# -- La redondance, generalisee mais pas bavarde -----------------------------


def _axe_de(nom: str, membres: object) -> tuple[str, list[RateRow]]:
    """Un axe d'une seule ligne, portant ces selections."""
    ligne = RateRow(key=nom, label=nom, won=1, lost=1)
    ligne.members = set(membres)
    return (nom, [ligne])


def test_un_recouvrement_fort_se_nomme(migrated: Settings) -> None:
    """Deux axes qui portent les memes selections : le second n'ajoute aucune
    observation au premier."""
    trouves, _ = _overlaps([_axe_de("sport", range(20)), _axe_de("niveau", range(20))])

    assert len(trouves) == 1
    assert trouves[0].share == 1.0
    assert "décrivent les mêmes 20 sélections" in trouves[0].note


def test_les_partiels_se_comptent_et_ne_s_enumerent_pas(migrated: Settings) -> None:
    """**Deux faits, pas trente signalements.** Trente avertissements de
    recouvrement faible reproduiraient sous un autre nom le defaut que cette
    page a mis huit lots a corriger : des lignes qui n'affirment rien, en nombre
    tel que plus personne ne les lit."""
    trouves, partiels = _overlaps([_axe_de("sport", range(20)), _axe_de("niveau", range(5, 25))])

    assert trouves == [], "0,60 <= J < 0,90 ne se nomme pas"
    assert partiels == 1


def test_la_matrice_ne_garde_que_les_paires_qui_disent_quelque_chose(
    migrated: Settings,
) -> None:
    """Elle existe pour qui veut verifier, elle ne se lit pas de haut en bas :
    une paire sans le moindre recouvrement n'y figure pas."""
    disjointe = overlap_matrix([_axe_de("sport", range(20)), _axe_de("niveau", range(20, 40))])
    proche = overlap_matrix([_axe_de("sport", range(20)), _axe_de("niveau", range(20))])

    assert disjointe == []
    assert len(proche) == 1


def test_une_ligne_portee_se_marque_sur_sa_barre(migrated: Settings, client: TestClient) -> None:
    """**Retirer un critere faux et retirer le critere sont deux operations
    differentes, et elles se ressemblent dans un diff.**

    En retirant `thin` et `inconclusive`, la barre a perdu toute qualification :
    le concept survivait dans le modele — `carried` — mais plus rien ne le
    montrait. La polarite est inversee au passage : on marque ce qui est porte
    plutot que d'attenuer le reste, parce qu'a ce volume vingt-neuf lignes sur
    trente-trois seraient attenuees et le contraste ne dirait plus rien.
    """
    _axe_tranchant(migrated)

    page = client.get("/stats").text
    portees = analysis(migrated).carried_rows

    assert portees, "ce lot doit porter au moins une ligne"
    assert "is-carried" in page
    assert page.count("is-carried") >= len(portees)


# -- Trois cas, et un seul compte les fondait --------------------------------
#
# **La cause racine n'etait ni un horodatage manquant ni un fuseau**, et la
# mesure l'a etablie avant qu'une ligne soit ecrite : sur les 230 selections
# tranchees de la base au 17/08/2026, **aucune** ne manque de `created_at` ni du
# `commence_time` de son match. Les 52 ecartees sont reellement posterieures au
# coup d'envoi — 15 declarees `differee`, 0 `live`, et 37 anterieures a la garde
# d'ecriture de la migration 034.
#
# Ce qui manquait n'est donc pas une donnee, c'est la **distinction** : une
# decision anterieure saisie en retard porte une etiquette valide et un prix
# douteux ; un pari pris en direct n'a ni l'une ni l'autre ; une ligne sans
# motif est d'une population **close**, qui ne grandira plus et ne se repare pas.


def test_les_motifs_de_saisie_tardive_se_comptent_a_part(migrated: Settings) -> None:
    session_id = _lot(migrated, [("2.00", "win", False)])
    for index, motif in enumerate(("differee", "live"), start=1):
        event_id = _match(migrated, f"Tardif {index}")
        board_service.toggle_selection(event_id, True, migrated)
        db.execute(
            "UPDATE events SET commence_time = '2000-01-01T00:00:00Z' WHERE id = ?",
            (event_id,),
            settings=migrated,
        )
        pick_id = add_pick(
            session_id,
            "safe",
            "1N2",
            "Domicile",
            event_id=str(event_id),
            price="2.00",
            late_reason=motif,
            settings=migrated,
        )
        set_result(pick_id, "win", migrated)

    report = analysis(migrated)

    assert report.without_antecedence == 2
    assert report.late_by_reason == {"differee": 1, "live": 1}
    libelles = dict(report.late_reasons)
    assert any("différée" in libelle for libelle in libelles)
    assert any("Live" in libelle for libelle in libelles)


def test_une_ligne_sans_motif_est_nommee_comme_telle(migrated: Settings) -> None:
    """**Population close.** Ces lignes sont anterieures a la garde d'ecriture,
    rien ne dira jamais laquelle des deux c'etait, et l'annoncer comme un manque
    de collecte enverrait chercher un defaut qui n'existe pas."""
    session_id = _lot(migrated, [("2.00", "win", True), ("2.00", "loss", False)])
    assert session_id

    report = analysis(migrated)

    assert report.late_by_reason == {"": 1}
    assert "population close" in dict(report.late_reasons).popitem()[0]


def test_l_horodatage_d_ecriture_vient_du_serveur(migrated: Settings) -> None:
    """**Ce que la garde suppose**, et qui n'avait aucun test : `created_at` est
    ecrit par le service, en UTC, au moment de l'insertion. Une valeur fournie
    par le formulaire ou par le rendu rendrait l'anteriorite declarable — donc
    sans valeur."""
    session_id = _lot(migrated, [("2.00", "win", False)])

    ligne = db.query_one("SELECT created_at FROM picks", settings=migrated)

    assert ligne["created_at"].endswith("Z"), "stockage en UTC, chaine ISO 8601"
    assert ligne["created_at"] >= "2026-", "l'heure du serveur, jamais une saisie"
    assert session_id


# -- Deux selections sur un meme match ---------------------------------------
#
# **La garde existait, la trace non.** `add_pick` refuse depuis la migration 028
# une seconde ligne sur une meme rencontre sans justification d'independance ;
# le refus s'affichait et rien n'en gardait la memoire, si bien qu'une garde qui
# mord souvent restait invisible. La journalisation est traitee avec le reste de
# l'ingestion ; ce qui manquait ici est de dire **ou** aller relire.
#
# Fixture reelle du 16/08/2026 : `Lens – Paris Saint Germain` porte
# « PSG O1.5 Eq. buts » et « Lens +0.5 Handicap » — deux lectures opposees du
# meme rapport de forces, notees l'une gagnante et l'autre perdante.


def test_les_rencontres_a_deux_selections_sont_nommees(migrated: Settings) -> None:
    session_id = _lot(migrated, [("2.00", "win", False)])
    event_id = _match(migrated, "Lens")
    board_service.toggle_selection(event_id, True, migrated)
    for market, selection, result in (
        ("Eq. buts", "PSG O1.5", "loss"),
        ("Handicap", "Lens +0.5", "win"),
    ):
        pick_id = add_pick(
            session_id,
            "safe",
            market,
            selection,
            event_id=str(event_id),
            price="1.90",
            independence_note="angles opposés, assumés",
            settings=migrated,
        )
        set_result(pick_id, result, migrated)

    report = analysis(migrated)

    assert report.clustered_selections == 1
    assert report.clustered_events == [("Lens – Adv Lens", 2)]


def test_le_compte_de_regroupement_vient_du_meme_calcul_que_la_borne(
    migrated: Settings,
) -> None:
    """**Un champ, jamais un recomptage a l'affichage.** `clustered_selections`
    et `clustered_events` sortent tous deux de `residual_clusters` : deux
    comptages paralleles auraient fini par ne plus designer les memes matchs, et
    la borne conservatrice du bloc de tete se serait mise a decrire un autre
    lot que la reserve affichee juste a cote."""
    session_id = _lot(migrated, [("2.00", "win", False), ("2.00", "loss", False)])
    event_id = _match(migrated, "Reims")
    board_service.toggle_selection(event_id, True, migrated)
    for market, result in (("1N2", "win"), ("O/U 2.5", "loss")):
        pick_id = add_pick(
            session_id,
            "safe",
            market,
            "Domicile",
            event_id=str(event_id),
            price="1.90",
            independence_note="angles distincts",
            settings=migrated,
        )
        set_result(pick_id, result, migrated)

    report = analysis(migrated)

    assert report.clustered_selections == sum(compte - 1 for _, compte in report.clustered_events)


def test_une_seconde_selection_sans_justification_est_refusee(migrated: Settings) -> None:
    """Seul controle bloquant du module avec la garde d'anteriorite : ailleurs
    une valeur manquante vaut « non renseigne », ici elle vaudrait « je ne me
    suis pas pose la question »."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]

    with pytest.raises(HistoryError, match="déjà une sélection"):
        add_pick(
            session_id,
            "safe",
            "O/U 2.5",
            "Over 2.5",
            event_id=str(event_id),
            price="1.90",
            settings=migrated,
        )


# -- La troisième population -------------------------------------------------
#
# **Le diagnostic d'origine etait faux, et sa correction change la conclusion.**
# On tenait les 52 selections ecartees pour un defaut de collecte ; la mesure du
# 17/08/2026 dit 0 sur 230 sans horodatage. Elles ont reellement ete ecrites
# apres le coup d'envoi — ce n'est plus un bug a reparer, c'est un choix d'usage.
#
# Et ce qu'elles mesurent n'existe nulle part ailleurs : elles sont au-dessus de
# leur prix la ou la population principale est en dessous, et l'ecart entre les
# deux est la meilleure estimation disponible du biais que produit une selection
# ecrite en connaissant le debut du match.


def test_les_trois_populations_somment_au_total(migrated: Settings) -> None:
    """**Le critère d'acceptation du §C.** Aucun indicateur ne les mélange ; ce
    compte-ci existe pour vérifier qu'aucune sélection ne s'est perdue entre
    elles — même rôle que `Analysis.recorded`, qui ne peut pas baisser."""
    session_id = _lot(migrated, [("2.00", "win", False), ("2.00", "loss", True)])
    event_id = _match(migrated, "Exploratoire")
    board_service.toggle_selection(event_id, True, migrated)
    add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Domicile",
        event_id=str(event_id),
        price="7.50",
        exploratory=True,
        settings=migrated,
    )

    compte = populations(migrated)

    assert (compte.main, compte.exploratory, compte.late) == (1, 1, 1)
    assert compte.consistent


def test_une_selection_tardive_ne_touche_aucun_indicateur_principal(
    migrated: Settings,
) -> None:
    session_id = _lot(migrated, [("2.00", "win", False), ("2.00", "loss", True)])
    assert session_id

    principale, tardive = analysis(migrated), late(migrated)

    assert principale.settled == 1
    assert tardive.settled == 1
    assert principale.consistent


def test_le_bloc_tardif_separe_declarees_et_non_declarees(migrated: Settings) -> None:
    """Une décision antérieure mal saisie porte une étiquette valide et un prix
    douteux ; un pari pris en direct n'a ni l'une ni l'autre. Les additionner
    détruisait la distinction."""
    session_id = _lot(migrated, [("2.00", "win", True)])
    event_id = _match(migrated, "Déclarée")
    board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "UPDATE events SET commence_time = '2000-01-01T00:00:00Z' WHERE id = ?",
        (event_id,),
        settings=migrated,
    )
    pick_id = add_pick(
        session_id,
        "safe",
        "1N2",
        "Domicile",
        event_id=str(event_id),
        price="2.00",
        late_reason="differee",
        settings=migrated,
    )
    set_result(pick_id, "win", migrated)

    report = late(migrated)

    assert report.declared == {"differee": 1}
    assert report.undeclared == 1
    assert [libelle for libelle, _ in report.reasons][-1] == "Aucun motif déclaré"


def test_un_report_de_match_leve_le_retard(migrated: Settings) -> None:
    """**Un match reporte n'a pas commence.** Une selection ecrite « apres »
    l'ancien horaire n'a rien vu, et la laisser en population tardive la ferait
    sortir des indicateurs principaux pour rien. C'est le seul cas où un report
    change une mesure déjà écrite — le projet en garde la trace depuis la
    migration 040."""
    session_id = _lot(migrated, [("2.00", "win", True)])
    pick = worksheet(session_id, migrated).picks[0]
    assert not pick.antecedence

    db.execute(
        "UPDATE events SET commence_time = '2099-06-01T20:45:00Z' WHERE id = ?",
        (pick.event_id,),
        settings=migrated,
    )
    refresh_late(int(pick.event_id or 0), migrated)

    assert late(migrated).settled == 0, "le report rend la sélection antérieure"
    assert analysis(migrated).settled == 1
