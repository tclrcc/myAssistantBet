from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from myassistantbet.services.render import (
    Outcome,
    RenderableEvent,
    estimate_tokens,
    render_event,
)

PARIS = ZoneInfo("Europe/Paris")


def _event(**overrides: object) -> RenderableEvent:
    base = {
        "index": 3,
        "sport_key": "football",
        "competition": "Allsvenskan",
        "home": "Hacken",
        "away": "Djurgarden",
        "commence_local": datetime(2026, 8, 3, 17, 30, tzinfo=PARIS),
        "markets": {},
        "fetched_local": datetime(2026, 8, 3, 8, 12, tzinfo=PARIS),
    }
    base.update(overrides)
    return RenderableEvent(**base)  # type: ignore[arg-type]


def _lines(event: RenderableEvent) -> dict[str, str]:
    """Indexe le bloc rendu par libelle de ligne, pour des assertions lisibles."""
    result = {}
    for row in render_event(event).splitlines():
        if row.startswith("  ") and row[2] != " ":
            label = row[2:14].strip()
            result[label] = row[14:]
    return result


# -- En-tete ----------------------------------------------------------------


def test_entete() -> None:
    rendered = render_event(_event(markets={"h2h": [Outcome("Hacken", 2.55)]}))

    assert rendered.splitlines()[0] == (
        "### M3 · FOOT · Allsvenskan · Hacken – Djurgarden · 03/08 17:30"
    )


def test_entete_tennis() -> None:
    event = _event(sport_key="tennis", competition="US Open", home="Alcaraz", away="Sinner")

    assert render_event(event).splitlines()[0].startswith("### M3 · TENNIS · US Open ·")


# -- Marches ----------------------------------------------------------------


def test_1n2() -> None:
    event = _event(
        markets={
            "h2h": [
                Outcome("Hacken", 2.55),
                Outcome("Draw", 3.55),
                Outcome("Djurgarden", 2.6),
            ]
        }
    )

    assert _lines(event)["1N2"] == "2.55 / 3.55 / 2.60"


def test_1n2_sans_nul_devient_deux_issues() -> None:
    event = _event(
        sport_key="tennis",
        home="Alcaraz",
        away="Sinner",
        markets={"h2h": [Outcome("Alcaraz", 1.4), Outcome("Sinner", 2.95)]},
    )

    assert _lines(event)["1-2"] == "1.40 / 2.95"


def test_double_chance() -> None:
    event = _event(
        markets={
            "double_chance": [
                Outcome("Hacken/Draw", 1.48),
                Outcome("Hacken/Djurgarden", 1.29),
                Outcome("Draw/Djurgarden", 1.5),
            ]
        }
    )

    assert _lines(event)["DC"] == "1.48 / 1.29 / 1.50"


def test_double_chance_nommage_inattendu_rend_brut() -> None:
    # Plutot que de deviner un ordre faux, on affiche ce que le fournisseur envoie.
    event = _event(
        markets={"double_chance": [Outcome("1X", 1.48), Outcome("12", 1.29), Outcome("X2", 1.5)]}
    )

    assert _lines(event)["DC"] == "12 1.29 | 1X 1.48 | X2 1.50"


def test_over_under_limite_a_cinq_lignes_autour_de_la_principale() -> None:
    outcomes = []
    for point, over, under in [
        (0.5, 1.05, 9.0),
        (1.5, 1.22, 4.1),
        (2.5, 1.72, 2.05),
        (3.5, 2.9, 1.38),
        (4.5, 5.2, 1.14),
        (5.5, 11.0, 1.02),
    ]:
        outcomes += [Outcome("Over", over, point), Outcome("Under", under, point)]

    value = _lines(_event(markets={"totals": outcomes}))["O/U"]

    # La ligne principale est 2.5 (cotes les plus proches) : on garde 0.5 a 4.5.
    assert (
        value
        == "0.5: 1.05/9.00 | 1.5: 1.22/4.10 | 2.5: 1.72/2.05 | 3.5: 2.90/1.38 | 4.5: 5.20/1.14"
    )
    assert "5.5" not in value


