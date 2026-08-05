from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from jinja2 import TemplateNotFound

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services import board as board_service
from myassistantbet.services import session as session_service
from myassistantbet.services.enrich import run_enrich
from myassistantbet.services.prompt import (
    DEFAULT_TEMPLATE,
    build_prompt,
    date_fr,
    list_templates,
    load_tiers,
    save_prompt,
)
from myassistantbet.services.scan import active_competitions, run_scan

from .helpers import NOW, QUOTA_HEADERS

EVENT_ID = "3c7f9a1b2d4e5f60718293a4b5c6d7e8"
PARIS = ZoneInfo("Europe/Paris")


async def _session_enrichie(
    client: OddsAPIClient, settings: Settings, load_fixture: Any, *, enrich: bool = True
) -> int:
    for competition in active_competitions(settings):
        key = competition["oddsapi_key"]
        respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200,
                json=load_fixture("oddsapi_allsvenskan_scan.json")
                if key == "soccer_sweden_allsvenskan"
                else [],
                headers=QUOTA_HEADERS,
            )
        )
    await run_scan(client, settings, now=NOW)

    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=settings)
    session_id = board_service.toggle_selection(int(event["id"]), True, settings)

    if enrich:
        respx.get(f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/{EVENT_ID}/odds").mock(
            return_value=httpx.Response(
                200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
            )
        )
        await run_enrich(client, session_id, settings, now=NOW)
    return session_id


# -- Briques ----------------------------------------------------------------


def test_templates_disponibles() -> None:
    names = list_templates()

    assert DEFAULT_TEMPLATE in names
    assert names[0] == DEFAULT_TEMPLATE, "le defaut est propose en premier"


def test_paliers_lus_en_base(migrated: Settings) -> None:
    tiers = load_tiers(migrated)

    assert [tier.key for tier in tiers] == ["safe", "fun", "ultra_fun", "giga_fun", "giga_plus"]
    assert tiers[0].range_label == "1.25 – 1.70"
    assert tiers[0].quota_label == "2-4 🟢"
    assert tiers[-1].range_label.startswith("> 15.00")


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 3, tzinfo=PARIS), "lundi 3 août 2026"),
        (datetime(2026, 12, 25, tzinfo=PARIS), "vendredi 25 décembre 2026"),
    ],
)
def test_date_en_francais(moment: datetime, expected: str) -> None:
    assert date_fr(moment) == expected


# -- Assemblage -------------------------------------------------------------


@respx.mock
async def test_prompt_contient_les_sections_attendues(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=datetime(2026, 8, 3, tzinfo=PARIS)).body

    assert "# SESSION D'ANALYSE — lundi 3 août 2026" in body
    assert "## TON RÔLE" in body
    assert "## MÉTHODE" in body
    assert "## CE QU'IL FAUT VÉRIFIER" in body
    assert "## MATCHS" in body
    # Les faits avant les opinions : la fiche precede l'analyse dans le corps.
    assert body.index("### A. Fiche de vérification") < body.index("### B. Analyse par match")
    assert "### E. Le match que tu ne jouerais pas" in body
    assert "### F. Ce qui aurait changé ton analyse" in body


