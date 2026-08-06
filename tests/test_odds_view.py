"""Fiche evenement : toutes les cotes, sans rien perdre ni rien inventer."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import context, odds_view


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _event(settings: Settings, sport: str = "tennis", away: str = "Bergs") -> int:
    row = db.query_one(f"SELECT id FROM sports WHERE key = '{sport}'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Moutet', ?, '2026-08-04T15:00:00Z', 'oddsapi', ?)",
        (row["id"], away, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _odd(
    settings: Settings,
    event_id: int,
    market: str,
    name: str,
    price: float,
    point: float | None = None,
    bookmaker: str = "betclic_fr",
) -> None:
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, point, price, "
        "                  fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
        (event_id, bookmaker, market, name, point, price, db.utcnow()),
        settings=settings,
    )


def test_evenement_inconnu_renvoie_none(migrated: Settings) -> None:
    assert odds_view.build(999_999, migrated) is None


def test_evenement_sans_cote_est_vide(migrated: Settings) -> None:
    view = odds_view.build(_event(migrated), migrated)

    assert view is not None
    assert view.is_empty
    assert view.market_count == 0
    assert view.enriched is False


def test_les_marches_suivent_l_ordre_du_sport(migrated: Settings) -> None:
    """Le tennis parle en sets et en jeux : l'ordre est celui du rendu compact."""
    event_id = _event(migrated)
    # Insere volontairement dans le desordre.
    _odd(migrated, event_id, "totals", "Over", 1.90, 22.5)
    _odd(migrated, event_id, "h2h_s1", "Moutet", 1.95)
    _odd(migrated, event_id, "h2h", "Moutet", 1.85)

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert [block.key for block in view.blocks] == ["h2h", "totals", "h2h_s1"]
    assert [block.label for block in view.blocks] == ["Vainqueur", "Jeux O/U", "Set 1"]


def test_un_marche_non_modelise_est_rendu_brut(migrated: Settings) -> None:
    """Paye mais inconnu du rendu : le montrer brut plutot que le perdre."""
    event_id = _event(migrated)
    _odd(migrated, event_id, "h2h", "Moutet", 1.85)
    _odd(migrated, event_id, "marche_exotique", "Quelque chose", 3.40)

    view = odds_view.build(event_id, migrated)

    assert view is not None
    exotic = [block for block in view.blocks if block.key == "marche_exotique"]
    assert len(exotic) == 1
    assert exotic[0].unmodelled is True
    assert exotic[0].label == "marche_exotique"
    # Il passe apres les marches connus, jamais devant.
    assert view.blocks[-1].key == "marche_exotique"


def test_les_lignes_sont_triees_par_point_croissant(migrated: Settings) -> None:
    event_id = _event(migrated)
    for point in (24.5, 21.5, 22.5):
        _odd(migrated, event_id, "totals", "Over", 1.90, point)

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert [outcome.point for outcome in view.blocks[0].outcomes] == [21.5, 22.5, 24.5]


def test_l_ordre_du_fournisseur_est_conserve_sans_ligne(migrated: Settings) -> None:
    """Domicile, nul, exterieur : cet ordre porte un sens, on ne le trie pas."""
    event_id = _event(migrated, sport="football", away="Nice")
    for name, price in (("Lyon", 2.10), ("Match nul", 3.40), ("Nice", 3.20)):
        _odd(migrated, event_id, "h2h", name, price)

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert [outcome.name for outcome in view.blocks[0].outcomes] == ["Lyon", "Match nul", "Nice"]


def test_un_evenement_non_enrichi_est_signale(migrated: Settings) -> None:
    """h2h et totals seuls : c'est l'etage A, pas un enrichissement."""
    event_id = _event(migrated)
    _odd(migrated, event_id, "h2h", "Moutet", 1.85)
    _odd(migrated, event_id, "totals", "Over", 1.90, 22.5)

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert view.enriched is False
    assert view.deep_market_count == 0

    _odd(migrated, event_id, "h2h_s1", "Moutet", 1.95)
    enriched = odds_view.build(event_id, migrated)
    assert enriched is not None
    assert enriched.enriched is True


