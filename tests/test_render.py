from __future__ import annotations

from datetime import datetime
from zoneinfo import ZoneInfo

import pytest

from myassistantbet.services.render import (
    Outcome,
    RenderableEvent,
    estimate_tokens,
    ordered_labels,
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

    assert _lines(event)["Non servis"] == (
        "Hand. jeux, Set 1, Jeux S1 — aucun book interroge ne les sert sur cette competition"
    )


def test_un_marche_present_n_est_jamais_dit_non_servi() -> None:
    """Une saisie manuelle peut combler ce que l'API ne sert pas."""
    event = _tennis(
        markets={
            "h2h": [Outcome("Alcaraz", 1.4)],
            "spreads": [Outcome("Alcaraz", 1.85, -3.5)],
        },
        unserved=["spreads", "h2h_s1"],
    )

    assert _lines(event)["Non servis"] == (
        "Set 1 — aucun book interroge ne les sert sur cette competition"
    )


def test_sans_marche_abandonne_aucune_ligne_n_apparait() -> None:
    event = _tennis(markets={"h2h": [Outcome("Alcaraz", 1.4)]})

    assert "Non servis" not in render_event(event)


def test_un_evenement_sans_cote_dit_quand_meme_ce_qui_manque() -> None:
    """Sinon un bloc vide et un bloc jamais enrichi seraient indiscernables."""
    event = _tennis(unserved=["spreads"])
    rendered = render_event(event)

    assert "MARCHES" in rendered
    assert "Hand. jeux — aucun book interroge" in rendered


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


def test_un_marche_servi_en_reference_seulement_est_dit_non_jouable() -> None:
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

    assert "Non jouable Jeux O/U" in bloc
    assert "Vainqueur" in bloc and "Non jouable Vainqueur" not in bloc


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

    assert "Non jouable" not in render_event(event)


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

    assert "Non jouable" not in render_event(event)