@respx.mock
async def test_le_prompt_interdit_toujours_le_calcul_de_value(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Garde-fou de la section 9 de SPEC.md : le prompt doit porter l'interdit."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    for interdit in ("value bet", "EV", "edge", "CLV", "devigging", "Kelly"):
        assert interdit in body, f"« {interdit} » doit rester explicitement interdit"


@respx.mock
async def test_prompt_contient_scores_exacts_et_over_under(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Score exact" in body
    assert "1-1 6.50" in body
    assert "O/U" in body
    assert "2.5: 1.72/2.05" in body
    assert "BTTS        Oui 1.60 / Non 2.25" in body
    assert "Corners     O/U 9.5: 1.85/1.90" in body


@respx.mock
async def test_paliers_injectes_dans_le_prompt(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "🟢 SAFE         1.25 – 1.70" in body
    assert "💥 GIGA+" in body
    assert "Quotas indicatifs : 2-4 🟢, 3-5 🔵, 2-4 🟠, 1-3 🔴, 0-2 💥." in body


@respx.mock
async def test_note_perso_injectee_telle_quelle(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=migrated)
    session_service.set_note(session_id, int(event["id"]), "Gardien n°2 annoncé", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "NOTE PERSO  Gardien n°2 annoncé" in body


@respx.mock
async def test_prompt_reste_sous_huit_mille_tokens_pour_six_matchs(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Critere d'acceptation de la phase 2, avec le pire cas : 6 matchs enrichis."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    source = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    deep = db.query("SELECT * FROM odds WHERE event_id = ?", (source["id"],), settings=migrated)

    for index in range(5):
        db.execute(
            "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
            "commence_time, source, created_at) VALUES (?, ?, ?, ?, ?, ?, 'api', ?)",
            (
                source["sport_id"],
                source["competition_id"],
                f"clone-{index}",
                f"Equipe A{index}",
                f"Equipe B{index}",
                source["commence_time"],
                db.utcnow(),
            ),
            settings=migrated,
        )
        clone = db.query_one(
            "SELECT id FROM events WHERE oddsapi_event_id = ?",
            (f"clone-{index}",),
            settings=migrated,
        )
        with db.connect(migrated) as conn:
            for row in deep:
                conn.execute(
                    "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, "
                    "description, point, price, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        clone["id"],
                        row["bookmaker"],
                        row["market_key"],
                        row["outcome_name"],
                        row["description"],
                        row["point"],
                        row["price"],
                        row["fetched_at"],
                    ),
                )
        board_service.toggle_selection(int(clone["id"]), True, migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 6
    assert prompt.token_estimate < 8000, f"prompt trop lourd : {prompt.token_estimate} tokens"


@respx.mock
async def test_matchs_numerotes_dans_l_ordre(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    second = db.query_one("SELECT id FROM events WHERE home = 'IFK Norrkoping'", settings=migrated)
    session_id = board_service.toggle_selection(int(second["id"]), True, migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "### M1 · FOOT · Allsvenskan · BK Hacken – Djurgardens IF" in body
    assert "### M2 · FOOT · Allsvenskan · IFK Norrkoping – Malmo FF" in body


def test_session_vide_produit_un_prompt_sans_bloc(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 0
    assert "## MATCHS" in prompt.body


def test_template_inconnu_refuse(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    with pytest.raises(TemplateNotFound):
        build_prompt(session_id, "inexistant.md.j2", migrated)


# -- Sauvegarde -------------------------------------------------------------


def test_prompt_sauvegarde_en_base(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)
    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    prompt_id = save_prompt(session_id, prompt, migrated)

    row = db.query_one("SELECT * FROM prompts WHERE id = ?", (prompt_id,), settings=migrated)
    assert row["session_id"] == session_id
    assert row["template_name"] == DEFAULT_TEMPLATE
    assert row["body"] == prompt.body
    assert row["token_estimate"] == prompt.token_estimate > 0


# -- Contexte sportif dans le prompt ----------------------------------------


@respx.mock
async def test_le_prompt_contient_le_bloc_contexte(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Critere d'acceptation de la phase 3, vu depuis le prompt final."""
    from myassistantbet.providers.apifootball import APIFootballClient
    from myassistantbet.services.context import fetch_context

    from .test_context import _mock_all

    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    _mock_all(load_fixture)
    await fetch_context(
        APIFootballClient(http_client, migrated),
        {
            "id": event["id"],
            "home": event["home"],
            "away": event["away"],
            "commence_time": event["commence_time"],
            "apifootball_league_id": 113,
        },
        migrated,
    )

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CONTEXTE" in body
    assert "  Classement  BK Hacken 4e (34pts, 16j)" in body
    assert "  Forme 5     BK Hacken VVNDV (9-4)" in body
    assert "  Absents     BK Hacken — M. Rygaard" in body
    assert "  H2H (3)     1-1 · 0-2 D · 2-2" in body
    # Le bloc MARCHES suit immediatement le contexte, sans ligne vide parasite.
    assert "MARCHES (Betclic" in body


@respx.mock
async def test_absents_des_deux_equipes_sur_deux_lignes_alignees(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    from myassistantbet.providers.apifootball import APIFootballClient
    from myassistantbet.services.context import fetch_context

    from .test_context import _mock_all

    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    _mock_all(load_fixture)
    await fetch_context(
        APIFootballClient(http_client, migrated),
        {
            "id": event["id"],
            "home": event["home"],
            "away": event["away"],
            "commence_time": event["commence_time"],
            "apifootball_league_id": 113,
        },
        migrated,
    )

    rows = build_prompt(session_id, settings=migrated, now=NOW).body.splitlines()
    absents = next(index for index, row in enumerate(rows) if "Absents" in row)

    assert rows[absents].startswith("  Absents     BK Hacken — ")
    assert rows[absents + 1] == "              Djurgardens IF — aucun signale"


@respx.mock
async def test_le_prompt_verrouille_les_cotes_du_bloc(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une cote substituee classerait la selection dans le mauvais palier."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Ne remplace jamais une cote du bloc" in body
    assert "au centime près" in body


@respx.mock
async def test_le_prompt_impose_une_hierarchie_de_sources(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une blessure decide d'un pari : elle ne peut pas venir d'un agregateur."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Qualité des sources" in body
    assert "non confirmé" in body


@respx.mock
async def test_le_prompt_refuse_de_gonfler_un_combine(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "N'ajoute\njamais une jambe sous confiance 3" in body.replace("**", "")


# -- Ce que le prompt annonce du lot ----------------------------------------
#
# Sans ces annonces, l'analyste redemande des marches que le fournisseur ne sert
# pas, recalcule la taille du lot a chaque session et devine le fuseau des
# horaires. Trois devinettes evitables, toutes verifiables ici.


@respx.mock
async def test_le_prompt_annonce_les_marches_demandes(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Football : " in body
    assert "Score exact" in body, "un marche demande et servi est annonce"
    assert "BTTS" in body


@respx.mock
async def test_le_catalogue_suit_le_sport_du_lot(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un lot de tennis n'annonce pas les marches du football."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'", settings=migrated
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 'evt', 'Fils', 'Rune', "
        "'2026-08-03T18:00:00Z', 'oddsapi', ?)",
        (sport["id"], competition["id"], db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)
    board_service.toggle_selection(int(event["id"]), True, migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    ligne = next(row for row in body.splitlines() if row.strip().startswith("· Tennis"))
    assert "Hand. jeux" in ligne and "Set 1" in ligne
    assert "BTTS" not in ligne and "Score exact" not in ligne


@respx.mock
async def test_le_prompt_donne_la_taille_du_lot_et_son_plafond(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Ce lot comporte **1 match(s)**" in body
    assert "le total ne\npeut donc pas dépasser 1, tous paliers confondus." in body


@respx.mock
async def test_le_fuseau_des_horaires_est_dit(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Toutes les heures sont en Europe/Paris" in body


@respx.mock
async def test_le_multichoix_n_est_propose_que_si_le_marche_existe(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sur un lot sans scores exacts, l'imposer fait ecrire « impossible » pour rien."""
    sans = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    assert "multichoix scores exacts" not in build_prompt(sans, settings=migrated, now=NOW).body

    respx.get(f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/{EVENT_ID}/odds").mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    await run_enrich(odds_client, sans, migrated, now=NOW)

    assert "multichoix scores exacts" in build_prompt(sans, settings=migrated, now=NOW).body


@respx.mock
async def test_l_arbitrage_des_paliers_est_ecrit(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les bandes se chevauchent : sans regle, le modele en invente une par session."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "la confiance tranche" in body