def test_totals_et_alternate_totals_sont_fusionnes() -> None:
    event = _event(
        markets={
            "totals": [Outcome("Over", 1.72, 2.5), Outcome("Under", 2.05, 2.5)],
            "alternate_totals": [Outcome("Over", 1.22, 1.5), Outcome("Under", 4.1, 1.5)],
        }
    )

    assert _lines(event)["O/U"] == "1.5: 1.22/4.10 | 2.5: 1.72/2.05"


def test_btts() -> None:
    event = _event(markets={"btts": [Outcome("Yes", 1.6), Outcome("No", 2.25)]})

    assert _lines(event)["BTTS"] == "Oui 1.60 / Non 2.25"


def test_mi_temps_over_under() -> None:
    event = _event(
        markets={
            "totals_h1": [
                Outcome("Over", 1.32, 0.5),
                Outcome("Under", 3.2, 0.5),
                Outcome("Over", 2.55, 1.5),
                Outcome("Under", 1.48, 1.5),
            ]
        }
    )

    assert _lines(event)["MT O/U"] == "0.5: 1.32/3.20 | 1.5: 2.55/1.48"


def test_totaux_par_equipe() -> None:
    event = _event(
        markets={
            "team_totals": [
                Outcome("Over", 2.3, 1.5, "Hacken"),
                Outcome("Under", 1.58, 1.5, "Hacken"),
                Outcome("Over", 2.45, 1.5, "Djurgarden"),
                Outcome("Under", 1.52, 1.5, "Djurgarden"),
            ]
        }
    )

    assert _lines(event)["Eq. buts"] == "Hacken O1.5 2.30 | Djurgarden O1.5 2.45"


def test_score_exact_dix_cotes_les_plus_basses_triees() -> None:
    scores = [
        ("1-1", 6.5),
        ("2-1", 8.0),
        ("1-2", 8.5),
        ("1-0", 8.5),
        ("0-1", 9.5),
        ("2-2", 11.0),
        ("2-0", 11.0),
        ("0-0", 11.0),
        ("3-1", 15.0),
        ("1-3", 17.0),
        ("3-0", 21.0),
        ("4-0", 41.0),
    ]
    event = _event(markets={"correct_score": [Outcome(name, price) for name, price in scores]})
    rendered = render_event(event)

    ligne = [row for row in rendered.splitlines() if "Score exact" in row][0]
    assert ligne.startswith("  Score exact 1-1 6.50 | 2-1 8.00 | ")
    # 10 cotes retenues, donc les deux plus chers sont exclus.
    assert "21.00" not in rendered
    assert "41.00" not in rendered
    assert rendered.count("|") >= 8


def test_score_exact_passe_a_la_ligne_avec_alignement() -> None:
    scores = [(f"{i}-0", 5.0 + i) for i in range(9)]
    event = _event(markets={"correct_score": [Outcome(name, price) for name, price in scores]})

    rows = [row for row in render_event(event).splitlines() if "-0 " in row]
    assert len(rows) == 2
    assert rows[1].startswith(" " * 14), "la continuation est alignee sous la valeur"


def test_corners_ligne_principale_seule() -> None:
    event = _event(
        markets={
            "alternate_totals_corners": [
                Outcome("Over", 1.85, 9.5),
                Outcome("Under", 1.9, 9.5),
                Outcome("Over", 2.6, 10.5),
                Outcome("Under", 1.45, 10.5),
            ]
        }
    )

    assert _lines(event)["Corners"] == "O/U 9.5: 1.85/1.90"


def test_marche_non_modelise_est_rendu_plutot_que_perdu() -> None:
    # On a paye ce marche : le perdre silencieusement serait pire que l'afficher brut.
    event = _event(
        markets={
            "halftime_fulltime": [
                Outcome("Hacken/Hacken", 4.5),
                Outcome("Draw/Draw", 5.25),
            ]
        }
    )

    assert _lines(event)["MT/FT"] == "Hacken/Hacken 4.50 | Draw/Draw 5.25"


