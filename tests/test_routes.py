from __future__ import annotations

import re
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
from myassistantbet.services import board as board_service
from myassistantbet.services import session as session_service
from myassistantbet.services.manual import build, save
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


def _stylesheet() -> str:
    from myassistantbet.config import PACKAGE_DIR

    return (PACKAGE_DIR / "static" / "app.css").read_text(encoding="utf-8")


def test_le_theme_clair_ne_redeclare_que_des_tokens() -> None:
    """C'est la preuve que le systeme de tokens tient : si un theme devait
    reecrire un selecteur de composant, c'est qu'une couleur avait ete ecrite
    en dur quelque part. Le jour ou ce test casse, il faut sortir la couleur
    fautive dans `:root`, pas ajouter la regle ici."""
    css = _stylesheet()
    debut = css.index("@media (prefers-color-scheme: light)")
    # Bloc delimite par comptage d'accolades : le decoupage naif s'arretait a la
    # premiere fermeture, donc avant la fin de `:root`.
    profondeur, fin = 0, debut
    for index in range(debut, len(css)):
        if css[index] == "{":
            profondeur += 1
        elif css[index] == "}":
            profondeur -= 1
            if profondeur == 0:
                fin = index
                break
    bloc = re.sub(r"/\*.*?\*/", "", css[debut:fin], flags=re.S)

    intrus = [
        ligne.strip()
        for ligne in bloc.splitlines()
        if ligne.strip() and not ligne.strip().startswith(("--", "@media", ":root", "}"))
    ]

    assert not intrus, f"le theme clair ne doit porter que des tokens : {intrus}"


def test_chaque_pictogramme_vise_un_symbole_existant(client: TestClient) -> None:
    """Un identifiant mal orthographie dans `CONTEXT_ICONS` ne casse rien : le
    `<use>` pointe simplement dans le vide et la carte perd son pictogramme
    sans un mot. Constate en reel sur une feuille de style servie perimee."""
    from myassistantbet.services.labels import CONTEXT_ICONS, SPORT_ICONS

    page = client.get("/").text
    disponibles = set(re.findall(r'<symbol id="i-([a-z]+)"', page))

    assert disponibles >= SPORT_ICONS
    manquants = set(CONTEXT_ICONS.values()) - disponibles
    assert not manquants, f"symboles absents du sprite : {sorted(manquants)}"


def test_chaque_sport_porte_sa_bande_et_son_filet() -> None:
    """Meme famille que le test du sprite, et meme raison : un sport ajoute sans
    sa teinte ne casse rien.

    `_board.html` ecrit `sportrow-{{ sport_key }}` : sans la regle en face, la
    ligne sort **sans bande et sans filet**, donc exactement comme une ligne de
    football sur un theme ou le vert ne se voit pas. La colonne « Sport » se
    videra de son sens sans qu'aucune page ne tombe.

    Les deux themes sont verifies parce qu'un token declare dans le seul `:root`
    ne manque pas : il retombe sur la valeur sombre, et le fond clair recoit une
    teinte pensee pour du noir. Le degat est une bande illisible, pas une
    erreur.
    """
    from myassistantbet.services.labels import SPORT_ICONS

    css = _stylesheet()
    debut = css.index("@media (prefers-color-scheme: light)")

    for sport in sorted(SPORT_ICONS):
        for token in (f"--sport-{sport}:", f"--sport-{sport}-soft:"):
            assert css.count(token) >= 2, f"{token} n'est pas declare dans les deux themes"
            assert token in css[debut:], f"{token} manque au theme clair"
        # La bande se reconnait a l'accolade qui suit la classe : `in css` sur
        # le seul nom de classe etait satisfait par la regle de filet, donc
        # l'assertion passait sans que la bande existe.
        assert re.search(rf"tr\.sportrow-{sport}\s*\{{", css), f"aucune bande de fond pour {sport}"
        assert f"tr.sportrow-{sport} td:first-child" in css, f"aucun filet pour {sport}"


# -- Shortlist : densite de contexte ----------------------------------------


def _lot_pauvre(settings: Settings) -> int:
    """Un match sans aucun contexte : son bloc ne portera que des cotes."""
    event_id = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    return board_service.toggle_selection(event_id, True, settings)


