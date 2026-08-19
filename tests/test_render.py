from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from myassistantbet.config import get_settings
from myassistantbet.services.render import (
    ESTIMATED_MARK,
    Outcome,
    RenderableEvent,
    common_unplayable,
    estimate_tokens,
    ordered_labels,
    render_event,
    unserved_note,
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
    # Football sans issue « Draw » : le libelle 1N2 n'aurait plus de sens.
    event = _event(markets={"h2h": [Outcome("Hacken", 2.1), Outcome("Djurgarden", 1.8)]})

    assert _lines(event)["1-2"] == "2.10 / 1.80"


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


# -- Handicap au football ---------------------------------------------------

#: Trois rencontres reelles, avec leur 1N2 et l'echelle de handicap servie par
#: le book de substitution. Les signes sont ceux que la base porte **apres**
#: conversion a l'ingestion : chaque issue a le sien.
HANDICAPS_REELS = [
    (
        "Paris Saint Germain",
        "Aston Villa",
        [1.73, 3.90, 4.60],
        [(-0.5, 1.70, 2.12), (0.5, 1.21, 4.35), (-1.5, 2.87, 1.40)],
        "Paris Saint Germain -0.5 1.70 | Aston Villa +0.5 2.12",
    ),
    (
        "Tromso",
        "CFR 1907 Cluj",
        [1.49, 4.35, 5.75],
        [(-0.5, 1.46, 2.52), (-1.5, 2.27, 1.57), (0.5, 1.12, 5.25)],
        "Tromso -1.5 2.27 | CFR 1907 Cluj +1.5 1.57",
    ),
    (
        "Dinamo Minsk",
        "SC Braga",
        [7.20, 4.80, 1.37],
        [(-0.5, 6.50, 1.08), (0.5, 2.95, 1.35), (1.5, 1.72, 2.00)],
        "Dinamo Minsk +1.5 1.72 | SC Braga -1.5 2.00",
    ),
]


def _match_avec_handicaps(
    home: str,
    away: str,
    h2h: list[float],
    echelle: list[tuple[float, float, float]],
    inverser_l_exterieur: bool = False,
) -> RenderableEvent:
    """Un bloc football complet. `echelle` donne (handicap du domicile, son prix,
    le prix de l'exterieur sur la moitie opposee de ce palier)."""
    spreads = []
    for point, prix_domicile, prix_exterieur in echelle:
        spreads.append(Outcome(home, prix_domicile, point))
        # Le defaut d'origine : l'exterieur recopiait le handicap du domicile au
        # lieu de porter le sien.
        spreads.append(Outcome(away, prix_exterieur, point if inverser_l_exterieur else -point))
    return _event(
        home=home,
        away=away,
        markets={
            "h2h": [Outcome(home, h2h[0]), Outcome("Draw", h2h[1]), Outcome(away, h2h[2])],
            "spreads": spreads,
        },
    )


def _handicaps_rendus(ligne: str, home: str, away: str) -> dict[str, float]:
    """Relit les handicaps ecrits dans la ligne rendue, par equipe."""
    lus = {}
    for fragment in ligne.split(" | "):
        for equipe in (home, away):
            if fragment.startswith(equipe):
                lus[equipe] = float(fragment[len(equipe) :].split()[0])
    return lus


@pytest.mark.parametrize(("home", "away", "h2h", "echelle", "attendu"), HANDICAPS_REELS)
def test_le_handicap_rend_les_deux_moities_d_un_meme_palier(
    home: str, away: str, h2h: list[float], echelle: list[tuple[float, float, float]], attendu: str
) -> None:
    """Chaque camp choisissait sa ligne de son cote, la plus proche de 2.00 :
    rien ne garantissait que les deux moities affichees fussent les deux faces
    d'un meme pari. Elles sortent desormais du meme palier."""
    event = _match_avec_handicaps(home, away, h2h, echelle)

    assert _lines(event)["Handicap"] == attendu


@pytest.mark.parametrize(("home", "away", "h2h", "echelle", "_attendu"), HANDICAPS_REELS)
def test_les_deux_signes_d_un_handicap_sont_toujours_opposes(
    home: str,
    away: str,
    h2h: list[float],
    echelle: list[tuple[float, float, float]],
    _attendu: str,
) -> None:
    """L'invariant que le defaut violait : un handicap donne d'un cote est un
    handicap recu de l'autre. Il tient **par construction** depuis que les deux
    prix sortent du meme palier, et c'est cette propriete-la qu'on verifie —
    pas la valeur d'une ligne."""
    event = _match_avec_handicaps(home, away, h2h, echelle)

    lus = _handicaps_rendus(_lines(event)["Handicap"], home, away)

    assert len(lus) == 2, "les deux camps sont servis"
    assert lus[home] == -lus[away], f"{lus} : le meme signe des deux cotes"


def test_un_handicap_incoherent_avec_le_1n2_leve_une_alerte() -> None:
    """Aston Villa vainqueur valait 4.60 : « Aston Villa -0.5 2.12 » est le prix
    de sa double chance sous le libelle de sa victoire. Trois blocs sur trois
    portaient la faute et rien ne la disait — seul un recoupement a la main avec
    le 1N2 l'a rattrapee."""
    home, away, h2h, echelle, _ = HANDICAPS_REELS[0]
    event = _match_avec_handicaps(home, away, h2h, echelle, inverser_l_exterieur=True)

    lignes = _lines(event)

    assert "Alerte" in lignes, lignes
    assert "Aston Villa" in lignes["Alerte"]
    assert "Paris Saint Germain" not in lignes["Alerte"], "seul le camp fautif est nomme"


@pytest.mark.parametrize(("home", "away", "h2h", "echelle", "_attendu"), HANDICAPS_REELS)
def test_un_releve_sain_ne_leve_aucune_alerte(
    home: str,
    away: str,
    h2h: list[float],
    echelle: list[tuple[float, float, float]],
    _attendu: str,
) -> None:
    """Le controle doit se taire sur les trois memes rencontres une fois le
    signe corrige, sans quoi il crierait sur tout le board."""
    event = _match_avec_handicaps(home, away, h2h, echelle)

    assert "Alerte" not in _lines(event)


def test_l_alerte_se_tait_quand_les_deux_paris_se_confondent() -> None:
    """Cote favori extreme, « gagne » et « gagne ou fait nul » ont presque le
    meme prix : a 1.05 le nul ne vaut que trois points de probabilite, et
    l'ecart entre les deux hypotheses tombe sous le bruit qui separe deux books.

    Le prix rendu ici **est** celui de la double chance, et l'alerte se tait
    quand meme : la question a cesse d'etre lisible, et un silence vaut mieux
    qu'une accusation que la donnee ne porte pas. C'est la seule chose que
    `HANDICAP_ALERT_MARGIN` decide — le reste du controle n'a pas de seuil."""
    event = _event(
        home="Bayern",
        away="Bochum",
        markets={
            "h2h": [Outcome("Bayern", 1.05), Outcome("Draw", 15.0), Outcome("Bochum", 34.0)],
            "spreads": [Outcome("Bayern", 1.02, -0.5)],
        },
    )

    assert "Alerte" not in _lines(event)


def test_l_alerte_reste_lisible_sur_l_outsider_du_meme_match() -> None:
    """Le garde-fou ci-dessus ne doit pas eteindre le controle du **second**
    camp : cote outsider les deux paris restent tres separes — 0.03 contre 0.10
    de probabilite implicite — et c'est justement la que le defaut mesure se
    produisait."""
    event = _event(
        home="Bayern",
        away="Bochum",
        markets={
            "h2h": [Outcome("Bayern", 1.05), Outcome("Draw", 15.0), Outcome("Bochum", 34.0)],
            "spreads": [Outcome("Bochum", 14.0, -0.5)],
        },
    )

    assert "Bochum" in _lines(event)["Alerte"]


def test_l_alerte_ne_se_pose_pas_sans_1n2() -> None:
    """Le controle confronte deux marches : sans le second, il n'a pas de
    reference et ne doit rien affirmer."""
    event = _event(
        markets={
            "spreads": [
                Outcome("Hacken", 1.70, -0.5),
                Outcome("Djurgarden", 2.12, -0.5),
            ]
        }
    )

    assert "Alerte" not in _lines(event)


def test_un_palier_servi_d_un_seul_cote_reste_rendu() -> None:
    """Une moitie manquante n'est pas une raison de perdre l'autre : le prix est
    la, il porte son signe, et le camp absent se voit a ce qu'il manque."""
    event = _event(
        markets={"spreads": [Outcome("Hacken", 1.88, -1.5)]},
    )

    assert _lines(event)["Handicap"] == "Hacken -1.5 1.88"


def test_la_ligne_nulle_ne_porte_pas_de_signe() -> None:
    """`+0` ou `-0` inventerait une direction sur un pari qui n'en a pas : a
    handicap nul, la mise est rendue si le match est nul, des deux cotes."""
    event = _event(
        markets={
            "spreads": [Outcome("Hacken", 1.93, 0.0), Outcome("Djurgarden", 1.92, 0.0)],
        }
    )

    assert _lines(event)["Handicap"] == "Hacken 0 1.93 | Djurgarden 0 1.92"


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
    """L'heure seule laissait une soustraction a faire, donc personne ne la
    faisait : huit heures d'ecart sur une qualification obscure ne bougent rien,
    le meme ecart sur une affiche couvre l'annonce des compositions."""
    event = _event(markets={"h2h": [Outcome("Hacken", 2.55)]})

    assert (
        "MARCHES (Betclic, releve 08:12 — coup d'envoi dans 9h18, avant les compositions)"
        in render_event(event)
    )


def test_un_releve_frais_ne_compte_pas_le_temps() -> None:
    """Un releve de dix minutes n'a rien traverse, et l'ecrire ferait du bruit
    sur les blocs les plus frais."""
    event = _event(
        markets={"h2h": [Outcome("Hacken", 2.55)]},
        fetched_local=datetime(2026, 8, 3, 17, 22, tzinfo=PARIS),
    )

    assert "MARCHES (Betclic, releve 17:22)" in render_event(event)


def test_un_releve_posterieur_aux_compositions_ne_le_dit_pas() -> None:
    """C'est le seul moment nomme, parce que c'est le seul qui deplace les prix a
    heure connue. Un releve qui l'a deja traverse n'a rien a signaler."""
    event = _event(
        markets={"h2h": [Outcome("Hacken", 2.55)]},
        fetched_local=datetime(2026, 8, 3, 17, 0, tzinfo=PARIS),
    )

    entete = render_event(event).splitlines()[1]
    assert "coup d'envoi dans 0h30" in entete
    assert "avant les compositions" not in entete


def test_les_compositions_ne_sont_nommees_qu_au_football() -> None:
    """Au tennis il n'y a pas de onze a publier : la mention n'y decrirait
    rien."""
    event = _event(
        sport_key="tennis",
        home="Alcaraz",
        away="Sinner",
        markets={"h2h": [Outcome("Alcaraz", 1.4)]},
    )

    entete = render_event(event).splitlines()[1]
    assert "coup d'envoi dans 9h18" in entete
    assert "compositions" not in entete


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


# -- Bloc tennis ------------------------------------------------------------


def _tennis(**overrides: object) -> RenderableEvent:
    base = {
        "index": 2,
        "sport_key": "tennis",
        "competition": "US Open",
        "home": "Alcaraz",
        "away": "Sinner",
        "commence_local": datetime(2026, 8, 4, 19, 0, tzinfo=PARIS),
        "markets": {},
        "fetched_local": datetime(2026, 8, 4, 8, 12, tzinfo=PARIS),
    }
    base.update(overrides)
    return RenderableEvent(**base)  # type: ignore[arg-type]


def test_tennis_vainqueur_sans_nul() -> None:
    event = _tennis(markets={"h2h": [Outcome("Alcaraz", 1.4), Outcome("Sinner", 2.95)]})

    assert _lines(event)["Vainqueur"] == "1.40 / 2.95"


def test_tennis_jeux_over_under() -> None:
    event = _tennis(
        markets={
            "totals": [
                Outcome("Over", 1.9, 21.5),
                Outcome("Under", 1.9, 21.5),
                Outcome("Over", 2.05, 22.5),
                Outcome("Under", 1.8, 22.5),
            ]
        }
    )

    assert _lines(event)["Jeux O/U"] == "21.5: 1.90/1.90 | 22.5: 2.05/1.80"


def test_tennis_sets() -> None:
    event = _tennis(
        markets={
            "h2h_s1": [Outcome("Alcaraz", 1.45), Outcome("Sinner", 2.7)],
            "h2h_s2": [Outcome("Alcaraz", 1.5), Outcome("Sinner", 2.6)],
        }
    )
    lines = _lines(event)

    assert lines["Set 1"] == "1.45 / 2.70"
    assert lines["Set 2"] == "1.50 / 2.60"


def test_tennis_handicap_en_jeux() -> None:
    """Au tennis le handicap jeux est un continuum, comme un total : il se rend en
    echelle et non en une ligne par joueur. Le book en sert une dizaine, et la
    forme du football — la ligne la plus serree de chaque cote — en jetait neuf."""
    event = _tennis(
        markets={
            "spreads": [
                Outcome("Alcaraz", 1.85, -3.5),
                Outcome("Sinner", 1.95, 3.5),
            ]
        }
    )

    assert _lines(event)["Hand. jeux"] == "-3.5: 1.85/1.95"


def test_tennis_jeux_du_premier_set_fusionnes() -> None:
    event = _tennis(
        markets={
            "totals_s1": [Outcome("Over", 1.85, 9.5), Outcome("Under", 1.95, 9.5)],
            "alternate_totals_s1": [Outcome("Over", 2.6, 10.5), Outcome("Under", 1.45, 10.5)],
        }
    )

    assert _lines(event)["Jeux S1"] == "9.5: 1.85/1.95 | 10.5: 2.60/1.45"


def test_tennis_n_emprunte_pas_les_libelles_du_football() -> None:
    event = _tennis(markets={"h2h": [Outcome("Alcaraz", 1.4), Outcome("Sinner", 2.95)]})
    rendered = render_event(event)

    assert "1N2" not in rendered
    assert "1-2" not in rendered


def test_ordre_des_marches_tennis() -> None:
    event = _tennis(
        markets={
            "h2h_s1": [Outcome("Alcaraz", 1.45), Outcome("Sinner", 2.7)],
            "h2h": [Outcome("Alcaraz", 1.4), Outcome("Sinner", 2.95)],
            "totals": [Outcome("Over", 1.9, 21.5), Outcome("Under", 1.9, 21.5)],
        }
    )

    labels = [row[2:14].strip() for row in render_event(event).splitlines() if row.startswith("  ")]
    assert labels == ["Vainqueur", "Jeux O/U", "Set 1"]


# -- Provenance des cotes ---------------------------------------------------
#
# Un bloc annoncant « Betclic + Pinnacle (ref.) » laissait deviner quelle ligne
# etait jouable et laquelle ne faisait que situer le marche. La mention descend
# donc sur la ligne, la ou la selection se decide.


def test_une_ligne_du_book_principal_ne_porte_aucune_mention() -> None:
    event = _tennis(
        primary_book="betclic_fr",
        markets={
            "h2h": [
                Outcome("Alcaraz", 1.4, bookmaker="betclic_fr"),
                Outcome("Sinner", 2.95, bookmaker="betclic_fr"),
            ]
        },
    )

    assert _lines(event)["Vainqueur"] == "1.40 / 2.95"


def test_une_ligne_de_reference_nomme_sa_source() -> None:
    event = _tennis(
        primary_book="betclic_fr",
        markets={
            "h2h": [
                Outcome("Alcaraz", 1.4, bookmaker="betclic_fr"),
                Outcome("Sinner", 2.95, bookmaker="betclic_fr"),
            ],
            "spreads": [
                Outcome("Alcaraz", 1.85, -3.5, bookmaker="pinnacle"),
                Outcome("Sinner", 1.95, 3.5, bookmaker="pinnacle"),
            ],
        },
    )
    lignes = _lines(event)

    assert lignes["Vainqueur"] == "1.40 / 2.95"
    assert lignes["Hand. jeux"] == "-3.5: 1.85/1.95  [Pinnacle (ref.)]"


def test_une_ligne_fusionnee_dit_ce_qu_elle_melange() -> None:
    """Une variante « alternate » peut venir d'un autre book que sa base."""
    event = _tennis(
        primary_book="betclic_fr",
        markets={
            "totals_s1": [
                Outcome("Over", 1.85, 9.5, bookmaker="betclic_fr"),
                Outcome("Under", 1.95, 9.5, bookmaker="betclic_fr"),
            ],
            "alternate_totals_s1": [
                Outcome("Over", 2.6, 10.5, bookmaker="pinnacle"),
                Outcome("Under", 1.45, 10.5, bookmaker="pinnacle"),
            ],
        },
    )

    assert _lines(event)["Jeux S1"].endswith("[dont Pinnacle (ref.)]")


def test_une_saisie_manuelle_se_signale_comme_les_autres() -> None:
    event = _tennis(
        primary_book="betclic_fr",
        markets={
            "h2h": [Outcome("Alcaraz", 1.4, bookmaker="betclic_fr")],
            "outright": [Outcome("Oui", 2.15, bookmaker="manual")],
        },
    )

    assert _lines(event)["Cotes"] == "Oui 2.15  [saisie manuelle]"


def test_sans_book_principal_connu_aucune_mention_n_est_inventee() -> None:
    """Les blocs construits sans provenance restent rendus tels quels."""
    event = _tennis(markets={"spreads": [Outcome("Alcaraz", 1.85, -3.5, bookmaker="pinnacle")]})

    assert "[" not in render_event(event)


# -- Marches non servis -----------------------------------------------------


def test_les_marches_jamais_servis_deviennent_une_ligne() -> None:
    event = _tennis(
        markets={"h2h": [Outcome("Alcaraz", 1.4), Outcome("Sinner", 2.95)]},
        unserved=["totals_s1", "h2h_s1", "spreads"],
    )

    assert _lines(event)["Non servis"] == ("Hand. jeux, Set 1, Jeux S1 — " + unserved_note())


def test_un_marche_present_n_est_jamais_dit_non_servi() -> None:
    """Une saisie manuelle peut combler ce que l'API ne sert pas."""
    event = _tennis(
        markets={
            "h2h": [Outcome("Alcaraz", 1.4)],
            "spreads": [Outcome("Alcaraz", 1.85, -3.5)],
        },
        unserved=["spreads", "h2h_s1"],
    )

    assert _lines(event)["Non servis"] == ("Set 1 — " + unserved_note())


def test_sans_marche_abandonne_aucune_ligne_n_apparait() -> None:
    event = _tennis(markets={"h2h": [Outcome("Alcaraz", 1.4)]})

    assert "Non servis" not in render_event(event)


def test_un_evenement_sans_cote_dit_quand_meme_ce_qui_manque() -> None:
    """Sinon un bloc vide et un bloc jamais enrichi seraient indiscernables."""
    event = _tennis(unserved=["spreads"])
    rendered = render_event(event)

    assert "MARCHES" in rendered
    assert f"Hand. jeux — {unserved_note()}" in rendered


def test_un_marche_absent_de_ce_match_seul_ne_se_dit_pas_de_la_competition() -> None:
    """**La ligne mentait sur une de ses trois causes.**

    `_unserved_for` distingue ce que la competition ne sert pas de ce qui n'est
    pas revenu **sur ce match**, et les deux se rendaient sous la meme note —
    celle qui affirme la competition. Or c'est une ligne dont l'effet est de
    dire a l'analyste de ne pas chercher : l'affirmer au-dela de ce qui a ete
    observe est le pire endroit du bloc ou avoir tort.
    """
    event = _tennis(
        markets={"h2h": [Outcome("Alcaraz", 1.4)]},
        unserved=["h2h_s1"],
        unserved_here=["spreads"],
    )
    lignes = [ligne for ligne in render_event(event).splitlines() if "Non servis" in ligne]

    assert len(lignes) == 2, "une ligne par cause, jamais fondues"
    assert unserved_note() in lignes[0] and "Set 1" in lignes[0]
    assert "Hand. jeux" in lignes[1]
    assert "sur ce match" in lignes[1]
    assert "servis ailleurs dans la competition" in lignes[1]
    # Et surtout : la note de competition ne doit pas s'appliquer a ce constat.
    assert unserved_note() not in lignes[1]


def test_une_seule_cause_ne_rend_qu_une_ligne() -> None:
    """Le cas ordinaire ne se paie pas la distinction."""
    event = _tennis(markets={"h2h": [Outcome("Alcaraz", 1.4)]}, unserved_here=["spreads"])
    lignes = [ligne for ligne in render_event(event).splitlines() if "Non servis" in ligne]

    assert len(lignes) == 1
    assert "a verifier plutot qu'a ecarter" in lignes[0]


def test_le_handicap_jeux_est_signe_du_point_de_vue_du_premier_joueur() -> None:
    """Constate sur un prompt reel : regroupe sur la valeur absolue, le signe
    suivait le **favori**. « -2.5 » designait le second joueur quand il etait
    favori, le premier sinon — d'un bloc a l'autre, sans que rien le dise. Les
    prix restaient justes, mais une selection lue a l'envers est l'erreur la plus
    couteuse que ce bloc puisse produire."""
    # Le second joueur est le favori : c'est lui qui donne les jeux.
    event = _tennis(
        markets={
            "spreads": [
                Outcome("Bartunkova", 2.22, 2.5),
                Outcome("Anisimova", 1.71, -2.5),
            ]
        },
        home="Bartunkova",
        away="Anisimova",
    )

    assert _lines(event)["Hand. jeux"] == "+2.5: 2.22/1.71"


def test_le_handicap_jeux_garde_le_signe_quand_le_premier_joueur_est_favori() -> None:
    """L'autre moitie du meme piege : la forme ne doit pas changer selon qui est
    favori, sinon les deux blocs d'un meme lot ne se lisent pas pareil."""
    event = _tennis(
        markets={
            "spreads": [
                Outcome("Lehecka", 1.64, -1.5),
                Outcome("Blockx", 2.36, 1.5),
            ]
        },
        home="Lehecka",
        away="Blockx",
    )

    assert _lines(event)["Hand. jeux"] == "-1.5: 1.64/2.36"


def test_les_marches_tennis_demandes_ont_tous_un_libelle() -> None:
    """`alternate_totals` fuyait en cle brute dans la liste des marches demandes,
    en tete de prompt : il manquait a l'ordre d'affichage du tennis alors que
    `MERGED_MARKETS` en fait la cible de `totals`."""
    from myassistantbet.services.markets import TENNIS_MARKETS

    libelles = ordered_labels("tennis", TENNIS_MARKETS)

    assert not any("_" in libelle for libelle in libelles), libelles
    assert "Jeux O/U" in libelles
    assert "Hand. jeux" in libelles


def test_un_marche_servi_en_reference_seulement_est_dit_a_relever() -> None:
    """Troisieme etat, distinct de « absent » et de « jouable », et c'est celui
    qui decide de ce qu'on peut reellement parier.

    Mesure qui l'a fait naitre : sur 127 matchs de tennis a venir, `betclic_fr`
    ne sert **que** le `h2h`. Chaque ligne portait deja son `[Pinnacle (ref.)]`,
    mais il fallait les lire toutes pour voir qu'il ne restait rien a jouer hors
    du vainqueur — et une analyse reelle a bati deux angles sur les jeux avant de
    devoir se rabattre sur l'issue.
    """
    event = _event(sport_key="tennis")
    event.primary_book = "betclic_fr"
    event.markets = {
        "h2h": [
            Outcome(name=event.home, price=1.39, bookmaker="betclic_fr"),
            Outcome(name=event.away, price=2.90, bookmaker="betclic_fr"),
        ],
        "alternate_totals": [
            Outcome(name="Over", price=1.98, point=21.5, bookmaker="pinnacle"),
            Outcome(name="Under", price=1.91, point=21.5, bookmaker="pinnacle"),
        ],
    }

    bloc = render_event(event)

    assert "A relever   Jeux O/U" in bloc
    assert "Vainqueur" in bloc and "A relever   Vainqueur" not in bloc


def test_un_marche_jouable_ne_devient_pas_non_jouable_par_sa_variante() -> None:
    """`spreads` et `alternate_spreads` partagent une ligne et un libelle : si le
    book principal sert la ligne principale et Pinnacle l'echelle, le marche est
    jouable. Le declarer non jouable ferait chercher un prix affiche juste au
    dessus."""
    event = _event(sport_key="tennis")
    event.primary_book = "betclic_fr"
    event.markets = {
        "spreads": [Outcome(name=event.home, price=1.88, point=-2.5, bookmaker="betclic_fr")],
        "alternate_spreads": [
            Outcome(name=event.home, price=2.36, point=-4.5, bookmaker="pinnacle")
        ],
    }

    assert "A relever" not in render_event(event)


def test_un_book_de_substitution_ne_declare_rien_non_jouable() -> None:
    """Tous ses prix sont de reference par construction, le bloc le dit deja en
    entier, et repeter la liste de ses marches n'ajouterait rien."""
    event = _event(sport_key="tennis")
    event.primary_book = "betvictor"
    event.substitute = True
    event.markets = {
        "h2h": [Outcome(name=event.home, price=1.39, bookmaker="betvictor")],
        "alternate_totals": [
            Outcome(name="Over", price=1.98, point=21.5, bookmaker="pinnacle"),
            Outcome(name="Under", price=1.91, point=21.5, bookmaker="pinnacle"),
        ],
    }

    assert "A relever" not in render_event(event)


# -- Le report d'un horaire --------------------------------------------------


def _bloc_deplace(**champs: object) -> str:
    from datetime import UTC, datetime

    event = RenderableEvent(
        index=5,
        sport_key="tennis",
        competition="ATP Cincinnati Open",
        home="Alexander Shevchenko",
        away="Christopher O'Connell",
        commence_local=datetime(2026, 8, 12, 23, 0, tzinfo=UTC),
        markets={"h2h": [Outcome("Alexander Shevchenko", 1.99)]},
        **champs,  # type: ignore[arg-type]
    )
    return render_event(event)


def test_un_horaire_deplace_se_lit_sous_l_heure() -> None:
    """**Le fait dominant d'une soiree d'orages etait un report de cinq
    heures**, et l'en-tete ne portait que l'heure du moment : l'information a du
    etre retrouvee dans la presse alors que l'application avait les deux
    relevés.

    Elle se pose **sous l'heure**, parce que c'est l'heure qu'elle corrige."""
    from datetime import UTC, datetime

    bloc = _bloc_deplace(
        previous_local=datetime(2026, 8, 12, 17, 55, tzinfo=UTC),
        shifted_local=datetime(2026, 8, 12, 22, 14, tzinfo=UTC),
    )

    lignes = bloc.splitlines()
    # **L'heure est dans l'en-tete, la mention juste dessous** : c'est ce que ce
    # test garde. Elle n'est plus en fin de ligne depuis que le tennis porte
    # « (estimée) », et exiger la fin de ligne testerait la mise en forme plutot
    # que la regle.
    assert "· 12/08 23:00" in lignes[0]
    assert lignes[1] == "    (horaire deplace de +5h05, constate le 12/08 22:14)"


def test_un_horaire_stable_n_ajoute_aucune_ligne() -> None:
    """La mention est un signal, pas un decor : sans mouvement, rien."""
    assert "horaire deplace" not in _bloc_deplace()


# -- Le releve « A relever » commun au lot ----------------------------------


def _relevable(index: int, marches: dict[str, str]) -> RenderableEvent:
    """Un bloc dont chaque marche est servi par le seul book indique."""
    return RenderableEvent(
        index=index,
        sport_key="football",
        competition="Ligue 2",
        home=f"Club {index}",
        away=f"Adv {index}",
        commence_local=datetime(2026, 8, 14, 20, 45, tzinfo=PARIS),
        primary_book="betclic_fr",
        markets={
            key: [Outcome(name="Over", price=1.9, bookmaker=book)] for key, book in marches.items()
        },
    )


def test_le_releve_commun_se_derive_du_lot() -> None:
    """**Jamais code en dur.** « Handicap et O/U en référence » est vrai un jour
    parce que le book principal ne sert que le 1N2 sur ces compétitions-là ; ce
    n'est pas une propriété de l'application."""
    lot = [_relevable(i, {"h2h": "betclic_fr", "totals": "pinnacle"}) for i in range(1, 6)]

    assert common_unplayable(lot) == ["O/U"]


def test_sans_majorite_nette_la_liste_reste_plate() -> None:
    """Mesure du 14/08/2026 : sur un prompt, deux blocs sur six portaient la
    ligne. Une phrase de portée générale s'y lirait comme valant pour les six."""
    lot = [_relevable(i, {"h2h": "betclic_fr", "totals": "pinnacle"}) for i in range(1, 3)]
    lot += [_relevable(i, {"h2h": "betclic_fr", "totals": "betclic_fr"}) for i in range(3, 7)]

    assert common_unplayable(lot) == []


def test_sous_le_seuil_la_condensation_ne_paie_pas() -> None:
    """Remplacer n lignes identiques par une phrase en coûte deux : en dessous
    de quatre, la condensation coûte plus en lecture qu'elle ne gagne."""
    lot = [_relevable(i, {"h2h": "betclic_fr", "totals": "pinnacle"}) for i in range(1, 4)]

    assert common_unplayable(lot) == []
    assert common_unplayable(lot, minimum=3) == ["O/U"], "le seuil est un paramètre, pas une loi"


def test_un_bloc_qui_redit_le_releve_commun_se_tait() -> None:
    """Sur 28 blocs du 14/08, 24 portaient mot pour mot « Handicap, O/U »."""
    event = _relevable(1, {"h2h": "betclic_fr", "totals": "pinnacle"})

    assert "A relever" in render_event(event)
    assert "A relever" not in render_event(event, ["O/U"])


def test_un_bloc_qui_fait_exception_garde_sa_ligne() -> None:
    """C'est l'exception qu'il faut lire, et elle ne se voyait plus au milieu de
    vingt-quatre lignes identiques."""
    event = _relevable(1, {"h2h": "pinnacle", "totals": "pinnacle"})

    rendu = render_event(event, ["O/U"])

    assert "A relever" in rendu
    assert "1N2" in rendu.split("A relever")[1]


# -- Un constat d'absence porte son perimetre -------------------------------


def _books(monkeypatch: pytest.MonkeyPatch, valeur: str) -> None:
    """Change les books interroges, comme le ferait `.env`."""
    monkeypatch.setenv("REFERENCE_BOOKMAKERS", valeur)
    get_settings.cache_clear()


def test_le_constat_d_absence_enumere_les_books_interroges(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """**Exact au mot pres, et trompeur a la lecture.**

    « aucun book interroge ne les sert » se comprenait « ce marche n'existe pas
    sur cette competition ». Mesure du 14/08/2026 : `btts` sur la Ligue 2 est
    servi par 1xBet, William Hill et Matchbook — il existe, mais pas chez les
    trois books que nous interrogeons. Le constat doit donc porter son
    perimetre, nombre et noms.
    """
    _books(monkeypatch, "pinnacle,unibet_nl")

    note = unserved_note()

    assert "aucun des 3 books interroges" in note
    assert "Betclic" in note and "Pinnacle" in note and "Unibet NL" in note
    # Le suffixe « (ref.) » qualifie un prix affiche, pas un book interroge, et
    # il ferait ici une parenthese dans une parenthese.
    assert "(ref.)" not in note


def test_le_perimetre_suit_la_constante(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ajouter un book met la phrase a jour **sans edition** : le nombre et les
    noms se derivent de la meme source que celle qui decide des appels."""
    _books(monkeypatch, "")
    seul = unserved_note()

    _books(monkeypatch, "pinnacle,unibet_nl,onexbet")
    elargi = unserved_note()

    assert "aucun des 1 books interroges (Betclic)" in seul
    assert "aucun des 4 books interroges" in elargi
    assert "1xBet" in elargi, "un book ajoute apparait sans qu'on touche a la phrase"


def test_la_ligne_du_bloc_porte_le_perimetre(monkeypatch: pytest.MonkeyPatch) -> None:
    """Le generateur alimente la ligne rendue, et non une chaine isolee."""
    _books(monkeypatch, "pinnacle")
    event = _event(sport_key="tennis", markets={}, unserved=["spreads"])

    ligne = _lines(event)["Non servis"]

    assert ligne.endswith(unserved_note())
    assert "aucun des 2 books interroges (Betclic, Pinnacle)" in ligne


def test_le_constat_du_book_de_substitution_porte_deja_le_sien(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """L'autre cause de la meme ligne n'a rien a enumerer : « le book de
    substitution » **est** l'ensemble interroge, et il n'y en a qu'un."""
    _books(monkeypatch, "pinnacle")
    event = _event(sport_key="tennis", markets={}, unserved=["spreads"], substitute=True)

    ligne = _lines(event)["Non servis"]

    assert "book de substitution" in ligne
    assert "books interroges" not in ligne


def test_l_heure_d_un_bloc_tennis_est_annoncee_comme_estimee() -> None:
    """**Une heure au quart d'heure près se lit comme une heure ferme.**

    Au tennis elle ne l'est pas : un match qui suit trois autres sur le même
    court part quand il part. Le lot 11 a établi qu'aucune source accessible ne
    sert le court ni le rang dans le programme — l'heure est donc
    **invérifiable**, et deux blocs d'une session réelle du 16/08 étaient faux de
    deux à trois heures.

    La mention ne coûte rien et retire la fausse précision. Elle ne prétend pas
    corriger l'heure : rien ne le permet.
    """
    from datetime import UTC, datetime

    del UTC, datetime
    bloc = _bloc_deplace()

    assert bloc.splitlines()[0].endswith(ESTIMATED_MARK)


def test_le_football_ne_porte_aucune_mention_d_estimation() -> None:
    """**La différence est le point.** Un coup d'envoi de football est fixé à
    l'avance, et un report s'y dit déjà par sa propre ligne. Marquer les deux
    ferait de la mention un décor, et elle cesserait d'être lue."""
    from datetime import UTC, datetime

    event = RenderableEvent(
        index=1,
        sport_key="football",
        competition="Ligue 1",
        home="Lyon",
        away="Nice",
        commence_local=datetime(2026, 8, 12, 20, 45, tzinfo=UTC),
        markets={"h2h": [Outcome("Lyon", 1.99)]},
    )

    assert ESTIMATED_MARK not in render_event(event).splitlines()[0]
