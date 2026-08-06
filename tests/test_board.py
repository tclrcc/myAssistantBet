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
    assert board_service.banner(migrated, NOW).selected_count == 1
    assert len(db.query("SELECT * FROM session_events", settings=migrated)) == 1

    board_service.toggle_selection(event_id, False, migrated)
    assert board_service.banner(migrated, NOW).selected_count == 0


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


def test_le_bandeau_porte_le_quota_du_fournisseur_de_contexte(migrated: Settings) -> None:
    """Ce quota n'etait surveille nulle part : il ne servait qu'au contexte,
    quelques dizaines d'appels par soiree. Le dossier d'equipe en consomme assez
    pour qu'une journee chargee le vide sans que rien ne l'annonce."""
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('apifootball', '/coachs', 1, 4300, '2026-08-03T06:00:00Z')",
        settings=migrated,
    )

    state = board_service.banner(migrated)

    assert state.context_calls_remaining == 4300
    assert state.context_below_floor is False
    assert state.credits_remaining is None, "les deux quotas ne se melangent jamais"


def test_le_bandeau_signale_le_plancher_de_contexte_franchi(migrated: Settings) -> None:
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('apifootball', '/coachs', 1, 90, '2026-08-03T06:00:00Z')",
        settings=migrated,
    )

    state = board_service.banner(migrated)

    assert state.context_below_floor is True


def test_bandeau_sans_appel_de_contexte(migrated: Settings) -> None:
    state = board_service.banner(migrated)

    assert state.context_calls_remaining is None
    assert state.context_below_floor is False, "un quota inconnu n'est pas un quota epuise"


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


def test_les_competitions_sont_groupees_par_sport_et_triees(migrated: Settings) -> None:
    """Un ordre de priorite melangeant les sports ne dit pas ou chercher."""
    db.execute(
        "UPDATE competitions SET active = 1 "
        "WHERE sport_id = (SELECT id FROM sports WHERE key = 'tennis')",
        settings=migrated,
    )
    options = board_service.filter_options(migrated)

    groups = options["competition_groups"]
    # Le football n'a pas de niveau : le sport seul titre son groupe. Les
    # tournois de tennis livres par les migrations sont tous des Grands Chelems.
    assert [group["label"] for group in groups] == ["Football", "Tennis · Grand Chelem"]
    # Les groupes suivent l'ordre des sports, les competitions l'alphabet.
    for group in groups:
        labels = [row["label"] for row in group["competitions"]]
        assert labels == sorted(labels, key=str.casefold)
    # La liste a plat reste servie : `coherent()` s'en sert pour ecarter un
    # filtre devenu invisible.
    assert len(options["competitions"]) == sum(len(group["competitions"]) for group in groups)


def test_les_tournois_sont_groupes_par_niveau(migrated: Settings) -> None:
    """Un Grand Chelem et un 250 ne se cherchent pas au meme endroit."""
    db.execute(
        "UPDATE competitions SET active = 1 WHERE oddsapi_key = 'tennis_atp_french_open'",
        settings=migrated,
    )
    db.execute(
        "UPDATE competitions SET active = 1, category = 'level_500' "
        "WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=migrated,
    )
    db.execute(
        "UPDATE competitions SET active = 1, category = NULL "
        "WHERE oddsapi_key = 'tennis_atp_wimbledon'",
        settings=migrated,
    )

    groups = [
        group["label"]
        for group in board_service.filter_options(migrated, "tennis")["competition_groups"]
    ]

    # L'ordre suit la hierarchie, pas l'alphabet — sinon « ATP/WTA 500 »
    # passerait devant « Grand Chelem ». Le tournoi sans niveau ferme la marche.
    assert groups == ["Tennis · Grand Chelem", "Tennis · ATP/WTA 500", "Tennis"]


def test_le_tri_des_competitions_ignore_les_accents(migrated: Settings) -> None:
    """Sans cela « Série A » tomberait apres « Super Lig », a la fin de la liste."""
    db.execute(
        "INSERT INTO competitions (sport_id, oddsapi_key, label, priority, active) "
        "SELECT id, 'soccer_italy_serie_a', 'Série A', 10, 1 FROM sports WHERE key = 'football'",
        settings=migrated,
    )

    labels = [
        row["label"] for row in board_service.filter_options(migrated, "football")["competitions"]
    ]

    assert labels.index("Série A") < labels.index("Super Lig")