def test_la_shortlist_affiche_la_densite(client: TestClient, isolated_settings: Settings) -> None:
    session_id = _lot_pauvre(isolated_settings)

    page = " ".join(client.get(f"/session/{session_id}").text.split())

    assert "<th>Contexte</th>" in page
    # 26 : « Lieu » a cesse d'etre conditionnelle, et « Arbitre » l'a rejointe —
    # servie sur 209 des 210 matchs d'une saison de Conference League.
    assert "0/26" in page


def test_un_enrichissement_vide_est_annonce(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le match avait consomme son credit pour ne rien rapporter, et rien ne le
    signalait avant la generation du prompt. Il reste selectionnable — mais par
    choix explicite, pas par defaut."""
    session_id = _lot_pauvre(isolated_settings)

    page = " ".join(client.get(f"/session/{session_id}").text.split())

    assert "sans aucune ligne de contexte" in page
    assert "retirer du lot" in page


def test_la_shortlist_se_trie_et_se_filtre(client: TestClient, isolated_settings: Settings) -> None:
    session_id = _lot_pauvre(isolated_settings)

    response = client.get(
        f"/session/{session_id}/shortlist", params={"order": "density", "thin_only": "1"}
    )

    assert response.status_code == 200
    assert response.text.strip().startswith('<div id="shortlist">')
    assert "Lyon" in response.text, "le bloc pauvre passe le filtre"


def test_le_filtre_des_blocs_pauvres_ecarte_les_autres(migrated: Settings) -> None:
    session_id = _lot_pauvre(migrated)
    riche = save(
        build(
            "cycling",
            "Tour",
            "Étape 5",
            "",
            "2026-08-04",
            "14:00",
            "Pogacar 2.10",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    board_service.toggle_selection(riche, True, migrated)

    vue = session_service.build_view(session_id, migrated, thin_only=True)

    # Le cyclisme n'a pas de referentiel : il n'est jamais « pauvre », et le
    # retirer sur ce filtre serait un jugement plutot qu'une mesure.
    assert [event.sport_key for _, events in vue.groups for event in events] == ["football"]


# -- Marquer une rencontre non disputee ---------------------------------------


def _seed_tennis(settings: Settings, home: str, away: str, when: str) -> int:
    """Un match de tennis, dans un tournoi rattache."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=settings,
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, 'api', ?)",
        (competition["sport_id"], competition["id"], home, away, when, db.utcnow()),
        settings=settings,
    )
    return int(
        db.query_one(
            "SELECT id FROM events WHERE home = ? AND away = ?", (home, away), settings=settings
        )["id"]
    )


def test_marquer_un_match_non_dispute(client: TestClient, isolated_settings: Settings) -> None:
    """La seule source vivante d'un forfait : le fichier de resultats parait une
    fois par semaine et apres coup, donc toujours apres que le tournoi a cesse
    d'etre rendu."""
    event_id = _seed_tennis(isolated_settings, "Bencic", "Gauff", "2026-08-11T23:00:00Z")

    response = client.post(f"/events/{event_id}/unplayed", data={"outcome": "walkover"})

    assert response.status_code == 200
    assert "<html" not in response.text, "une route ciblee par HTMX rend le fragment"
    assert response.text.strip().startswith('<div id="event-context">')
    ligne = db.query_one(
        "SELECT match_outcome_type FROM events WHERE id = ?",
        (event_id,),
        settings=isolated_settings,
    )
    assert ligne["match_outcome_type"] == "walkover"


def test_un_etat_refuse_laisse_la_base_intacte(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Un marquage qui parait pose sans avoir d'effet serait le silence exact que
    cette colonne existe pour supprimer."""
    event_id = _seed_tennis(isolated_settings, "Bencic", "Gauff", "2026-08-11T23:00:00Z")

    response = client.post(f"/events/{event_id}/unplayed", data={"outcome": "annule"})

    assert response.status_code == 200
    assert "Etat inconnu" in response.text
    ligne = db.query_one(
        "SELECT match_outcome_type FROM events WHERE id = ?",
        (event_id,),
        settings=isolated_settings,
    )
    assert ligne["match_outcome_type"] is None


def test_le_choix_n_est_propose_qu_au_tennis(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Au football, un forfait arrive par le fournisseur de contexte et sort deja
    sur la ligne « Statut » : un second chemin, saisi a la main, pourrait le
    contredire."""
    foot = _seed_event(isolated_settings)
    tennis = _seed_tennis(isolated_settings, "Bencic", "Gauff", "2026-08-11T23:00:00Z")

    assert "Rencontre disputée ?" not in client.get(f"/events/{foot}").text
    assert "Rencontre disputée ?" in client.get(f"/events/{tennis}").text
