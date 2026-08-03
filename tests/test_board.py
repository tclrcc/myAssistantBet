from __future__ import annotations

from typing import Any

import httpx
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services import board as board_service
from myassistantbet.services.scan import active_competitions, run_scan

from .helpers import NOW, QUOTA_HEADERS


async def _seed_board(client: OddsAPIClient, settings: Settings, payload: Any) -> None:
    for competition in active_competitions(settings):
        key = competition["oddsapi_key"]
        respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200,
                json=payload if key == "soccer_sweden_allsvenskan" else [],
                headers=QUOTA_HEADERS,
            )
        )
    await run_scan(client, settings, now=NOW)


@respx.mock
async def test_lignes_du_board(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    rows = board_service.list_rows(settings=migrated, now=NOW)

    assert len(rows) == 2
    first = rows[0]
    assert first.affiche == "BK Hacken – Djurgardens IF"
    assert first.competition_label == "Allsvenskan"
    assert first.sport_label == "Football"
    assert (first.home_price, first.draw_price, first.away_price) == (2.55, 3.55, 2.6)
    # 17:30 heure de Paris pour un coup d'envoi a 15:30 UTC.
    assert first.local_time.strftime("%d/%m %H:%M") == "03/08 17:30"


@respx.mock
async def test_ligne_ou_principale_est_la_plus_equilibree(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    row = board_service.list_rows(settings=migrated, now=NOW)[0]

    # Lignes disponibles : 1.5 (1.22/4.10), 2.5 (1.72/2.05), 3.5 (2.90/1.38).
    assert row.total_point == 2.5
    assert (row.over_price, row.under_price) == (1.72, 2.05)


@respx.mock
async def test_evenement_sans_totals_n_a_pas_de_ligne(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    row = board_service.list_rows(settings=migrated, now=NOW)[1]

    assert row.affiche == "IFK Norrkoping – Malmo FF"
    assert row.total_point is None
    assert row.has_odds is True


@respx.mock
async def test_filtres(odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    par_texte = board_service.list_rows(
        board_service.Filters(text="Malmo"), settings=migrated, now=NOW
    )
    assert [row.away for row in par_texte] == ["Malmo FF"]

    par_sport = board_service.list_rows(
        board_service.Filters(sport="tennis"), settings=migrated, now=NOW
    )
    assert par_sport == []

    # Le premier match est a 17:30, le second a 19:00 (heure de Paris).
    par_heure = board_service.list_rows(
        board_service.Filters(hour_from=18), settings=migrated, now=NOW
    )
    assert [row.home for row in par_heure] == ["IFK Norrkoping"]


@respx.mock
async def test_selection_et_bandeau(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))
    event_id = board_service.list_rows(settings=migrated, now=NOW)[0].event_id

    board_service.toggle_selection(event_id, True, migrated)
    board_service.toggle_selection(event_id, True, migrated)  # deux fois : pas de doublon

    rows = board_service.list_rows(settings=migrated, now=NOW)
    assert rows[0].selected is True
    assert rows[1].selected is False
    assert board_service.banner(migrated).selected_count == 1
    assert len(db.query("SELECT * FROM session_events", settings=migrated)) == 1

    board_service.toggle_selection(event_id, False, migrated)
    assert board_service.banner(migrated).selected_count == 0


@respx.mock
async def test_bandeau_apres_scan(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    state = board_service.banner(migrated)

    assert state.credits_remaining == 4821
    assert state.credit_floor == 500
    assert state.below_floor is False
    assert state.last_scan_at is not None


def test_bandeau_sans_aucun_appel(migrated: Settings) -> None:
    state = board_service.banner(migrated)

    assert state.credits_remaining is None
    assert state.last_scan_at is None
    assert state.below_floor is False, "un quota inconnu n'est pas un quota epuise"


def test_bandeau_sous_le_plancher(migrated: Settings) -> None:
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('oddsapi', '/sports/x/odds', 2, 120, '2026-08-03T06:00:00Z')",
        settings=migrated,
    )

    state = board_service.banner(migrated)

    assert state.credits_remaining == 120
    assert state.below_floor is True


def test_session_courante_est_reutilisee(migrated: Settings) -> None:
    first = board_service.current_session(migrated)
    second = board_service.current_session(migrated)

    assert first == second
    assert len(db.query("SELECT id FROM sessions", settings=migrated)) == 1


def test_options_de_filtre(migrated: Settings) -> None:
    options = board_service.filter_options(migrated)

    assert [sport["key"] for sport in options["sports"]] == ["football", "tennis", "cycling"]
    assert len(options["competitions"]) == 7