@respx.mock
async def test_selection_en_masse_suit_le_filtre(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Cocher en masse ne doit embarquer que ce que le filtre laisse voir."""
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    touches = board_service.toggle_filtered(
        board_service.Filters(text="Malmo"), True, migrated, now=NOW
    )

    assert touches == 1
    rows = board_service.list_rows(settings=migrated, now=NOW)
    assert [row.selected for row in rows] == [False, True]
    assert board_service.banner(migrated, NOW).selected_count == 1


@respx.mock
async def test_selection_en_masse_est_idempotente(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))

    board_service.toggle_filtered(None, True, migrated, now=NOW)
    board_service.toggle_filtered(None, True, migrated, now=NOW)

    assert board_service.banner(migrated, NOW).selected_count == 2
    assert len(db.query("SELECT * FROM session_events", settings=migrated)) == 2


@respx.mock
async def test_deselection_en_masse_epargne_le_reste(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Decocher sous filtre ne touche pas ce qui est coche en dehors."""
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))
    board_service.toggle_filtered(None, True, migrated, now=NOW)

    board_service.toggle_filtered(board_service.Filters(text="Malmo"), False, migrated, now=NOW)

    rows = board_service.list_rows(settings=migrated, now=NOW)
    assert [row.selected for row in rows] == [True, False]


def test_selection_en_masse_sur_board_vide(migrated: Settings) -> None:
    assert board_service.toggle_filtered(None, True, migrated, now=NOW) == 0


def test_les_competitions_proposees_suivent_le_sport(migrated: Settings) -> None:
    """Proposer les ligues de football en tennis n'offre que des filtres vides."""
    db.execute(
        "UPDATE competitions SET active = 1 WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=migrated,
    )
    toutes = board_service.filter_options(migrated)
    tennis = board_service.filter_options(migrated, "tennis")

    assert {row["sport_key"] for row in tennis["competitions"]} == {"tennis"}
    assert len(tennis["competitions"]) < len(toutes["competitions"])
    # La liste des sports, elle, ne se restreint jamais.
    assert tennis["sports"] == toutes["sports"]


def test_un_sport_sans_competition_active_donne_une_liste_vide(migrated: Settings) -> None:
    options = board_service.filter_options(migrated, "cycling")

    assert options["competitions"] == []


def test_une_competition_d_un_autre_sport_est_ecartee(migrated: Settings) -> None:
    """Changer de sport ne doit pas laisser un filtre invisible vider le board."""
    football = board_service.filter_options(migrated, "football")["competitions"][0]
    options = board_service.filter_options(migrated, "tennis")

    corrige = board_service.coherent(
        board_service.Filters(sport="tennis", competition_id=football["id"]), options
    )

    assert corrige.competition_id is None
    assert corrige.sport == "tennis"


def test_une_competition_du_bon_sport_est_conservee(migrated: Settings) -> None:
    options = board_service.filter_options(migrated, "football")
    garde = options["competitions"][0]["id"]

    corrige = board_service.coherent(
        board_service.Filters(sport="football", competition_id=garde), options
    )

    assert corrige.competition_id == garde


@respx.mock
async def test_le_board_ignore_une_competition_incoherente(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le board montre le sport demande, pas une page vide sans explication."""
    await _seed_board(odds_client, migrated, load_fixture("oddsapi_allsvenskan_scan.json"))
    allsvenskan = next(
        row["id"]
        for row in board_service.filter_options(migrated, "football")["competitions"]
        if row["label"] == "Allsvenskan"
    )

    view = board_service.build_view(
        board_service.Filters(sport="football", competition_id=allsvenskan), migrated, now=NOW
    )
    assert len(view.rows) == 2

    # Le meme filtre, mais en tennis : la competition n'a plus lieu d'etre.
    autre = board_service.build_view(
        board_service.Filters(sport="tennis", competition_id=allsvenskan), migrated, now=NOW
    )
    assert autre.filters.competition_id is None
    assert {row["sport_key"] for row in autre.options["competitions"]} <= {"tennis"}
