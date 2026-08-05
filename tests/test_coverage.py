"""Memoire des marches qu'une competition sert reellement.

The Odds API reserve ses marches additionnels a certains sports et bookmakers :
sans memoire, l'etage B repaie le meme constat vide a chaque session.
"""

from __future__ import annotations

from typing import Any

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import coverage

from .helpers import NOW


def _competition(settings: Settings, key: str = "tennis_atp_us_open") -> int:
    row = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (key,), settings=settings
    )
    return int(row["id"])


def _payload(*markets: str) -> dict[str, Any]:
    return {
        "bookmakers": [
            {
                "key": "betclic_fr",
                "markets": [{"key": market, "outcomes": []} for market in markets],
            }
        ]
    }


def test_les_marches_servis_sont_lus_dans_la_reponse() -> None:
    assert coverage.markets_in(_payload("h2h", "totals")) == {"h2h", "totals"}
    assert coverage.markets_in({}) == set()
    assert coverage.markets_in({"bookmakers": [{"key": "x", "markets": []}]}) == set()


def test_un_marche_jamais_servi_finit_par_ne_plus_etre_demande(migrated: Settings) -> None:
    competition = _competition(migrated)
    demandes = ("h2h", "spreads", "totals_s1")

    # Un seul constat ne suffit pas : un match peut ne pas proposer un marche
    # que la competition sert habituellement.
    coverage.record(competition, demandes, _payload("h2h"), migrated)
    assert coverage.useful(competition, demandes, migrated) == demandes

    coverage.record(competition, demandes, _payload("h2h"), migrated)
    assert coverage.useful(competition, demandes, migrated) == ()


def test_un_marche_servi_une_fois_reste_demande(migrated: Settings) -> None:
    """Absent d'un match, present sur un autre : on continue de le demander."""
    competition = _competition(migrated)
    demandes = ("h2h", "spreads")

    coverage.record(competition, demandes, _payload("h2h"), migrated)
    coverage.record(competition, demandes, _payload("h2h", "spreads"), migrated)
    coverage.record(competition, demandes, _payload("h2h"), migrated)

    assert coverage.useful(competition, demandes, migrated) == demandes


def test_seul_le_marche_d_ancrage_ne_vaut_pas_un_appel(migrated: Settings) -> None:
    """L'etage A possede deja le h2h : le redemander seul serait payer pour rien."""
    competition = _competition(migrated)
    demandes = ("h2h", "spreads", "totals")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, demandes, _payload("h2h"), migrated)

    assert coverage.useful(competition, demandes, migrated) == ()


def test_les_marches_encore_utiles_sont_conserves(migrated: Settings) -> None:
    competition = _competition(migrated)
    demandes = ("h2h", "spreads", "totals_s1")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, demandes, _payload("h2h", "spreads"), migrated)

    assert coverage.useful(competition, demandes, migrated) == ("h2h", "spreads")


def test_sans_historique_tout_est_demande(migrated: Settings) -> None:
    competition = _competition(migrated)

    assert coverage.useful(competition, ("h2h", "spreads"), migrated) == ("h2h", "spreads")


def test_un_evenement_sans_competition_n_est_pas_memorise(migrated: Settings) -> None:
    """Un evenement manuel n'a pas de competition d'API : rien a apprendre."""
    coverage.record(None, ("h2h",), _payload(), migrated)

    assert db.query("SELECT * FROM market_coverage", settings=migrated) == []
    assert coverage.useful(None, ("h2h", "spreads"), migrated) == ("h2h", "spreads")


def test_le_compteur_de_verifications_s_incremente(migrated: Settings) -> None:
    competition = _competition(migrated)
    for _ in range(3):
        coverage.record(competition, ("spreads",), _payload("h2h"), migrated)

    row = db.query_one(
        "SELECT served, checks FROM market_coverage WHERE market_key = 'spreads'",
        settings=migrated,
    )
    assert (row["served"], row["checks"]) == (0, 3)


