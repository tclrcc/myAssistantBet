from __future__ import annotations

from typing import Any

import httpx
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services.scan import active_competitions, run_scan, scan_window

from .helpers import NOW, QUOTA_HEADERS


def _mock_all_competitions(payload_by_key: dict[str, Any]) -> dict[str, respx.Route]:
    """Repond a chaque competition active : la fixture si connue, sinon une liste vide.

    Renvoie les routes par cle de competition, pour pouvoir en ajuster une ensuite.
    """
    routes: dict[str, respx.Route] = {}
    for competition in active_competitions():
        key = competition["oddsapi_key"]
        routes[key] = respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200, json=payload_by_key.get(key, []), headers=QUOTA_HEADERS
            )
        )
    return routes


def test_seed_des_competitions(migrated: Settings) -> None:
    competitions = active_competitions(migrated)

    labels = {item["label"] for item in competitions}
    assert labels == {
        "Ligue 1",
        "Premier League",
        "Allsvenskan",
        "Eliteserien",
        "Chinese Super League",
        "Liga Portugal",
        "Super Lig",
    }
    assert competitions[0]["label"] == "Ligue 1", "tri par priorite decroissante"
    assert all(item["sport_key"] == "football" for item in competitions)


def test_fenetre_de_scan_couvre_j0_et_j1(migrated: Settings) -> None:
    start, end = scan_window(migrated, NOW)

    assert start == NOW
    # Fin de journee du 4 aout a Paris (UTC+2) = 21:59:59Z.
    assert end.isoformat() == "2026-08-04T21:59:59+00:00"


