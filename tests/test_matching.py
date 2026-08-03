from __future__ import annotations

import pytest

from myassistantbet.config import Settings
from myassistantbet.services.matching import (
    Candidate,
    is_confident,
    levenshtein,
    lookup_alias,
    normalize,
    resolve_team,
    save_alias,
    score_candidates,
    similarity,
)

ALLSVENSKAN = [
    (376, "BK Hacken"),
    (377, "Djurgardens IF"),
    (378, "AIK"),
    (379, "Hammarby"),
    (380, "Malmo FF"),
    (381, "IFK Norrkoping"),
]


# -- Normalisation ----------------------------------------------------------


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("BK Hacken", "hacken"),
        ("Häcken", "hacken"),
        ("Djurgårdens IF", "djurgardens"),
        ("Malmö FF", "malmo"),
        ("IFK Norrköping", "norrkoping"),
        ("Paris Saint-Germain", "paris saint germain"),
        ("1. FC Köln", "koln"),
        ("Schalke 04", "schalke"),
        ("Borussia M'gladbach", "borussia m gladbach"),
    ],
)
def test_normalisation(raw: str, expected: str) -> None:
    assert normalize(raw) == expected


def test_un_nom_entierement_compose_de_jetons_de_club_survit() -> None:
    # « AIK » ne doit pas se normaliser en chaine vide.
    assert normalize("AIK") == "aik"
    assert normalize("FC") == "fc"


def test_les_jetons_ne_sont_retires_qu_aux_extremites() -> None:
    # Retirer « city »/« united » au milieu confondrait les deux clubs.
    assert normalize("Manchester United") != normalize("Manchester City")


# -- Distance ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("left", "right", "expected"),
    [("", "", 0), ("abc", "abc", 0), ("abc", "abd", 1), ("", "abc", 3), ("chat", "chats", 1)],
)
def test_levenshtein(left: str, right: str, expected: int) -> None:
    assert levenshtein(left, right) == expected


def test_similarite() -> None:
    assert similarity("hacken", "hacken") == 1.0
    assert similarity("hacken", "") == 0.0
    assert similarity("hacken", "backen") == pytest.approx(1 - 1 / 6)


# -- Classement des candidats -----------------------------------------------


def test_correspondance_exacte_apres_normalisation() -> None:
    candidates = score_candidates("Häcken", ALLSVENSKAN)

    assert candidates[0].apifootball_id == 376
    assert candidates[0].score == 1.0
    assert is_confident(candidates) is True


def test_correspondance_avec_suffixe_different() -> None:
    candidates = score_candidates("Djurgarden", ALLSVENSKAN)

    assert candidates[0].apifootball_name == "Djurgardens IF"
    assert is_confident(candidates) is True


def test_candidats_tries_par_score_decroissant() -> None:
    scores = [item.score for item in score_candidates("Malmo", ALLSVENSKAN)]

    assert scores == sorted(scores, reverse=True)


def test_aucune_correspondance_sure_sur_un_nom_etranger() -> None:
    candidates = score_candidates("Olympique de Marseille", ALLSVENSKAN)

    assert is_confident(candidates) is False


def test_deux_clubs_trop_proches_exigent_une_confirmation() -> None:
    manchester = [(33, "Manchester United"), (50, "Manchester City")]

    candidates = score_candidates("Manchester Utd", manchester)

    assert candidates[0].apifootball_name == "Manchester United"
    assert is_confident(candidates) is False, "l'ecart avec le second est trop faible"


def test_aucun_candidat() -> None:
    assert is_confident([]) is False


def test_candidat_unique_suffisamment_proche() -> None:
    assert is_confident([Candidate(1, "Hacken", 0.9)]) is True
    assert is_confident([Candidate(1, "Hacken", 0.5)]) is False


# -- Alias ------------------------------------------------------------------


def test_alias_memorise_et_relu(migrated: Settings) -> None:
    save_alias("Häcken", 376, "BK Hacken", "manual", migrated)

    alias = lookup_alias("Häcken", migrated)

    assert alias is not None
    assert alias.apifootball_id == 376
    assert lookup_alias("Inconnu", migrated) is None


def test_alias_prime_sur_la_deduction(migrated: Settings) -> None:
    # Choix manuel contre-intuitif : il doit gagner malgre un meilleur score ailleurs.
    save_alias("BK Hacken", 379, "Hammarby", "manual", migrated)

    resolution = resolve_team("BK Hacken", ALLSVENSKAN, migrated)

    assert resolution.from_alias is True
    assert resolution.matched.apifootball_id == 379


def test_alias_manuel_ecrase_l_alias_automatique(migrated: Settings) -> None:
    save_alias("Häcken", 376, "BK Hacken", "auto", migrated)
    save_alias("Häcken", 378, "AIK", "manual", migrated)

    alias = lookup_alias("Häcken", migrated)

    assert alias.apifootball_id == 378


# -- Resolution complete ----------------------------------------------------


def test_resolution_sure_memorise_l_alias(migrated: Settings) -> None:
    resolution = resolve_team("Häcken", ALLSVENSKAN, migrated)

    assert resolution.resolved is True
    assert resolution.matched.apifootball_id == 376
    assert lookup_alias("Häcken", migrated) is not None, "l'alias est memorise pour la suite"


def test_resolution_incertaine_ne_devine_pas(migrated: Settings) -> None:
    resolution = resolve_team("Olympique de Marseille", ALLSVENSKAN, migrated)

    assert resolution.resolved is False
    assert resolution.matched is None
    assert resolution.candidates, "les candidats sont conserves pour le formulaire manuel"
    assert lookup_alias("Olympique de Marseille", migrated) is None


def test_resolution_sans_memorisation(migrated: Settings) -> None:
    resolve_team("Häcken", ALLSVENSKAN, migrated, remember=False)

    assert lookup_alias("Häcken", migrated) is None
