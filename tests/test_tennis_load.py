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

    assert lignes == [("Repos", "Fils 2j | Navone 3j")]


def test_le_nombre_de_tours_n_accompagne_plus_le_repos(migrated: Settings) -> None:
    """Il comptait les apparitions **scannees**, pas les matchs joues. Sur un
    tournoi dont les premiers jours precedent notre fenetre, il en manque :
    constate en reel, le bloc creditait Michelsen d'un tour la ou l'ATP lui en
    donne deux. La ligne « Tour » dit desormais ou en est le tournoi, et elle le
    dit juste — ce compte-la n'avait plus de raison d'etre.
    """
    _match(migrated, "Fils", "A", "2026-08-03T18:00:00Z")
    _match(migrated, "B", "Fils", "2026-08-05T18:00:00Z")

    lignes = tennis_load.lines(
        "Fils", "Inconnu", _competition(migrated), "2026-08-07T18:00:00Z", migrated
    )

    assert lignes == [("Repos", "Fils 2j")]


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


def test_le_repos_se_compte_en_journees_de_tournoi(migrated: Settings) -> None:
    """Le defaut constate en reel sur Montreal. Un match de la session du soir
    part a 01h du matin a Paris : sa date civile est celle du lendemain, et le
    repos calcule dessus perdait un jour d'un cote et en gagnait un de l'autre.

    Le bloc donnait van de Zandschulp a 1j et Paul a 3j la ou l'ATP date leurs
    deux matchs precedents du meme mercredi. Regroupes en journees de tournoi,
    les deux tombent sur le meme compte.
    """
    # Session du soir du 5 aout a Montreal : 23h10 UTC, soit 01h10 a Paris le 6.
    _match(migrated, "Paul", "Royer", "2026-08-05T23:10:00Z")
    # Session du soir du 6 aout : 00h10 UTC le 7, soit 02h10 a Paris.
    _match(migrated, "Zandschulp", "Medvedev", "2026-08-06T00:10:00Z")

    # Les deux jouent la session du soir du 7 aout, soit 00h10 UTC le 8.
    repos = {
        nom: tennis_load.load_for(nom, _competition(migrated), "2026-08-08T00:10:00Z", migrated)
        for nom in ("Paul", "Zandschulp")
    }

    # Les deux ont joue la meme session du soir, a deux journees de tournoi de
    # celle-ci. En dates civiles, l'un donnait 3 jours et l'autre 2.
    assert repos["Paul"].days_rest == 2
    assert repos["Zandschulp"].days_rest == 2


def test_le_parcours_dit_depuis_quand_il_voit(migrated: Settings) -> None:
    """La liste se lisait comme un parcours complet, et elle ne l'est pas : un
    tournoi commence avant notre fenetre de scan a des premiers tours que nous
    n'avons jamais vus.

    Constate en reel — le « Parcours » de Norrie omettait son premier tour contre
    Ugo Carabelli, joue la veille du premier jour scanne, et seule une recherche
    exterieure l'a rattrape. La date rend le trou visible : comparee a « Tour »,
    elle dit tout de suite si le debut du tableau manque.
    """
    _match(migrated, "Norrie", "Buse", "2026-08-05T18:00:00Z")
    _match(migrated, "Norrie", "de Minaur", "2026-08-06T18:00:00Z")

    lignes = dict(
        tennis_load.path_lines(
            "Norrie", "Fils", _competition(migrated), "2026-08-08T23:10:00Z", None, migrated
        )
    )

    assert lignes["Parcours"].endswith("[vu depuis le 05/08]")
