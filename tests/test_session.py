"""Un match qui a commence quitte le prompt, mais pas la session.

Le board ne montre que la fenetre a venir : une fois l'heure passee, un match
coche disparait de l'ecran ou il aurait pu etre decoche. Sans regle explicite,
il continuerait a etre compte, enrichi et analyse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coverage
from myassistantbet.services import session as session_service
from myassistantbet.services.prompt import build_prompt

from .helpers import NOW

#: NOW vaut 10h00 UTC : ces deux bornes encadrent l'instant de reference.
COMMENCE_PASSE = "2026-08-03T08:00:00Z"
COMMENCE_A_VENIR = "2026-08-03T18:00:00Z"


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, home: str, away: str, commence: str) -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, ?, ?, ?, 'oddsapi', ?)",
        (sport["id"], home, away, commence, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _selection(settings: Settings) -> tuple[int, int, int]:
    """Une session avec un match deja commence et un match a venir."""
    passe = _match(settings, "Moutet", "Bergs", COMMENCE_PASSE)
    a_venir = _match(settings, "Fils", "Rune", COMMENCE_A_VENIR)
    session_id = 0
    for event_id in (passe, a_venir):
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, passe, a_venir


# -- La regle ---------------------------------------------------------------


def test_le_coup_d_envoi_fait_basculer() -> None:
    """A l'heure pile, il n'y a plus de pari avant-match a placer."""
    assert session_service.has_started("2026-08-03T10:00:00Z", NOW) is True
    assert session_service.has_started("2026-08-03T10:00:01Z", NOW) is False


def test_une_date_illisible_n_ecarte_pas_le_match() -> None:
    assert session_service.has_started("pas une date", NOW) is False


# -- Prompt -----------------------------------------------------------------


def test_un_match_commence_sort_du_prompt(migrated: Settings) -> None:
    session_id, _, _ = _selection(migrated)

    blocks = session_service.render_blocks(session_id, migrated, NOW)

    assert len(blocks) == 1
    assert "Fils – Rune" in blocks[0]
    assert "Moutet" not in blocks[0]
    assert blocks[0].startswith("### M1"), "la numerotation ne laisse pas de trou"


def test_le_prompt_nomme_ce_qu_il_a_ecarte(migrated: Settings) -> None:
    session_id, _, _ = _selection(migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 1
    assert prompt.started == ["Moutet – Bergs"]


# -- Shortlist et compteurs -------------------------------------------------


def test_le_match_commence_reste_dans_la_session(migrated: Settings) -> None:
    """L'historique des picks s'appuie dessus : on ne le supprime jamais tout seul."""
    session_id, _, _ = _selection(migrated)

    view = session_service.build_view(session_id, migrated, NOW)

    assert view.count == 2
    assert view.upcoming == 1
    assert view.started_count == 1
    assert [event.started for _, events in view.groups for event in events] == [True, False]
    assert len(db.query("SELECT * FROM session_events", settings=migrated)) == 2


def test_le_bandeau_ne_compte_que_les_matchs_a_venir(migrated: Settings) -> None:
    _selection(migrated)

    banner = board_service.banner(migrated, NOW)

    assert banner.selected_count == 1
    assert banner.started_count == 1, "le match commence est dit a part, jamais tu"


# -- Retrait manuel ---------------------------------------------------------


def test_retirer_un_match_de_la_shortlist(client: TestClient, isolated_settings: Settings) -> None:
    """Le board ne montre plus un match commence : la shortlist doit pouvoir le rendre."""
    session_id, passe, a_venir = _selection(isolated_settings)

    response = client.post(f"/session/{session_id}/events/{passe}/remove")

    assert response.status_code == 200
    restants = db.query("SELECT event_id FROM session_events", settings=isolated_settings)
    assert [int(row["event_id"]) for row in restants] == [a_venir]


def test_retrait_sur_une_session_inconnue(client: TestClient, isolated_settings: Settings) -> None:
    _, passe, _ = _selection(isolated_settings)

    assert client.post(f"/session/999/events/{passe}/remove").status_code == 404


# -- Provenance et marches abandonnes ---------------------------------------
#
# Le bloc doit dire d'ou vient chaque cote et ce que l'API n'a jamais servi :
# sans cela, une cote de reference passe pour jouable et une absence definitive
# passe pour un trou de collecte.


def _match_de_competition(settings: Settings, key: str = "tennis_atp_us_open") -> tuple[int, int]:
    """Un match a venir rattache a une competition d'API."""
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (key,), settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) "
        "VALUES (?, ?, 'evt-1', 'Fils', 'Rune', ?, 'oddsapi', ?)",
        (sport["id"], competition["id"], COMMENCE_A_VENIR, db.utcnow()),
        settings=settings,
    )
    event_id = int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])
    return event_id, int(competition["id"])


def _cote(
    settings: Settings, event_id: int, book: str, market: str, name: str, price: float
) -> None:
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, point, price, "
        "fetched_at) VALUES (?, ?, ?, ?, ?, ?, '2026-08-03T12:08:00Z')",
        (event_id, book, market, name, -3.5 if market == "spreads" else None, price),
        settings=settings,
    )


def test_le_bloc_distingue_la_cote_jouable_de_la_cote_de_reference(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, _ = _match_de_competition(isolated_settings)
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    _cote(isolated_settings, event_id, "pinnacle", "spreads", "Fils", 1.84)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert "MARCHES (Betclic, releve" in block, "l'en-tete ne nomme que la source jouable"
    assert "  Vainqueur   1.81" in block
    assert "[Pinnacle (ref.)]" in block.split("Hand. jeux")[1].splitlines()[0]


def test_le_bloc_enumere_les_marches_que_l_api_ne_sert_pas(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, competition_id = _match_de_competition(isolated_settings)
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(
            competition_id,
            ("h2h", "spreads", "h2h_s1"),
            {"bookmakers": [{"key": "betclic_fr", "markets": [{"key": "h2h"}]}]},
            isolated_settings,
        )

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert "  Non servis  Hand. jeux, Set 1 — aucun book interroge" in block


def test_un_marche_abandonne_ailleurs_ne_deteint_pas_sur_ce_match(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, _ = _match_de_competition(isolated_settings)
    autre = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_wta_us_open'",
        settings=isolated_settings,
    )
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(
            int(autre["id"]),
            ("h2h", "spreads"),
            {"bookmakers": [{"key": "betclic_fr", "markets": [{"key": "h2h"}]}]},
            isolated_settings,
        )

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert "Non servis" not in block
