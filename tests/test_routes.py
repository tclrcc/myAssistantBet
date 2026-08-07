from __future__ import annotations

from collections.abc import Iterator
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.providers.oddsapi import BASE_URL
from myassistantbet.services.scan import active_competitions

from .helpers import QUOTA_HEADERS
from .test_db import LATEST_VERSION


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _seed_event(settings: Settings) -> int:
    """Insere un evenement a une date lointaine mais dans la fenetre de test."""
    from datetime import UTC, datetime, timedelta

    soon = (datetime.now(UTC) + timedelta(hours=2)).strftime("%Y-%m-%dT%H:%M:%SZ")
    competition = active_competitions(settings)[0]
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 'evt-test', 'Lyon', 'Nice', ?, 'api', ?)",
        (competition["sport_id"], competition["id"], soon, db.utcnow()),
        settings=settings,
    )
    row = db.query_one(
        "SELECT id FROM events WHERE oddsapi_event_id = 'evt-test'", settings=settings
    )
    return int(row["id"])


def test_board_repond(client: TestClient) -> None:
    response = client.get("/")

    assert response.status_code == 200
    assert "MyAssistantBet" in response.text
    assert "Crédits Odds API" in response.text
    assert "Relancer le scan" in response.text


def test_board_vide_affiche_un_message(client: TestClient) -> None:
    assert "Aucun événement dans la fenêtre courante" in client.get("/").text


def test_fragment_board(client: TestClient, isolated_settings: Settings) -> None:
    _seed_event(isolated_settings)

    response = client.get("/board", params={"text": "Lyon"})

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="board">')
    assert "Lyon – Nice" in response.text


def test_filtre_texte_sans_resultat(client: TestClient, isolated_settings: Settings) -> None:
    _seed_event(isolated_settings)

    assert "Lyon" not in client.get("/board", params={"text": "Marseille"}).text


def test_parametre_invalide_ne_casse_pas_le_board(client: TestClient) -> None:
    response = client.get("/board", params={"hour_from": "midi", "competition_id": "x"})

    assert response.status_code == 200


def test_selection_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    event_id = _seed_event(isolated_settings)

    coche = client.post(f"/events/{event_id}/select", data={"selected": "1"})
    assert coche.status_code == 200
    assert 'id="banner"' in coche.text
    assert len(db.query("SELECT * FROM session_events", settings=isolated_settings)) == 1

    decoche = client.post(f"/events/{event_id}/select", data={})
    assert decoche.status_code == 200
    assert db.query("SELECT * FROM session_events", settings=isolated_settings) == []


@respx.mock
def test_scan_manuel(client: TestClient, isolated_settings: Settings, load_fixture: Any) -> None:
    payload = load_fixture("oddsapi_allsvenskan_scan.json")
    for competition in active_competitions(isolated_settings):
        key = competition["oddsapi_key"]
        respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200,
                json=payload if key == "soccer_sweden_allsvenskan" else [],
                headers=QUOTA_HEADERS,
            )
        )

    response = client.post("/scan")

    assert response.status_code == 200
    assert "Scan terminé" in response.text
    assert "crédit(s) consommé(s)" in response.text
    assert "4821" in response.text, "le bandeau doit refleter le quota restant"


@respx.mock
def test_scan_signale_une_competition_indisponible(
    client: TestClient, isolated_settings: Settings
) -> None:
    for competition in active_competitions(isolated_settings):
        respx.get(f"{BASE_URL}/sports/{competition['oddsapi_key']}/odds").mock(
            return_value=httpx.Response(503, text="indisponible")
        )

    response = client.post("/scan")

    assert response.status_code == 200, "une API HS ne doit jamais empecher de servir la page"
    assert "indisponible" in response.text


def test_static_servi(client: TestClient) -> None:
    assert client.get("/static/app.css").status_code == 200
    assert client.get("/static/htmx.min.js").status_code == 200


def test_health_toujours_ok(client: TestClient) -> None:
    payload = client.get("/health").json()

    assert payload["status"] == "ok"
    assert payload["config"]["scheduler_enabled"] is False
    assert payload["db"]["schema_version"] == LATEST_VERSION


# --- Shortlist et prompt ---------------------------------------------------


def _select_event(client: TestClient, settings: Settings) -> tuple[int, int]:
    """Coche un evenement et renvoie (session_id, event_id)."""
    event_id = _seed_event(settings)
    client.post(f"/events/{event_id}/select", data={"selected": "1"})
    session = db.query_one("SELECT id FROM sessions", settings=settings)
    return int(session["id"]), event_id


def test_shortlist_affiche_la_selection(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _select_event(client, isolated_settings)
    # Le 1N2 de l'etage A : sur une competition que Betclic sert, il est deja en
    # base et l'etage B ne le rachete pas. Sans cette cote, le match se lirait
    # comme un match sans etage A, et couterait un credit de plus.
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
        "VALUES (?, 'betclic_fr', 'h2h', 'Lyon', 1.8, ?)",
        (event_id, db.utcnow()),
        settings=isolated_settings,
    )

    response = client.get(f"/session/{session_id}")

    assert response.status_code == 200
    assert "Lyon – Nice" in response.text
    assert "Coût estimé" in response.text
    # Ligue 1 est dans la liste blanche des props : 14 marches + 2 props.
    assert "16 crédits" in response.text