def test_deux_competitions_apprennent_separement(migrated: Settings) -> None:
    atp = _competition(migrated, "tennis_atp_us_open")
    epl = _competition(migrated, "soccer_epl")
    demandes = ("h2h", "spreads")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(atp, demandes, _payload("h2h"), migrated)

    assert coverage.useful(atp, demandes, migrated) == ()
    assert coverage.useful(epl, demandes, migrated) == demandes


def test_le_resume_liste_les_competitions_testees(migrated: Settings) -> None:
    competition = _competition(migrated)
    coverage.record(competition, ("h2h", "spreads"), _payload("h2h"), migrated)

    lignes = coverage.summary(migrated)

    assert len(lignes) == 1
    assert lignes[0]["servis"] == 1
    assert lignes[0]["testes"] == 2


@pytest.mark.parametrize("payload", [{}, {"bookmakers": []}, {"bookmakers": [{}]}])
def test_une_reponse_vide_ne_casse_rien(migrated: Settings, payload: dict[str, Any]) -> None:
    competition = _competition(migrated)

    coverage.record(competition, ("h2h",), payload, migrated)

    row = db.query_one("SELECT served FROM market_coverage", settings=migrated)
    assert row["served"] == 0


def test_un_evenement_sans_marche_servi_est_ecarte_du_cout(migrated: Settings) -> None:
    """Il est couvert par l'API : le motif differe d'un evenement manuel."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.enrich import TENNIS_MARKETS, build_estimate

    competition = _competition(migrated)
    db.execute("UPDATE competitions SET active = 1 WHERE id = ?", (competition,), settings=migrated)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) "
        "VALUES (?, ?, 'abc123', 'Moutet', 'Bergs', '2026-08-04T15:00:00Z', 'oddsapi', ?)",
        (sport["id"], competition, db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)
    session_id = board_service.toggle_selection(int(event["id"]), True, migrated)

    avant = build_estimate(session_id, migrated, NOW)
    assert avant.events == 1 and avant.cost > 0

    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, TENNIS_MARKETS, _payload("h2h"), migrated)

    apres = build_estimate(session_id, migrated, NOW)
    assert apres.events == 0
    assert apres.cost == 0
    assert apres.barren == ["Moutet – Bergs"]
    assert apres.skipped == [], "le motif n'est pas « evenement manuel »"


def test_le_motif_de_blocage_ne_ment_pas(migrated: Settings) -> None:
    """Dire « rien de coche » alors que des matchs le sont egare le diagnostic."""
    from myassistantbet.services import board as board_service
    from myassistantbet.services.enrich import TENNIS_MARKETS, build_estimate

    competition = _competition(migrated)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) "
        "VALUES (?, ?, 'abc', 'Moutet', 'Bergs', '2026-08-04T15:00:00Z', 'oddsapi', ?)",
        (sport["id"], competition, db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)

    vide = build_estimate(board_service.current_session(migrated), migrated, NOW)
    assert vide.blocked_reason == "Aucun evenement selectionne."

    session_id = board_service.toggle_selection(int(event["id"]), True, migrated)
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, TENNIS_MARKETS, _payload("h2h"), migrated)

    plein = build_estimate(session_id, migrated, NOW)
    assert plein.considered == 1
    assert plein.events == 0
    assert "Aucun evenement selectionne" not in (plein.blocked_reason or "")
    assert "aucun marche profond" in (plein.blocked_reason or "")


def test_le_retest_efface_ce_qui_a_ete_appris(migrated: Settings) -> None:
    """L'apprentissage ne doit pas etre une porte a sens unique."""
    competition = _competition(migrated)
    demandes = ("h2h", "spreads")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, demandes, _payload("h2h"), migrated)
    assert coverage.useful(competition, demandes, migrated) == ()

    efface = coverage.reset(competition, migrated)

    assert efface == 2
    assert coverage.useful(competition, demandes, migrated) == demandes