@respx.mock
async def test_scan_persiste_evenements_et_cotes(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.total_events == 2, "le match du 11 aout est hors fenetre"
    assert report.failures == []
    assert report.total_cost == 2 * len(active_competitions(migrated))

    events = db.query("SELECT * FROM events ORDER BY commence_time", settings=migrated)
    assert [row["home"] for row in events] == ["BK Hacken", "IFK Norrkoping"]
    assert events[0]["source"] == "api"

    odds = db.query(
        "SELECT market_key, outcome_name, point, price FROM odds "
        "WHERE event_id = ? ORDER BY market_key, point, outcome_name",
        (events[0]["id"],),
        settings=migrated,
    )
    assert len([row for row in odds if row["market_key"] == "h2h"]) == 3
    assert len([row for row in odds if row["market_key"] == "totals"]) == 6
    assert odds[0]["price"] == 2.55


@respx.mock
async def test_scan_est_idempotent(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    payload = {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    _mock_all_competitions(payload)

    await run_scan(odds_client, migrated, now=NOW)
    counts_after_first = (
        len(db.query("SELECT id FROM events", settings=migrated)),
        len(db.query("SELECT id FROM odds", settings=migrated)),
    )

    await run_scan(odds_client, migrated, now=NOW)
    counts_after_second = (
        len(db.query("SELECT id FROM events", settings=migrated)),
        len(db.query("SELECT id FROM odds", settings=migrated)),
    )

    assert counts_after_first == counts_after_second == (2, 12)


@respx.mock
async def test_les_cotes_sont_remplacees_pas_accumulees(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    routes = _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)

    # Nouveau releve : la cote domicile bouge et le marche totals disparait.
    payload[0]["bookmakers"][0]["markets"] = [
        {
            "key": "h2h",
            "outcomes": [
                {"name": "BK Hacken", "price": 2.9},
                {"name": "Djurgardens IF", "price": 2.4},
                {"name": "Draw", "price": 3.5},
            ],
        }
    ]
    routes["soccer_sweden_allsvenskan"].mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await run_scan(odds_client, migrated, now=NOW)

    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=migrated)
    prices = db.query(
        "SELECT market_key, price FROM odds WHERE event_id = ? AND outcome_name = 'BK Hacken'",
        (event["id"],),
        settings=migrated,
    )
    assert [row["price"] for row in prices] == [2.9]
    # Un marche absent du nouveau releve conserve son dernier etat connu.
    totals = db.query(
        "SELECT id FROM odds WHERE event_id = ? AND market_key = 'totals'",
        (event["id"],),
        settings=migrated,
    )
    assert len(totals) == 6


@respx.mock
async def test_une_competition_en_echec_n_interrompt_pas_le_scan(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    routes = _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )
    routes["soccer_epl"].mock(return_value=httpx.Response(503, text="indisponible"))

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.total_events == 2
    assert [failure.label for failure in report.failures] == ["Premier League"]
    assert "503" in report.failures[0].error


@respx.mock
async def test_evenement_hors_fenetre_ignore(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    await run_scan(odds_client, migrated, now=NOW)

    assert db.query_one("SELECT id FROM events WHERE home = 'AIK'", settings=migrated) is None


@respx.mock
async def test_borne_de_fenetre_transmise_a_l_api(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    routes = _mock_all_competitions({})

    await run_scan(odds_client, migrated, now=NOW)

    request = routes["soccer_france_ligue_one"].calls[0].request
    assert request.url.params["commenceTimeTo"] == "2026-08-04T21:59:59Z"


# -- Le report d'un horaire --------------------------------------------------


@respx.mock
async def test_un_horaire_deplace_est_garde(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le fait dominant d'une soiree peut etre un report**, et l'application
    l'effacait a chaque scan : une journee d'orages a Cincinnati a repousse tout
    le programme de cinq heures — 17:30 au releve de 12:42, 22:30 a celui de
    22:15 — et le prompt ne portait que la derniere heure. Le decalage a du etre
    retrouve dans la presse alors que les deux relevés etaient passes par ici."""
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)
    avant = db.query_one(
        "SELECT id, commence_time FROM events ORDER BY commence_time", settings=migrated
    )

    repousse = [dict(item) for item in payload]
    # +5h05, l'ecart reel de la soiree de Cincinnati, et dans la fenetre de scan.
    repousse[0]["commence_time"] = "2026-08-03T20:35:00Z"
    _mock_all_competitions({"soccer_sweden_allsvenskan": repousse})
    await run_scan(odds_client, migrated, now=NOW)

    apres = db.query_one(
        "SELECT commence_time, previous_commence_time, commence_shifted_at FROM events "
        "WHERE id = ?",
        (avant["id"],),
        settings=migrated,
    )
    assert apres["commence_time"] == "2026-08-03T20:35:00Z"
    assert apres["previous_commence_time"] == avant["commence_time"]
    assert apres["commence_shifted_at"], "l'instant du constat, sans quoi un vieux report"


@respx.mock
async def test_un_horaire_stable_n_ecrit_rien(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """La mention doit rester un **signal**, pas un decor : deux scans du meme
    programme ne produisent aucun report. Le seuil est celui de l'age d'un
    releve — en dessous, un ecart n'a rien traverse."""
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)

    frole = [dict(item) for item in payload]
    # Dix minutes : sous le seuil, et c'est le cas ordinaire d'un fournisseur
    # qui reajuste un horaire a la marge.
    frole[0]["commence_time"] = "2026-08-03T15:40:00Z"
    _mock_all_competitions({"soccer_sweden_allsvenskan": frole})
    await run_scan(odds_client, migrated, now=NOW)

    lignes = db.query(
        "SELECT previous_commence_time FROM events WHERE previous_commence_time IS NOT NULL",
        settings=migrated,
    )
    assert lignes == []


@respx.mock
async def test_une_competition_non_rattachee_est_signalee_au_scan(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le controle est en amont, et c'est tout son interet.**

    Le symptome arrive une journee plus tard, sous la forme d'un bloc a zero
    ligne qui se lit comme un match sans histoire plutot que comme une question
    jamais posee. Le scan, lui, sait au moment ou les matchs entrent en base
    que rien ne pourra leur etre demande.
    """
    db.execute(
        "UPDATE competitions SET apifootball_league_id = NULL WHERE oddsapi_key = ?",
        ("soccer_sweden_allsvenskan",),
        settings=migrated,
    )
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    report = await run_scan(odds_client, migrated, now=NOW)

    signalees = [item.oddsapi_key for item in report.unmapped]
    assert signalees == ["soccer_sweden_allsvenskan"]
    assert report.unmapped[0].events == 2


@respx.mock
async def test_une_competition_non_rattachee_sans_match_ne_dit_rien(
    odds_client: OddsAPIClient, migrated: Settings
) -> None:
    """Une competition qui ne sert rien ne coute rien et n'a rien d'urgent.

    Les lister toutes ferait un signal de trente-trois lignes ou plus personne
    ne verrait celle qui joue ce soir — exactement le defaut du compte de
    mapping, qui se lit comme une file d'attente.
    """
    db.execute(
        "UPDATE competitions SET apifootball_league_id = NULL WHERE oddsapi_key = ?",
        ("soccer_sweden_allsvenskan",),
        settings=migrated,
    )
    _mock_all_competitions({})

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.unmapped == []


@respx.mock
async def test_une_competition_rattachee_ne_declenche_rien(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    _mock_all_competitions(
        {"soccer_sweden_allsvenskan": load_fixture("oddsapi_allsvenskan_scan.json")}
    )

    report = await run_scan(odds_client, migrated, now=NOW)

    assert report.total_events == 2
    assert report.unmapped == []


# -- Historique des cotes ---------------------------------------------------
#
# `replace_odds` fait un DELETE puis un INSERT : seul le dernier releve
# survivait, donc l'etat d'avant n'existait nulle part une heure apres un scan.
# Meme defaut que `commence_time` avant la migration 040.
#
# **Ce chantier n'affiche rien, ne lit rien, n'alerte sur rien** : il arrete une
# perte. Les tests portent donc sur ce qui est ecrit, et sur rien d'autre.


@respx.mock
async def test_un_prix_qui_bouge_laisse_sa_trace(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les deux bornes, le book et le marche : « le prix a change entre 11h22 et
    15h06 » n'est pas « il a change a 15h06 », et un mouvement Pinnacle ne dit
    pas la meme chose qu'un mouvement Betclic."""
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    routes = _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)

    avant = db.query_one(
        "SELECT price, fetched_at FROM odds WHERE outcome_name = 'BK Hacken' "
        "AND market_key = 'h2h'",
        settings=migrated,
    )
    payload[0]["bookmakers"][0]["last_update"] = "2026-08-04T15:06:00Z"
    payload[0]["bookmakers"][0]["markets"] = [
        {"key": "h2h", "outcomes": [{"name": "BK Hacken", "price": 1.55}]}
    ]
    routes["soccer_sweden_allsvenskan"].mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await run_scan(odds_client, migrated, now=NOW)

    lignes = db.query("SELECT * FROM odds_history", settings=migrated)
    assert len(lignes) == 1
    trace = lignes[0]
    assert trace["previous_price"] == avant["price"]
    assert trace["price"] == 1.55
    # La borne basse : sans elle, tout mouvement parait instantane.
    assert trace["previous_fetched_at"] == avant["fetched_at"]
    assert trace["fetched_at"] == "2026-08-04T15:06:00Z"
    assert trace["observed_at"], "l'instant de notre lecture, distinct du releve"
    assert trace["bookmaker"] and trace["market_key"] == "h2h"
    assert trace["outcome_name"] == "BK Hacken"


@respx.mock
async def test_un_prix_stable_n_ecrit_rien(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un prix stable ne dit rien qu'`odds` ne dise deja, et l'ecrire a chaque
    scan noierait les mouvements sous leur propre bruit."""
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)
    await run_scan(odds_client, migrated, now=NOW)

    assert db.query("SELECT id FROM odds_history", settings=migrated) == []


@respx.mock
async def test_un_premier_releve_n_est_pas_un_mouvement(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Il n'y a rien avant lui : ecrire une ligne ferait passer une arrivee pour
    une derive, et le premier scan d'une journee en produirait des milliers."""
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    await run_scan(odds_client, migrated, now=NOW)

    assert db.query("SELECT id FROM odds_history", settings=migrated) == []
    assert db.query("SELECT id FROM odds", settings=migrated), "les cotes, elles, sont la"


@respx.mock
async def test_les_issues_d_un_meme_marche_ne_se_confondent_pas(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """`Over 2.5` et `Over 3.5` sont deux issues du meme marche : `point` fait
    partie de l'identite, sinon un mouvement s'attribuerait a la mauvaise ligne.
    """
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    routes = _mock_all_competitions({"soccer_sweden_allsvenskan": payload})
    payload[0]["bookmakers"][0]["markets"] = [
        {
            "key": "totals",
            "outcomes": [
                {"name": "Over", "price": 1.90, "point": 2.5},
                {"name": "Over", "price": 3.10, "point": 3.5},
            ],
        }
    ]
    routes["soccer_sweden_allsvenskan"].mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await run_scan(odds_client, migrated, now=NOW)

    # Seule la ligne 3.5 bouge au releve suivant.
    payload[0]["bookmakers"][0]["markets"][0]["outcomes"][1]["price"] = 3.60
    routes["soccer_sweden_allsvenskan"].mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await run_scan(odds_client, migrated, now=NOW)

    lignes = db.query("SELECT point, previous_price, price FROM odds_history", settings=migrated)
    assert len(lignes) == 1
    assert lignes[0]["point"] == 3.5
    assert (lignes[0]["previous_price"], lignes[0]["price"]) == (3.10, 3.60)


def test_aucune_surface_ne_lit_l_historique_des_cotes() -> None:
    """**Le garde-fou du chantier, et il est volontaire.**

    L'etape 1 arrete une perte : elle n'affiche rien, ne lit rien, n'alerte sur
    rien, et ne pose aucun seuil. La raison n'est pas technique — voir CLAUDE.md,
    « L'historique des cotes » : une derive affichee avant que le lot soit fige
    orienterait la constitution du lot et le choix des dossiers, donc le tri
    circulaire que le preambule refuse, un cran en amont et sans qu'aucune regle
    du gabarit soit violee.

    Ce test tombera le jour ou une surface la lira. Ce sera alors une decision a
    prendre, pas un detail a corriger.
    """
    from pathlib import Path

    racine = Path(__file__).resolve().parents[1] / "src" / "myassistantbet"
    lecteurs = [
        chemin.relative_to(racine).as_posix()
        for chemin in racine.rglob("*")
        if chemin.suffix in (".py", ".html", ".j2")
        and chemin.name not in ("scan.py",)
        and "odds_history" in chemin.read_text(encoding="utf-8")
    ]
    assert lecteurs == [], f"l'historique des cotes est lu par : {lecteurs}"
