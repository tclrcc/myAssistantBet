"""Le règlement automatique : ce qu'il règle, et surtout ce qu'il refuse.

**Le risque n'est pas de mal régler, c'est de régler en silence.** 293
sélections tranchées portent tout ce que ce projet sait produire — le résidu au
prix, les crans, les intervalles. Un règlement erroné les corromprait sans que
rien ne le dise.

Ces tests portent donc autant sur les cas **non couverts** que sur les cas
réglés : un marché hors règle, un abandon, un camp ambigu doivent produire
`None`, jamais un verdict par défaut.
"""

from __future__ import annotations

import pytest

from myassistantbet.services import settlement as S

# -- Le score, et la convention établie par recoupement ----------------------


@pytest.mark.parametrize(
    ("score", "sets", "incomplet"),
    [
        ("6-4,6-3", (2, 0), False),
        ("4-6,7-6,0-6", (1, 2), False),
        ("7-5,3-6,2-1", (1, 1), True),  # abandon : dernier set inachevé
        ("6-1,1-0", (1, 0), True),  # abandon dès le second set
        ("7-6,6-7,7-6", (2, 1), False),  # trois tie-breaks, tous complets
        ("8-6", (1, 0), False),  # set long d'un cinquième set
    ],
)
def test_le_score_se_lit_set_par_set_et_l_abandon_se_voit(
    score: str, sets: tuple[int, int], incomplet: bool
) -> None:
    """**L'abandon se détecte sur le score seul**, sans champ supplémentaire.

    Un set inachevé — `2-1` — n'est pas un set, c'est l'instant où le jeu s'est
    arrêté. Compter les sets y désigne celui qui menait, donc le perdant :
    mesuré sur les 2 cas d'un recoupement de 800 matchs, et les deux
    ressortaient à l'envers.
    """
    lu = S.read_score(score)
    assert lu is not None
    assert (lu.sets_un, lu.sets_deux) == sets
    assert lu.incomplet is incomplet
    assert lu.decisif is (not incomplet and sets[0] != sets[1])


def test_un_score_illisible_ne_rend_rien() -> None:
    assert S.read_score("") is None
    assert S.read_score("abandon") is None


# -- Le camp désigné ---------------------------------------------------------


def test_un_jeton_partage_par_les_deux_camps_ne_designe_personne() -> None:
    """**Mesuré sur `Los Angeles FC – San Diego FC`** : le `fc` touchait les deux
    côtés, la sélection partait « hors règle » et un règlement parfaitement
    lisible était perdu.

    Ce n'est pas un assouplissement de la règle du doute : un jeton présent des
    deux côtés ne porte aucune information sur le camp visé, donc il ne doit pas
    en fabriquer.
    """
    assert S._camp("Los Angeles FC", "Los Angeles FC", "San Diego FC") == "home"
    assert S._camp("San Diego FC", "Los Angeles FC", "San Diego FC") == "away"


def test_en_cas_de_doute_reel_aucun_camp() -> None:
    """Une sélection qui ne distingue rien ne se règle pas."""
    assert S._camp("FC", "Los Angeles FC", "San Diego FC") is None
    assert S._camp("", "Lyon", "Nice") is None
    assert S._camp("Marseille", "Lyon", "Nice") is None


def test_le_nul_se_reconnait() -> None:
    assert S._camp("Nul", "Lyon", "Nice") == "draw"


# -- Les règles --------------------------------------------------------------


def _foot(domicile: int, exterieur: int) -> S.MatchResult:
    gagnant = "home" if domicile > exterieur else ("away" if exterieur > domicile else "draw")
    return S.MatchResult(
        sport="football",
        source=S.SRC_FOOT,
        observed_at="2026-08-20T00:00:00Z",
        detail=f"{domicile}-{exterieur}",
        winner=gagnant,
        goals=(domicile, exterieur),
    )


@pytest.mark.parametrize(
    ("selection", "attendu"),
    [("Lyon", S.WIN), ("Nice", S.LOSS), ("Nul", S.LOSS)],
)
def test_le_1n2_se_regle(selection: str, attendu: str) -> None:
    assert S.settle("h2h", "1N2", selection, "Lyon", "Nice", _foot(2, 1)) == attendu


