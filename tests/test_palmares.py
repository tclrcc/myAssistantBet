"""Le palmarès profond : ce qu'il affirme, et ce qu'il refuse d'affirmer.

**La ligne dit où un joueur est déjà allé.** Elle ne vaut que si son
dénominateur est juste : « demi-finale 2019, 6 participations » et « demi-finale
2019, 2 participations » ne décrivent pas le même joueur, et c'est tout l'objet
du chantier.
"""

from __future__ import annotations

import pytest

from myassistantbet.services import palmares as P


def _m(round_id, tournoi, tier, surface, annee, result):
    return {
        "roundId": round_id,
        "date": f"{annee}-06-01T00:00:00.000Z",
        "result": result,
        "player1": {"name": "Iga Swiatek"},
        "player2": {"name": "Autre Joueuse"},
        "tournament": {"name": tournoi, "tier": tier, "court": {"name": surface}},
    }


def test_le_tour_se_lit_sur_l_identifiant_et_pas_sur_un_libelle() -> None:
    """`roundId` est servi à 100 %, et son sens a été **mesuré** en comptant les
    matchs par (tournoi, tour) : 12 en rend 1 par édition, 10 en rend 2, 9 en
    rend 4."""
    assert P.ROUND_BY_ID[12] == "the final"
    assert P.ROUND_BY_ID[10] == "semifinals"
    assert P.ROUND_BY_ID[9] == "quarterfinals"


@pytest.mark.parametrize("exclu", [1, 2, 3, 8, 11, 13, 14, 15, 16, 17])
def test_les_qualifications_et_les_formats_par_equipes_sont_exclus(exclu: int) -> None:
    """**Mesuré** : `1` à `3` ont des maxima de 33, 23 et 16 matchs par édition —
    aucun tour de tableau ne fait 33 matchs. `8` est dominé par `Finals` et
    `United Cup`, `13` à `17` par la Fed Cup. Les nommer « tour » serait
    inventer."""
    assert exclu not in P.ROUND_BY_ID


def test_une_edition_ne_retient_que_son_tour_le_plus_profond() -> None:
    matchs = [
        _m(4, "Roland Garros", "Grand Slam", "Clay", "2024", "6-1 6-2"),
        _m(9, "Roland Garros", "Grand Slam", "Clay", "2024", "6-3 6-4"),
        _m(12, "Roland Garros", "Grand Slam", "Clay", "2024", "6-2 6-1"),
    ]
    editions = P.summarise(matchs, "Iga Swiatek")
    assert len(editions) == 1
    assert editions[0].round == "the final"
    assert editions[0].won is True


def test_une_finale_perdue_vaut_finaliste_et_pas_finale() -> None:
    """Le rang du tour ne dit pas l'issue, et les confondre serait l'erreur la
    plus visible de la ligne."""
    perdue = [_m(12, "US Open", "Grand Slam", "Hard", "2023", "4-6 3-6")]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(perdue, "Iga Swiatek"))
    assert "finaliste 2023" in P.fragment("Swiatek", entree, "Grand Slam")

    gagnee = [_m(12, "US Open", "Grand Slam", "Hard", "2023", "6-4 6-3")]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(gagnee, "Iga Swiatek"))
    assert "vainqueur 2023" in P.fragment("Swiatek", entree, "Grand Slam")


def test_le_denominateur_accompagne_toujours_le_resultat() -> None:
    """C'est l'angle demandé : un finaliste de Masters 1000 qui y va pour la
    deuxième fois n'aborde pas le match comme un habitué."""
    habitue = [
        _m(10, f"Tournoi {i}", "ATP Masters 1000", "Hard", str(2010 + i), "6-1 6-2")
        for i in range(12)
    ]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(habitue, "Iga Swiatek"))
    rendu = P.fragment("Fils", entree, "ATP Masters 1000")
    assert "12 éditions" in rendu

    novice = [_m(10, "Tournoi A", "ATP Masters 1000", "Hard", "2026", "6-1 6-2")]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(novice, "Iga Swiatek"))
    assert "1 édition" in P.fragment("Tirante", entree, "ATP Masters 1000")


