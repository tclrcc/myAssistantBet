"""Familles de marches : regrouper des libelles qui decrivent le meme pari.

Le risque propre a ce module n'est pas de rater un regroupement, c'est d'en
inventer un : ranger d'office un marche inconnu dans « Autre » ferait lire un
oubli comme une decision, et le marche nouveau qu'on essaie serait le premier a
disparaitre dans le fourre-tout.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import market_families
from myassistantbet.services.history import add_pick, analysis, set_result
from myassistantbet.services.manual import build, save


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _session(settings: Settings) -> tuple[int, int]:
    event_id = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings), event_id


def _pick(settings: Settings, session_id: int, event_id: int, market: str, result: str) -> None:
    pick_id = add_pick(
        session_id,
        "safe",
        market,
        "Over",
        event_id=str(event_id),
        # Ces tests montent plusieurs selections sur un **meme match** par
        # commodite — c'est le match le moins couteux a fabriquer. La note
        # d'independance est donc fournie d'office : c'est un test dedie qui
        # verifie qu'elle est exigee, pas chaque montage de fixture.
        independence_note="angles indépendants (fixture)",
        settings=settings,
    )
    set_result(pick_id, result, settings)


# -- Les deux niveaux de cle ------------------------------------------------


def test_la_cle_fine_ignore_casse_et_accents() -> None:
    """« Éq. buts » et « Eq. buts » sont le meme marche ecrit deux fois."""
    assert market_families.market_key("Éq. buts") == market_families.market_key("Eq. buts")
    assert market_families.market_key("O/U 2.5") == "o u 2 5"


def test_la_cle_de_famille_retire_la_ligne() -> None:
    """Une ligne est un parametre du marche, pas un autre marche.

    Sans cette regle, chaque seuil rencontre reclamerait sa propre
    correspondance et la liste « a classer » ne desemplirait jamais.
    """
    assert market_families.family_key("O/U 2.5") == "o u"
    assert market_families.family_key("O/U 3.5") == "o u"
    assert market_families.family_key("Jeux O/U 18.5") == "jeux o u"


def test_seuls_les_nombres_de_fin_sont_retires() -> None:
    """« Les 2 équipes marquent » garde son 2 : ce n'est pas une ligne, c'est
    une partie du nom. Retirer tout nombre ou qu'il soit aurait produit une cle
    que personne ne reconnait dans les reglages."""
    assert market_families.family_key("Les 2 équipes marquent (t. rég)") == (
        "les 2 equipes marquent t reg"
    )
    assert market_families.family_key("1N2") == "1n2"


# -- La table de correspondance ---------------------------------------------


def test_la_migration_rejoue_la_table_python() -> None:
    """Deux ecritures de la meme decision : elles doivent dire la meme chose.

    La migration classe ce qui existe au moment ou elle tourne, la table Python
    ce qui arrive ensuite. Les laisser diverger donnerait deux familles pour le
    meme marche selon la date d'installation. Le test relit le fichier plutot
    que d'en recopier la regle, comme celui des niveaux de competition.
    """
    chemin = Path(market_families.__file__).parent.parent / "migrations"
    sql = (chemin / "027_familles_de_marches.sql").read_text(encoding="utf-8")
    seed = {
        cle: famille
        for cle, famille in re.findall(r"\('([^']+)', '(\w+)'\)", sql)
        if famille in market_families.FAMILIES
    }

    assert seed == market_families.FAMILY_SEED


def test_toute_famille_seedee_existe() -> None:
    """Une faute de frappe ne casserait rien : le marche sortirait sous une
    famille qu'aucun libelle ne nomme, et ne serait jamais reclame puisqu'il
    porte bien une valeur."""
    for cle, famille in market_families.FAMILY_SEED.items():
        assert famille in market_families.FAMILIES, f"{cle} porte une famille inconnue"


def test_la_cle_seedee_est_deja_une_cle_de_famille() -> None:
    """Une entree ecrite « o u 2 5 » ne serait jamais trouvee : la recherche se
    fait sur la cle de famille, qui ne porte pas de ligne."""
    for cle in market_families.FAMILY_SEED:
        assert market_families.family_key(cle) == cle


# -- Regroupement -----------------------------------------------------------


def test_les_familles_rendent_lisible_ce_qui_ne_l_etait_pas(migrated: Settings) -> None:
    """Mesure qui justifie la tache : neuf regroupements dont six vus une seule
    fois, chacun mesurant le hasard. Groupes, trois familles passent le seuil."""
    session_id, event_id = _session(migrated)
    for market in ("Vainqueur", "1N2", "Double chance", "DC"):
        _pick(migrated, session_id, event_id, market, "win")
    for market in ("O/U", "O/U 2.5", "O/U 3.5", "Jeux O/U"):
        _pick(migrated, session_id, event_id, market, "loss")

    report = analysis(migrated)

    assert [(entry.rates.label, entry.rates.settled) for entry in report.by_family] == [
        ("Issue", 4),
        ("Total", 4),
    ]
    # Le deplie porte le detail fin **entier**, sans le seuil de la carte « Par
    # marché » : c'est tout l'interet du groupement, et la somme doit tomber
    # juste sur le total de sa ligne.
    total = next(entry for entry in report.by_family if entry.rates.key == "total")
    assert sorted(row.label for row in total.markets) == ["Jeux O/U", "O/U", "O/U 2.5", "O/U 3.5"]
    assert sum(row.settled for row in total.markets) == total.rates.settled


def test_une_ligne_differente_ne_fait_pas_une_famille_de_plus(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    for market in ("O/U 2.5", "O/U 3.5", "Jeux O/U 18.5"):
        _pick(migrated, session_id, event_id, market, "win")

    assert [entry.rates.key for entry in analysis(migrated).by_family] == ["total"]


def test_un_marche_inconnu_ne_tombe_pas_dans_autre(migrated: Settings) -> None:
    """« Autre » est une decision prise marche par marche, pas le fourre-tout de
    ce qu'on n'a pas regarde."""
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "Tirs cadrés Mbappé", "win")
    _pick(migrated, session_id, event_id, "1N2", "win")

    report = analysis(migrated)

    assert [entry.rates.key for entry in report.by_family] == ["issue"]
    assert report.unclassified_markets == 1
    somme = sum(entry.rates.settled for entry in report.by_family)
    assert somme + report.unclassified_markets == report.settled


def test_l_addition_se_ferme_sur_le_total_tranche(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    for market in ("Vainqueur", "Handicap", "O/U", "BTTS", "Score exact"):
        _pick(migrated, session_id, event_id, market, "win")

    report = analysis(migrated)

    assert sum(entry.rates.settled for entry in report.by_family) == report.settled
    assert report.unclassified_markets == 0


# -- Ce qui reste a classer -------------------------------------------------


def test_un_marche_a_classer_est_reclame_avec_son_compte(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "Tirs cadrés Mbappé", "win")
    _pick(migrated, session_id, event_id, "Tirs cadrés Mbappé", "pending")
    _pick(migrated, session_id, event_id, "1N2", "win")

    a_classer = market_families.unclassified(migrated)

    assert [(row.label, row.picks, row.settled) for row in a_classer] == [
        ("Tirs cadrés Mbappé", 2, 1)
    ]


def test_classer_un_marche_le_retire_de_la_liste(migrated: Settings) -> None:
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "Tirs cadrés Mbappé", "win")

    market_families.set_family("Tirs cadrés Mbappé", "autre", migrated)

    assert market_families.unclassified(migrated) == []
    assert [entry.rates.key for entry in analysis(migrated).by_family] == ["autre"]


def test_declasser_un_marche_le_fait_revenir(migrated: Settings) -> None:
    """Retirer le classement le fait revenir dans la liste, ce qui se voit — au
    lieu de le laisser sortir des statistiques sans un mot."""
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "1N2", "win")

    market_families.set_family("1N2", "", migrated)

    assert [row.key for row in market_families.unclassified(migrated)] == ["1n2"]


def test_reclasser_un_marche_reclasse_tout_son_historique(migrated: Settings) -> None:
    """La famille se resout **a la lecture**, elle n'est jamais recopiee sur la
    selection : reclasser doit reclasser tout ce qui est deja enregistre."""
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "O/U 2.5", "win")

    market_families.set_family("O/U", "autre", migrated)

    assert [entry.rates.key for entry in analysis(migrated).by_family] == ["autre"]


# -- Ecrans -----------------------------------------------------------------


def test_la_page_de_stats_affiche_les_familles(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, event_id = _session(isolated_settings)
    for market in ("Vainqueur", "O/U"):
        _pick(isolated_settings, session_id, event_id, market, "win")

    page = " ".join(client.get("/stats").text.split())

    assert "Par famille de marché" in page
    assert "Le marché fin" in page, "le détail reste dépliable"


def test_les_reglages_classent_un_marche(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _session(isolated_settings)
    _pick(isolated_settings, session_id, event_id, "Tirs cadrés Mbappé", "win")

    page = " ".join(client.get("/settings").text.split())
    assert "marché(s) à classer" in page
    assert "Tirs cadrés Mbappé" in page

    response = client.post(
        "/settings/families", data={"market_key": "Tirs cadrés Mbappé", "family": "autre"}
    )

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="families">')
    assert market_families.load(isolated_settings)["tirs cadres mbappe"] == "autre"


def test_les_reglages_disent_que_les_familles_ne_touchent_pas_au_prompt(
    client: TestClient,
) -> None:
    """La cote enregistree sur une selection fait foi : rien ne la reecrit, et
    un regroupement de lecture ne doit pas laisser croire le contraire."""
    page = " ".join(client.get("/settings").text.split())

    assert "Familles de marchés" in page
    assert "c'est une lecture, pas une réécriture" in page


def test_aucun_champ_financier_sur_une_famille() -> None:
    """Meme garde-fou que partout (SPEC.md section 9)."""
    from dataclasses import fields

    from myassistantbet.services.history import Family

    noms = {field.name for field in fields(Family)}
    assert not noms & {"roi", "profit", "stake", "mise", "gain"}


def test_le_detail_par_famille_est_dans_la_page(migrated: Settings) -> None:
    """Un marche vu deux fois ne dit rien seul ; il dit quelque chose sous sa
    famille. C'est pour ca que le deplie n'applique pas le seuil de la carte."""
    session_id, event_id = _session(migrated)
    _pick(migrated, session_id, event_id, "Nombre total de buts (t. rég)", "win")
    for _ in range(3):
        _pick(migrated, session_id, event_id, "O/U", "win")

    total = next(entry for entry in analysis(migrated).by_family if entry.rates.key == "total")

    assert [row.label for row in total.markets] == ["O/U", "Nombre total de buts (t. rég)"]
    assert total.markets[1].settled == 1, "gardé sous sa famille, malgré une seule ligne"
