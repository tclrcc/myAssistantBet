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

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services.history import add_pick, analysis, set_result, worksheet
from myassistantbet.services.inference import MARGIN_REFERENCE, Residual
from myassistantbet.services.manual import build, save

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
    assert report.residual.settled + report.residual_late.settled == report.settled


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

    ligne = worksheet(session_id, migrated).coverage_line

    assert "1 sur 3 sans antériorité établie" in ligne
    assert "3 sur 3 sans cote obtenue" in ligne


def test_le_compteur_se_tait_quand_tout_est_couvert(migrated: Settings) -> None:
    """Un compteur a zero sur chaque session serait du bruit : c'est le manque
    qui doit se voir."""
    session_id = _lot(migrated, [("2.00", "win", False)])
    pick_id = worksheet(session_id, migrated).picks[0].pick_id
    db.execute("UPDATE picks SET price_real = 1.95 WHERE id = ?", (pick_id,), settings=migrated)

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