def test_la_surface_est_celle_du_meilleur_resultat_et_non_du_lot() -> None:
    """Une catégorie s'étale sur plusieurs surfaces : en rendre une seule pour
    l'ensemble serait faux. Ce qui intéresse est **où** il est allé le plus
    loin."""
    matchs = [
        _m(9, "Roland Garros", "Grand Slam", "Clay", "2024", "6-1 6-2"),
        _m(12, "Wimbledon", "Grand Slam", "Grass", "2025", "6-3 6-4"),
    ]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(matchs, "Iga Swiatek"))
    rendu = P.fragment("Swiatek", entree, "Grand Slam")
    assert "gazon" in rendu, "la surface du meilleur résultat, et en français"
    assert "terre" not in rendu


def test_jamais_venu_a_ce_niveau_se_dit() -> None:
    """**Ici l'affirmation est sûre** : la catégorie est servie par la source,
    pas rapprochée par un libellé."""
    matchs = [_m(9, "Un 500", "ATP 500", "Hard", "2025", "6-1 6-2")]
    entree = P.Palmares(player="Iga Swiatek", editions=P.summarise(matchs, "Iga Swiatek"))
    assert P.fragment("Fils", entree, "ATP Masters 1000") == "Fils ATP Masters 1000 jamais joué"


def test_une_edition_sans_categorie_est_ignoree_jamais_rangee_ailleurs() -> None:
    """`tier` est absent de 15 % de l'historique profond. Une édition sans
    catégorie ne doit pas gonfler le dénominateur d'une autre."""
    matchs = [
        _m(12, "Un grand", "Grand Slam", "Hard", "2025", "6-1 6-2"),
        _m(12, "Un obscur", None, "Hard", "2019", "6-1 6-2"),
    ]
    editions = P.summarise(matchs, "Iga Swiatek")
    assert {e.tier for e in editions} == {"Grand Slam", ""}
    entree = P.Palmares(player="Iga Swiatek", editions=editions)
    assert "1 édition" in P.fragment("Swiatek", entree, "Grand Slam")


def test_les_alias_historiques_d_une_categorie_sont_reunis() -> None:
    """Réunir deux graphies du même niveau chez le même fournisseur est un fait
    de renommage, pas une déduction."""
    assert P.normalise_tier("ATP World Tour Masters 1000") == "ATP Masters 1000"
    assert P.normalise_tier("ATP World Tour 250") == "ATP 250"
    assert P.normalise_tier(None) == ""
    assert P.normalise_tier("None") == ""


def test_la_categorie_du_tournoi_vient_de_la_taxonomie_saisie() -> None:
    """Rien ne se déduit d'un libellé : « Masters » vaut pour Monte-Carlo comme
    pour le tournoi de fin d'année. La taxonomie est saisie à la main."""
    assert P.tier_for("masters_1000", "wta") == "WTA 1000"
    assert P.tier_for("masters_1000", "atp") == "ATP Masters 1000"
    assert P.tier_for("grand_slam", "atp") == "Grand Slam"
    assert P.tier_for("", "atp") == ""
    assert P.tier_for("masters_1000", "") == ""


def test_un_abandon_ne_compte_pas_comme_un_resultat() -> None:
    """Un set inachevé désigne celui qui menait quand le jeu s'est arrêté."""
    matchs = [_m(12, "Un tournoi", "Grand Slam", "Hard", "2025", "6-1 1-0")]
    assert P.summarise(matchs, "Iga Swiatek") == []


def test_le_score_se_lit_dans_les_deux_graphies_de_source() -> None:
    """**`matches-played` écrit `result` en espaces, `event/get` écrit `score`
    en virgules.** Découper sur la virgule rendait un seul set sur la première,
    donc un palmarès vide sur 589 matchs — un zéro parfaitement crédible."""
    espaces = [_m(12, "T", "Grand Slam", "Hard", "2025", "3-6 7-6(5) 6-0")]
    editions = P.summarise(espaces, "Iga Swiatek")
    assert len(editions) == 1 and editions[0].won is True