@pytest.mark.parametrize(
    ("selection", "buts", "attendu"),
    [
        ("Over 2.5", (2, 1), S.WIN),
        ("Under 2.5", (2, 1), S.LOSS),
        ("Over 3.5", (2, 1), S.LOSS),
        ("Under 3.5", (2, 1), S.WIN),
        ("Over 3", (2, 1), S.VOID),  # ligne entière touchée : remboursée
    ],
)
def test_l_over_under_se_regle_et_rembourse_la_ligne_entiere(
    selection: str, buts: tuple[int, int], attendu: str
) -> None:
    assert S.settle("totals", "O/U", selection, "Lyon", "Nice", _foot(*buts)) == attendu


def test_un_over_under_sans_sens_ni_ligne_ne_se_regle_pas() -> None:
    """En cas de doute, rien — jamais un verdict par défaut."""
    assert S.settle("totals", "O/U", "2.5", "Lyon", "Nice", _foot(2, 1)) is None
    assert S.settle("totals", "O/U", "Over", "Lyon", "Nice", _foot(2, 1)) is None


@pytest.mark.parametrize(
    "cle", ["alternate_spreads", "btts", "double_chance", "correct_score", "to_qualify"]
)
def test_un_marche_hors_regle_ne_se_regle_jamais(cle: str) -> None:
    """**Un marché dont la règle n'est pas écrite ne produit aucune ligne.**

    Il n'est pas rangé dans un état « inconnu » — il est absent, et c'est ce qui
    le distingue d'un marché couvert dont le résultat manque.
    """
    assert S.settle(cle, "peu importe", "Lyon", "Lyon", "Nice", _foot(2, 1)) is None
    assert S.enabled_for(cle, "peu importe") is False


def test_un_match_inacheve_ne_se_regle_pas() -> None:
    """Un abandon désigne celui qui menait. **Aucune règle ne s'y applique.**"""
    abandon = S.MatchResult(
        sport="tennis",
        source=S.SRC_TENNIS,
        observed_at="2026-08-20T00:00:00Z",
        detail="6-1,1-0",
        winner="home",
        sets=(1, 0),
        unfinished=True,
    )
    assert S.settle("h2h", "Vainqueur", "Sinner", "Sinner", "Alcaraz", abandon) is None


def test_les_familles_en_service_sont_celles_mesurees_a_cent_pour_cent() -> None:
    """Le seuil de mise en service est **100 %**, pas « assez bon ».

    2 % de règlements faux sur 293 sélections corrompent le résidu au prix, qui
    est la seule mesure que ce projet sache produire.
    """
    assert S.ENABLED == ("issue", "total")
    assert S.enabled_for("h2h", "1N2") is True
    assert S.enabled_for("", "Vainqueur") is True
    assert S.enabled_for("", "O/U 2.5") is True


# -- Les états, et le refus d'écraser ----------------------------------------
#
# **C'est la leçon du lot 14 transposée.** `set_open_dossiers` écrasait un bon
# état par un mauvais sans laisser de trace, et il a fallu un rejeu pour s'en
# apercevoir. Ici un règlement automatique qui contredit un règlement manuel se
# voit, et c'est tout ce qu'il fait.

from myassistantbet.config import Settings  # noqa: E402
from myassistantbet.services import board as board_service  # noqa: E402
from myassistantbet.services.history import add_pick, list_picks, set_result  # noqa: E402
from myassistantbet.services.manual import build, save  # noqa: E402


def _match(settings: Settings, home: str, away: str, quand: str = "2026-08-19") -> int:
    return save(
        build(
            "football",
            "Amical",
            home,
            away,
            quand,
            "20:45",
            f"{home} 1.45",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _pick(settings: Settings, event_id: int, marche: str, selection: str) -> tuple[int, int]:
    session_id = board_service.toggle_selection(event_id, True, settings)
    pick_id = add_pick(
        session_id,
        tier="safe",
        market=marche,
        selection=selection,
        event_id=str(event_id),
        price="1.45",
        settings=settings,
    )
    return session_id, pick_id


def _resultat_foot(
    settings: Settings, event_id: int, domicile: int, exterieur: int, jour: str = "2026-08-19"
) -> None:
    """Pose un résultat là où le module va le chercher : le résumé de saison."""
    import json

    from myassistantbet.db import connect, utcnow

    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO context (event_id, kind, payload_json, fetched_at) VALUES (?,?,?,?)",
            (event_id, "teams", json.dumps({"home": 7001, "away": 7002}), utcnow()),
        )
        conn.execute(
            "INSERT INTO team_context (team_id, kind, scope, payload_json, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (
                7001,
                "season",
                "2026",
                json.dumps(
                    [
                        {
                            "date": f"{jour}T20:45:00+00:00",
                            "status": "FT",
                            "goals": [domicile, exterieur],
                            "at_home": True,
                        }
                    ]
                ),
                utcnow(),
            ),
        )