def test_une_saisie_manuelle_n_affiche_aucun_horodatage(migrated: Settings) -> None:
    """L'heure d'une saisie est celle de la frappe, pas celle d'un releve."""
    event_id = _event(migrated)
    _odd(migrated, event_id, "outright", "Moutet", 1.85, bookmaker="manual")

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert view.fetched_local is None
    assert view.bookmakers == ["saisie manuelle"]


def test_le_releve_d_api_reste_horodate_malgre_une_saisie(migrated: Settings) -> None:
    event_id = _event(migrated)
    _odd(migrated, event_id, "h2h", "Moutet", 1.85)
    _odd(migrated, event_id, "outright", "Set 3", 1.95, bookmaker="manual")

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert view.fetched_local is not None
    assert view.bookmakers == ["Betclic", "saisie manuelle"]


def test_l_etape_sans_adversaire_n_a_pas_de_tiret_orphelin(migrated: Settings) -> None:
    event_id = _event(migrated, sport="cycling", away="")

    view = odds_view.build(event_id, migrated)

    assert view is not None
    assert view.affiche == "Moutet"


# -- Routes -----------------------------------------------------------------


def test_la_fiche_repond(client: TestClient, isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    event_id = _event(isolated_settings)
    _odd(isolated_settings, event_id, "h2h", "Moutet", 1.85)

    response = client.get(f"/events/{event_id}")

    assert response.status_code == 200
    assert "Moutet – Bergs" in response.text
    assert "1.85" in response.text


def test_une_fiche_inconnue_renvoie_404(client: TestClient, isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)

    assert client.get("/events/999999").status_code == 404


def test_la_fiche_montre_le_contexte_sportif(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Sans ce bloc, tout ce qui est recupere — classement, forme, absents,
    profil corners — n'existait que dans le prompt genere, donc invisible tant
    qu'une session n'avait pas ete montee."""
    event_id = _event(isolated_settings)
    context.store(
        event_id,
        context.KIND_STANDINGS,
        {"home": {"rank": 3, "points": 40, "played": 20}, "away": None},
        isolated_settings,
    )

    page = client.get(f"/events/{event_id}").text

    assert "Contexte sportif" in page
    assert "Classement" in page


def test_la_fiche_annonce_un_contexte_absent_sans_le_taire(
    client: TestClient, isolated_settings: Settings
) -> None:
    event_id = _event(isolated_settings)

    page = client.get(f"/events/{event_id}").text

    assert "Aucun contexte récupéré" in page


def test_la_saisie_manuelle_est_repliee(client: TestClient, isolated_settings: Settings) -> None:
    """Elle ne sert qu'une fois sur une fiche : dépliée, elle poussait les cotes
    et le contexte hors de l'écran. Même convention que les panneaux du board."""
    event_id = _event(isolated_settings)

    page = client.get(f"/events/{event_id}").text

    assert '<details class="panel manual-odds"' in page
    assert "<summary>Ajouter des cotes à la main</summary>" in page


def test_une_saisie_refusee_laisse_le_panneau_ouvert(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Sinon le texte retapé disparaîtrait de la vue avec le message d'erreur."""
    event_id = _event(isolated_settings)

    page = client.post(f"/events/{event_id}/odds", data={"odds": "ligne illisible"}).text

    assert 'class="panel manual-odds" open' in page


def test_chaque_ligne_de_contexte_porte_son_pictogramme(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Décoratif : le libellé reste écrit à côté, et le prompt généré n'en porte
    aucun — il compte ses tokens."""
    event_id = _event(isolated_settings)
    context.store(
        event_id,
        context.KIND_STANDINGS,
        {"home": {"rank": 3, "points": 40, "played": 20}, "away": None},
        isolated_settings,
    )

    page = client.get(f"/events/{event_id}").text

    assert "🏆" in page
    assert "Classement" in page
