"""Repos et charge d'un joueur, calcules sur nos propres lignes."""

from __future__ import annotations

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import tennis_load

TOURNOI = "tennis_atp_us_open"


def _competition(settings: Settings) -> int:
    row = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (TOURNOI,), settings=settings
    )
    return int(row["id"])


def _match(settings: Settings, home: str, away: str, when: str) -> None:
    competition_id = _competition(settings)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, 'api', ?)",
        (sport["id"], competition_id, home, away, when, db.utcnow()),
        settings=settings,
    )


def test_le_repos_se_compte_sur_les_tours_precedents(migrated: Settings) -> None:
    """L'information dormait deja en base : les tours precedents du meme
    tournoi ont ete scannes les jours d'avant. L'analyse allait la chercher a
    la main, match par match."""
    _match(migrated, "Fils", "Svajda", "2026-08-05T18:00:00Z")
    _match(migrated, "Navone", "Vacherot", "2026-08-04T18:00:00Z")

    lignes = tennis_load.lines(
        "Fils", "Navone", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes == [("Repos", "Fils 2j (1 tour) | Navone 3j (1 tour)")]


def test_le_nombre_de_tours_accompagne_le_repos(migrated: Settings) -> None:
    """Deux jours apres un premier tour et deux jours apres un quart ne se
    valent pas."""
    _match(migrated, "Fils", "A", "2026-08-03T18:00:00Z")
    _match(migrated, "B", "Fils", "2026-08-05T18:00:00Z")

    lignes = tennis_load.lines(
        "Fils", "Inconnu", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes == [("Repos", "Fils 2j (2 tours)")]


def test_un_joueur_sans_tour_precedent_ne_produit_rien(migrated: Settings) -> None:
    """Ecrire « 0 tour » laisserait croire a une entree en lice alors qu'on ne
    sait simplement pas : le tournoi peut n'avoir ete scanne que ce jour-la."""
    assert (
        tennis_load.lines(
            "Inconnu", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
        )
        == []
    )


def test_un_autre_tournoi_ne_compte_pas(migrated: Settings) -> None:
    """La charge se mesure dans l'epreuve en cours, pas sur la saison."""
    autre = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_wta_us_open'", settings=migrated
    )
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, 'Fils', 'X', '2026-08-05T18:00:00Z', 'api', ?)",
        (sport["id"], int(autre["id"]), db.utcnow()),
        settings=migrated,
    )

    assert (
        tennis_load.lines("Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated)
        == []
    )


def test_un_match_trop_ancien_ne_compte_pas(migrated: Settings) -> None:
    """Au-dela de dix jours, c'est une autre semaine : le repos ne dit plus
    rien de la fraicheur."""
    _match(migrated, "Fils", "A", "2026-07-01T18:00:00Z")

    assert (
        tennis_load.lines("Fils", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated)
        == []
    )


def test_les_accents_et_la_casse_ne_separent_pas_un_joueur(migrated: Settings) -> None:
    """Le fournisseur ecrit le meme joueur de la meme facon d'un tour a
    l'autre, mais la casse peut varier. Aucun rapprochement flou en revanche :
    deux joueurs differents ne doivent jamais partager un parcours."""
    _match(migrated, "Fabian Marozsán", "A", "2026-08-05T18:00:00Z")

    lignes = tennis_load.lines(
        "Fabian Marozsan", "Autre", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes and "Fabian Marozsan 2j" in lignes[0][1]