def test_un_reglement_automatique_n_ecrase_jamais_un_reglement_manuel(
    migrated: Settings,
) -> None:
    """**Le garde-fou central de ce module.**

    Une sélection déjà tranchée à la main que le calcul contredit ressort en
    `divergent`, et `picks.result` **ne bouge pas**. Un signal qui écrase n'est
    pas un signal, c'est une perte.
    """
    event_id = _match(migrated, "Lyon", "Nice")
    _, pick_id = _pick(migrated, event_id, "1N2", "Lyon")
    _resultat_foot(migrated, event_id, 2, 1)  # Lyon gagne : le calcul dira `win`
    set_result(pick_id, "loss", migrated)  # ... mais la main dit `loss`

    passe = S.run(migrated)

    divergents = passe.divergents
    assert len(divergents) == 1, "une contradiction doit se voir"
    assert divergents[0].verdict == S.WIN
    assert divergents[0].manuel == S.LOSS
    # Et le résultat manuel est intact.
    assert [p.result for p in list_picks(_pick_session(migrated, pick_id), migrated)] == ["loss"]


def _pick_session(settings: Settings, pick_id: int) -> int:
    from myassistantbet.db import connect

    with connect(settings) as conn:
        return int(
            conn.execute("SELECT session_id FROM picks WHERE id = ?", (pick_id,)).fetchone()[0]
        )


def test_une_divergence_ne_se_promeut_pas(migrated: Settings) -> None:
    """Promouvoir contre un règlement déjà posé serait l'écrasement même que ce
    module existe pour empêcher. Il faut d'abord trancher à la main."""
    event_id = _match(migrated, "Lyon", "Nice")
    _, pick_id = _pick(migrated, event_id, "1N2", "Lyon")
    _resultat_foot(migrated, event_id, 2, 1)
    set_result(pick_id, "loss", migrated)
    S.run(migrated)

    assert S.apply(pick_id, migrated) is False
    assert S.pending(migrated)[0].etat == S.DIVERGENT


def test_une_proposition_se_promeut_et_ne_se_retrograde_pas(migrated: Settings) -> None:
    """La promotion est un **geste humain**, et le calcul ne la défait pas :
    rejouer la passe ne doit pas ramener une ligne promue à `propose`."""
    event_id = _match(migrated, "Lyon", "Nice")
    _, pick_id = _pick(migrated, event_id, "1N2", "Lyon")
    _resultat_foot(migrated, event_id, 2, 1)

    passe = S.run(migrated)
    assert [p.etat for p in passe.proposals] == [S.PROPOSE]

    assert S.apply(pick_id, migrated) is True
    with __import__("myassistantbet.db", fromlist=["connect"]).connect(migrated) as conn:
        assert (
            conn.execute("SELECT result FROM picks WHERE id=?", (pick_id,)).fetchone()[0] == "win"
        )

    S.run(migrated)  # rejeu
    with __import__("myassistantbet.db", fromlist=["connect"]).connect(migrated) as conn:
        etat = conn.execute("SELECT etat FROM reglements WHERE pick_id=?", (pick_id,)).fetchone()[0]
    assert etat == S.APPLIQUE, "une promotion humaine ne se défait pas au rejeu"