def test_un_match_sans_etage_a_reclame_son_1n2(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Sur une competition que le book principal ne sert pas, l'etage A ne ramene
    rien : le 1N2 s'ajoute a la demande de l'etage B, pour un credit de plus.

    Sans lui, le marche n'arrivait jamais **et** ne pouvait pas etre declare
    manquant — il disparaissait du bloc sans laisser de trace. Constate en reel
    sur la Super League chinoise.
    """
    session_id, _ = _select_event(client, isolated_settings)

    response = client.get(f"/session/{session_id}")

    assert response.status_code == 200
    assert "17 crédits" in response.text


def test_shortlist_inconnue_renvoie_404(client: TestClient) -> None:
    assert client.get("/session/999").status_code == 404


def test_bouton_enrichir_desactive_sous_le_plancher(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _ = _select_event(client, isolated_settings)
    db.execute(
        "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
        "VALUES ('oddsapi', '/x', 2, 505, '2099-01-01T00:00:00Z')",
        settings=isolated_settings,
    )

    response = client.get(f"/session/{session_id}")

    assert "disabled" in response.text
    assert "plancher" in response.text


def test_note_enregistree(client: TestClient, isolated_settings: Settings) -> None:
    session_id, event_id = _select_event(client, isolated_settings)

    response = client.post(
        f"/session/{session_id}/events/{event_id}/note", data={"note": "  Gardien incertain  "}
    )

    assert response.status_code == 204
    row = db.query_one("SELECT note FROM session_events", settings=isolated_settings)
    assert row["note"] == "Gardien incertain"


@respx.mock
def test_enrichissement_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _select_event(client, isolated_settings)
    respx.get(url__regex=r".*/events/evt-test/odds.*").mock(
        return_value=httpx.Response(200, json={"bookmakers": []}, headers=QUOTA_HEADERS)
    )

    lance = client.post(f"/session/{session_id}/enrich")
    assert lance.status_code == 200
    assert 'id="enrich"' in lance.text

    statut = client.get(f"/session/{session_id}/enrich/status")
    assert statut.status_code == 200


def test_page_prompt(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _select_event(client, isolated_settings)

    response = client.get(f"/session/{session_id}/prompt")

    assert response.status_code == 200
    assert "SESSION D&#39;ANALYSE" in response.text or "SESSION D'ANALYSE" in response.text
    assert "Lyon – Nice" in response.text
    assert "tokens" in response.text
    assert "Copier" in response.text
    assert "session_default.md.j2" in response.text


def test_prompt_sauvegarde_a_chaque_generation(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _ = _select_event(client, isolated_settings)

    client.get(f"/session/{session_id}/prompt")
    client.get(f"/session/{session_id}/prompt")

    assert len(db.query("SELECT id FROM prompts", settings=isolated_settings)) == 2


def test_prompt_template_inconnu_renvoie_404(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _ = _select_event(client, isolated_settings)

    assert client.get(f"/session/{session_id}/prompt?template=nope.md.j2").status_code == 404


def test_telechargement_markdown(client: TestClient, isolated_settings: Settings) -> None:
    session_id, _ = _select_event(client, isolated_settings)

    response = client.get(f"/session/{session_id}/prompt.md")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    assert f'filename="session-{session_id}.md"' in response.headers["content-disposition"]
    assert response.text.startswith("# SESSION D'ANALYSE")


def test_la_page_courante_est_marquee_dans_la_navigation(client: TestClient) -> None:
    """Sans reperage, six liens obligent a relire l'URL pour savoir ou l'on est."""
    page = client.get("/competitions").text

    assert '<a href="/competitions"\n           class="is-current"' in page or (
        'href="/competitions"' in page and "is-current" in page
    )
    assert page.count("is-current") == 1, "une seule section a la fois"


def test_une_sous_page_reste_marquee(client: TestClient) -> None:
    """/history/2 appartient toujours a « Historique »."""
    page = client.get("/history").text

    assert "is-current" in page


def test_la_recherche_de_match_rend_les_options_seules(
    client: TestClient, isolated_settings: Settings
) -> None:
    """La recherche remplace le contenu du menu, pas le formulaire : rerendre
    le formulaire ferait perdre le focus a chaque frappe."""
    session_id, _ = _select_event(client, isolated_settings)

    response = client.get(f"/history/{session_id}/pick-options", params={"q": "Lyon"})

    assert response.status_code == 200
    assert "Lyon – Nice" in response.text
    assert "<optgroup" in response.text
    assert "<form" not in response.text, "un fragment d'options, jamais un formulaire"
    assert "<html" not in response.text


def test_une_recherche_sans_resultat_le_dit(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Un menu reduit a « hors match » se lirait comme une panne."""
    session_id, _ = _select_event(client, isolated_settings)

    response = client.get(f"/history/{session_id}/pick-options", params={"q": "zzz-introuvable"})

    assert "aucun match ne correspond" in response.text


def test_le_sprite_porte_les_pictogrammes_de_sport(client: TestClient) -> None:
    """Sans le sprite en tete de page, chaque `<use>` du board pointe dans le
    vide et la colonne « Sport » se vide sans rien dire — exactement le defaut
    que les emoji avaient sur un appareil sans police d'emoji."""
    page = client.get("/").text

    assert '<svg class="sprite"' in page
    for sport in ("football", "tennis", "cycling"):
        assert f'id="i-{sport}"' in page


def test_la_police_est_servie_en_local(client: TestClient) -> None:
    """Vendorisee comme htmx : aucun CDN, aucun appel reseau depuis la page."""
    assert "fonts/InterVariable.woff2" not in client.get("/").text, "referencee par la CSS"
    css = client.get("/static/app.css").text
    assert 'src: url("fonts/InterVariable.woff2") format("woff2")' in css
    assert "//fonts.googleapis" not in css and "https://" not in css
    assert client.get("/static/fonts/InterVariable.woff2").status_code == 200