def test_le_retest_d_une_competition_epargne_les_autres(migrated: Settings) -> None:
    atp = _competition(migrated, "tennis_atp_us_open")
    epl = _competition(migrated, "soccer_epl")
    for competition in (atp, epl):
        for _ in range(coverage.GIVE_UP_AFTER):
            coverage.record(competition, ("h2h", "spreads"), _payload("h2h"), migrated)

    coverage.reset(atp, migrated)

    assert coverage.useful(atp, ("h2h", "spreads"), migrated) == ("h2h", "spreads")
    assert coverage.useful(epl, ("h2h", "spreads"), migrated) == ()


# -- Les books font partie du constat ---------------------------------------
#
# Le 4 aout 2026, le WTA Canadian Open a ete constate sans handicap jeux ni
# total jeux alors que seul Betclic etait interroge. Les books de reference
# ajoutes le meme soir n'ont jamais eu l'occasion de repondre : la competition
# etait deja condamnee, et ses blocs n'ont plus porte que le vainqueur.


def test_l_empreinte_d_un_ensemble_de_books_ne_depend_pas_de_l_ordre() -> None:
    assert coverage.books_key(("pinnacle", "betclic_fr")) == coverage.books_key(
        ("betclic_fr", "pinnacle")
    )


def test_un_constat_fait_sans_les_books_de_reference_ne_condamne_rien(
    migrated: Settings,
) -> None:
    competition = _competition(migrated)
    demandes = ("h2h", "spreads", "totals")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, demandes, _payload("h2h"), migrated, ("betclic_fr",))

    assert coverage.useful(competition, demandes, migrated, ("betclic_fr",)) == ()
    elargi = ("betclic_fr", "pinnacle")
    assert coverage.useful(competition, demandes, migrated, elargi) == demandes


def test_un_constat_fait_avec_plus_de_books_vaut_pour_les_ensembles_plus_etroits(
    migrated: Settings,
) -> None:
    """Si dix books ne servent pas un marche, aucun sous-ensemble ne le sert."""
    competition = _competition(migrated)
    demandes = ("h2h", "spreads")
    large = ("betclic_fr", "pinnacle")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(competition, demandes, _payload("h2h"), migrated, large)

    assert coverage.useful(competition, demandes, migrated, ("betclic_fr",)) == ()


def test_chaque_ensemble_de_books_compte_pour_lui_meme(migrated: Settings) -> None:
    """Un constat par ensemble : additionner les deux conclurait trop vite."""
    competition = _competition(migrated)
    demandes = ("h2h", "spreads")
    coverage.record(competition, demandes, _payload("h2h"), migrated, ("betclic_fr",))
    coverage.record(competition, demandes, _payload("h2h"), migrated, ("betclic_fr", "pinnacle"))

    assert coverage.useful(competition, demandes, migrated, ("betclic_fr", "pinnacle")) == demandes


def test_le_resume_ne_compte_pas_deux_fois_le_meme_marche(migrated: Settings) -> None:
    competition = _competition(migrated)
    coverage.record(competition, ("h2h", "spreads"), _payload("h2h"), migrated, ("betclic_fr",))
    coverage.record(
        competition, ("h2h", "spreads"), _payload("h2h", "spreads"), migrated, ("pinnacle",)
    )

    lignes = coverage.summary(migrated)

    assert lignes[0]["testes"] == 2, "deux marches testes, sous deux ensembles de books"
    assert lignes[0]["servis"] == 2, "servi par un ensemble suffit"
    assert coverage.by_competition(migrated)[competition] == {"servis": 2, "testes": 2}


def test_les_marches_abandonnes_sont_lus_en_une_requete(migrated: Settings) -> None:
    atp = _competition(migrated, "tennis_atp_us_open")
    wta = _competition(migrated, "tennis_wta_us_open")
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(atp, ("h2h", "spreads"), _payload("h2h"), migrated)
        coverage.record(wta, ("h2h", "totals"), _payload("h2h", "totals"), migrated)

    dead = coverage.barren_by_competition([atp, wta], migrated)

    assert dead == {atp: {"spreads"}}