def test_un_marche_hors_service_ne_produit_aucune_ligne(migrated: Settings) -> None:
    """Il n'est pas rangé « inconnu », il est **absent** — et c'est ce qui le
    distingue d'un marché couvert dont le résultat manque."""
    event_id = _match(migrated, "Lyon", "Nice")
    _, pick_id = _pick(migrated, event_id, "Handicap", "Lyon -1")
    _resultat_foot(migrated, event_id, 2, 1)

    passe = S.run(migrated)

    assert passe.proposals == []
    assert passe.hors_regle == 1
    assert S.pending(migrated) == []


def test_le_journal_porte_sa_source_et_son_horodatage(migrated: Settings) -> None:
    """Sans eux, une divergence ne se distingue pas d'une source qui a changé
    d'avis, et le taux d'accord cesse d'être mesurable dans le temps."""
    from myassistantbet.db import connect

    event_id = _match(migrated, "Lyon", "Nice")
    _, pick_id = _pick(migrated, event_id, "1N2", "Lyon")
    _resultat_foot(migrated, event_id, 2, 1)
    S.run(migrated)

    with connect(migrated) as conn:
        row = conn.execute(
            "SELECT source, observed_at, detail FROM reglements WHERE pick_id=?", (pick_id,)
        ).fetchone()
    assert row["source"] == S.SRC_FOOT
    assert row["observed_at"]
    assert row["detail"] == "2-1"


def test_le_parcours_reel_promeut_et_refuse_d_ecraser(migrated: Settings) -> None:
    """**Le service et sa surface se livrent ensemble.**

    `add_pick` a accepté pendant deux jours un motif de saisie tardive que ni le
    formulaire ni la route ne transmettaient : les tests du service passaient, et
    la garde était absolue sur le seul chemin qu'elle devait laisser ouvert. Ce
    test poste donc le formulaire et **relit la base**.
    """
    from fastapi.testclient import TestClient

    from myassistantbet.db import connect
    from myassistantbet.main import app

    with TestClient(app) as client:
        # Une proposition ordinaire se promeut.
        gagnant = _match(migrated, "Lyon", "Nice")
        session_id, pick_ok = _pick(migrated, gagnant, "1N2", "Lyon")
        _resultat_foot(migrated, gagnant, 2, 1)
        S.run(migrated)

        page = client.get(f"/history/{session_id}").text
        assert "proposé : win" in page, "la proposition doit se voir avant d'être promue"

        reponse = client.post(f"/picks/{pick_ok}/settle")
        assert reponse.status_code == 200
        with connect(migrated) as conn:
            assert (
                conn.execute("SELECT result FROM picks WHERE id=?", (pick_ok,)).fetchone()[0]
                == "win"
            )

        # Une divergence ne se promeut pas, et le résultat manuel reste intact.
        autre = _match(migrated, "Reims", "Brest")
        _, pick_div = _pick(migrated, autre, "1N2", "Reims")
        _resultat_foot_second(migrated, autre, 0, 3)
        set_result(pick_div, "win", migrated)
        S.run(migrated)

        page = client.get(f"/history/{_pick_session(migrated, pick_div)}").text
        assert "divergence" in page, "une contradiction doit se voir"

        client.post(f"/picks/{pick_div}/settle")
        with connect(migrated) as conn:
            assert (
                conn.execute("SELECT result FROM picks WHERE id=?", (pick_div,)).fetchone()[0]
                == "win"
            ), "un règlement automatique ne doit jamais écraser une saisie à la main"


def _resultat_foot_second(
    settings: Settings, event_id: int, domicile: int, exterieur: int, jour: str = "2026-08-19"
) -> None:
    """Comme `_resultat_foot`, sur une seconde paire d'identifiants d'équipe."""
    import json

    from myassistantbet.db import connect, utcnow

    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO context (event_id, kind, payload_json, fetched_at) VALUES (?,?,?,?)",
            (event_id, "teams", json.dumps({"home": 7101, "away": 7102}), utcnow()),
        )
        conn.execute(
            "INSERT INTO team_context (team_id, kind, scope, payload_json, fetched_at) "
            "VALUES (?,?,?,?,?)",
            (
                7101,
                "season",
                "2026",
                json.dumps(
                    [
                        {
                            "date": f"{jour}T20:45:00+00:00",
                            "status": "FT",
                            "goals": [domicile, exterieur],
                            "at_home": True,
                        }
                    ]
                ),
                utcnow(),
            ),
        )