def test_marche_totalement_inconnu_est_conserve() -> None:
    event = _event(markets={"marche_exotique": [Outcome("Oui", 3.4)]})

    assert "Oui 3.40" in render_event(event)


# -- Regles d'omission ------------------------------------------------------


def test_aucune_ligne_vide_ni_na() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55), Outcome("Djurgarden", 2.6)]})
    rendered = render_event(event)

    assert "N/A" not in rendered
    assert "None" not in rendered
    for row in rendered.splitlines():
        assert row.strip(), "aucune ligne vide dans le bloc"


def test_marche_absent_est_omis() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55), Outcome("Djurgarden", 2.6)]})
    rendered = render_event(event)

    for absent in ("BTTS", "Score exact", "Corners", "O/U"):
        assert absent not in rendered


def test_evenement_sans_aucune_cote_ne_rend_que_l_entete() -> None:
    rendered = render_event(_event(markets={}))

    assert rendered.splitlines() == [
        "### M3 · FOOT · Allsvenskan · Hacken – Djurgarden · 03/08 17:30"
    ]


def test_bloc_contexte_omis_si_vide() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55)]})

    assert "CONTEXTE" not in render_event(event)


# -- Contexte et note -------------------------------------------------------


def test_note_perso() -> None:
    event = _event(
        markets={"h2h": [Outcome("Hacken", 2.55)]}, note="  Gardien titulaire incertain  "
    )
    rendered = render_event(event)

    assert "CONTEXTE" in rendered
    assert "  NOTE PERSO  Gardien titulaire incertain" in rendered


def test_note_vide_est_omise() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55)]}, note="   ")

    assert "NOTE PERSO" not in render_event(event)


def test_donnee_indisponible_est_explicite() -> None:
    event = _event(
        markets={"h2h": [Outcome("Hacken", 2.55)]},
        context_lines=[("Absents", "donnees non disponibles pour cette competition")],
    )

    assert "  Absents     donnees non disponibles pour cette competition" in render_event(event)


# -- En-tete des marches ----------------------------------------------------


def test_entete_marches_porte_le_bookmaker_et_l_heure() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55)]})

    assert "MARCHES (Betclic, releve 08:12)" in render_event(event)


def test_entete_marches_sans_heure_connue() -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55)]}, fetched_local=None)

    assert "MARCHES (Betclic)" in render_event(event)


# -- Formatage --------------------------------------------------------------


@pytest.mark.parametrize(
    ("value", "expected"),
    [(2.5, "2.50"), (11.0, "11.00"), (1.025, "1.02"), (100.0, "100.00")],
)
def test_cotes_a_deux_decimales(value: float, expected: str) -> None:
    event = _event(markets={"h2h": [Outcome("Hacken", value), Outcome("Djurgarden", 2.0)]})

    assert expected in render_event(event)


def test_estimation_de_tokens() -> None:
    assert estimate_tokens("a" * 360) == 100
    assert estimate_tokens("") == 0


def test_densite_du_bloc(load_fixture: object) -> None:
    """Un bloc complet doit rester de l'ordre de 300 tokens, pas 3 000."""
    outcomes = {
        "h2h": [Outcome("Hacken", 2.55), Outcome("Draw", 3.55), Outcome("Djurgarden", 2.6)],
        "double_chance": [
            Outcome("Hacken/Draw", 1.48),
            Outcome("Hacken/Djurgarden", 1.29),
            Outcome("Draw/Djurgarden", 1.5),
        ],
        "totals": [
            Outcome(name, price, point)
            for point, over, under in [(1.5, 1.22, 4.1), (2.5, 1.72, 2.05), (3.5, 2.9, 1.38)]
            for name, price in (("Over", over), ("Under", under))
        ],
        "btts": [Outcome("Yes", 1.6), Outcome("No", 2.25)],
        "correct_score": [Outcome(f"{i}-{j}", 6.5 + i + j) for i in range(4) for j in range(4)],
    }

    assert estimate_tokens(render_event(_event(markets=outcomes))) < 400
