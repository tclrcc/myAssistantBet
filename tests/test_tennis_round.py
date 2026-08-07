"""Le tour d'un match de tennis, deduit du nombre de joueurs encore en lice."""

from __future__ import annotations

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import tennis_round

TOURNOI = "tennis_atp_us_open"


def _edition(players: int, played: int) -> tennis_round.Edition:
    """Une edition vue avec `players` joueurs distincts et `played` matchs joues.

    Les joueurs qui n'apparaissent pas dans les matchs deja joues entrent par
    des matchs a venir : la base porte le tournoi entier, pas seulement son
    passe, et c'est bien ce qui rend le total des joueurs lisible des le
    premier jour.
    """
    noms = [f"J{index}" for index in range(players)]
    avant = [(noms[(2 * i) % players], noms[(2 * i + 1) % players]) for i in range(played)]
    vus = {nom for paire in avant for nom in paire}
    restants = [nom for nom in noms if nom not in vus]
    apres = [
        (restants[i], restants[i + 1] if i + 1 < len(restants) else noms[0])
        for i in range(0, len(restants), 2)
    ]

    matches = tuple(
        [(f"2026-08-07T{i % 24:02d}:{i % 60:02d}:00Z", a, b) for i, (a, b) in enumerate(avant)]
        + [(f"2026-08-08T18:{i % 60:02d}:00Z", a, b) for i, (a, b) in enumerate(apres)]
    )
    edition = tennis_round.Edition(matches=matches)
    assert edition.players == players, "construction du test : tous les joueurs doivent apparaitre"
    return edition


def test_les_derniers_tours_se_nomment_depuis_la_fin() -> None:
    """Deux joueurs restants sont une finale, quatre une demi-finale : le nom
    ne demande aucune connaissance de la taille du tableau."""
    assert tennis_round.label_for(_edition(64, 62), "2026-08-08T12:00:00Z") == "finale"
    assert tennis_round.label_for(_edition(64, 60), "2026-08-08T12:00:00Z") == "demi-finale"
    assert tennis_round.label_for(_edition(64, 56), "2026-08-08T12:00:00Z") == "quart de finale"
    assert tennis_round.label_for(_edition(64, 48), "2026-08-08T12:00:00Z") == "huitième de finale"


def test_un_tour_deja_entame_garde_son_nom() -> None:
    """Quatre joueurs moins une demi-finale deja jouee en laissent trois. Sans
    l'arrondi a la puissance de deux superieure, le second match de la soiree
    perdrait son libelle — ou pire, prendrait celui du tour suivant."""
    assert tennis_round.label_for(_edition(64, 61), "2026-08-08T12:00:00Z") == "demi-finale"
    assert tennis_round.label_for(_edition(64, 57), "2026-08-08T12:00:00Z") == "quart de finale"


def test_plus_de_matchs_que_de_joueurs_ne_produit_rien() -> None:
    """Une vue incoherente ne doit rien affirmer du tout."""
    assert tennis_round.label_for(_edition(64, 63), "2026-08-08T12:00:00Z") is None


def test_deux_editions_du_meme_tournoi_ne_se_melangent_pas() -> None:
    """La competition garde son identifiant d'une annee sur l'autre : sans la
    coupure, les joueurs de l'edition precedente gonfleraient le compte et le
    tour serait faux sur toute la semaine."""
    matches = [
        {"home": "A", "away": "B", "commence_time": "2025-08-05T12:00:00Z"},
        {"home": "C", "away": "D", "commence_time": "2025-08-06T12:00:00Z"},
        {"home": "E", "away": "F", "commence_time": "2026-08-05T12:00:00Z"},
    ]

    edition = tennis_round.edition_for(matches, "2026-08-05T12:00:00Z")

    assert edition.players == 2
    assert len(edition.matches) == 1


def _match(settings: Settings, home: str, away: str, when: str) -> int:
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (TOURNOI,), settings=settings
    )
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, 'api', ?)",
        (sport["id"], competition["id"], home, away, when, db.utcnow()),
        settings=settings,
    )
    return int(competition["id"])


def test_la_ligne_du_bloc_se_relit_en_base(migrated: Settings) -> None:
    """Quatre joueurs vus, deux matchs simultanes : ce sont les demi-finales.
    Les matchs simultanes ne se comptent pas les uns les autres."""
    competition_id = _match(migrated, "A", "B", "2026-08-08T12:00:00Z")
    _match(migrated, "C", "D", "2026-08-08T12:00:00Z")

    lignes = tennis_round.lines(competition_id, "2026-08-08T12:00:00Z", migrated)

    assert lignes == [("Tour", "demi-finale")]


def test_une_vue_partielle_nomme_quand_meme_le_tour(migrated: Settings) -> None:
    """42 joueurs vus et 20 matchs joues font 22 en lice : le tour a donc
    commence a 32, et c'est vrai quel que soit le tableau reel. L'ancienne regle
    se taisait ici parce que 42 n'est la taille d'aucun tableau — elle cherchait
    le debut. Compte depuis la fin, la question ne se pose plus.

    Limite connue et assumee : un tournoi dont **un seul** match a ete scanne
    rend « finale », deux joueurs en lice etant indiscernables d'une finale. Le
    cas suppose de n'avoir jamais scanne le tournoi avant ce match.
    """
    competition_id = _match(migrated, "A", "B", "2026-08-08T12:00:00Z")
    for index in range(20):
        _match(migrated, f"P{index}", f"Q{index}", "2026-08-07T12:00:00Z")

    assert tennis_round.lines(competition_id, "2026-08-08T12:00:00Z", migrated) == [
        ("Tour", "16e de finale")
    ]


def test_tous_les_tours_se_nomment_depuis_la_fin() -> None:
    """L'ordinal a ete essaye et retire : « 2e tour » exige de savoir ou est le
    premier, donc la taille du tableau. Compter depuis la fin ne suppose que le
    nombre de joueurs restants, qui reste juste sur une vue tronquee."""
    attendus = [
        (62, "finale"),
        (60, "demi-finale"),
        (56, "quart de finale"),
        (48, "huitième de finale"),
        (32, "16e de finale"),
        (0, "32e de finale"),
    ]
    for joues, attendu in attendus:
        assert tennis_round.label_for(_edition(64, joues), "2026-08-08T12:00:00Z") == attendu


def test_une_vue_tronquee_nomme_juste_le_tour_qu_elle_voit() -> None:
    """Le defaut qui a motive le retrait de l'ordinal, constate en reel : sur le
    Canadian Open feminin, 64 joueuses vues sur un tableau de 96 — le premier
    tour n'ayant jamais ete scanne. Huit blocs ont annonce « 2e tour » pour un
    tour de 32.

    Compte depuis la fin, le meme etat rend « 16e de finale », ce que l'ordre du
    jeu officiel appelle le 3e tour. Les deux nomment le meme tour ; seul le
    second est vrai sans connaitre le tableau.
    """
    assert tennis_round.label_for(_edition(64, 40), "2026-08-08T12:00:00Z") == "16e de finale"


def test_un_tour_qui_demanderait_plus_de_joueurs_que_vus_est_refuse() -> None:
    """Un tableau a exemptions vu des son premier tour : 96 joueurs ne forment
    pas un tour de 128, et arrondir a la puissance de deux superieure le
    pretendrait."""
    assert tennis_round.label_for(_edition(96, 0), "2026-08-08T12:00:00Z") is None
    # Des que les exemptions sont purgees, le compte redevient une puissance de
    # deux et le tour se nomme.
    assert tennis_round.label_for(_edition(96, 32), "2026-08-08T12:00:00Z") == "32e de finale"
