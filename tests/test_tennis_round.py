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


def test_une_population_qui_ne_forme_aucun_tableau_ne_nomme_aucun_tour(
    migrated: Settings,
) -> None:
    """**Le comptage ne decrit un tour que s'il decrit un tableau.**

    Cette regle-ci affirmait l'inverse : 42 joueurs vus et 20 matchs joues font
    22 en lice, donc « le tour a commence a 32, quel que soit le tableau reel ».
    C'est vrai d'un tableau unique vu en partie ; c'est faux des que la cle sert
    **deux** tableaux, et The Odds API sert les qualifications sous la cle du
    tournoi.

    Mesure du 12/08/2026, sur une soiree de qualifications a Cincinnati :
    34 joueurs vus a 11:05, 76 a 20:15 — aucun des deux n'est une taille de
    tableau — et le meme match a ete rendu « 16e de finale » puis « 32e de
    finale ». L'etiquette suivait l'avancement de nos scans.

    Le cout est assume et il faut le connaitre : un tableau unique vu en partie
    perd son tour lui aussi, alors que son compte etait juste. Les deux
    situations sont **indiscernables** d'ici, et le module a une regle pour ce
    cas — en cas de doute, rien."""
    competition_id = _match(migrated, "A", "B", "2026-08-08T12:00:00Z")
    for index in range(20):
        _match(migrated, f"P{index}", f"Q{index}", "2026-08-07T12:00:00Z")

    assert tennis_round.lines(competition_id, "2026-08-08T12:00:00Z", migrated) == [
        ("Tour", "phase non renseignee (42 joueurs vus ne forment aucun tableau)")
    ]


def test_le_meme_match_ne_change_pas_de_tour_quand_le_scan_avance(migrated: Settings) -> None:
    """**Test de stabilite**, et c'est le defaut qui l'a fait ecrire.

    Shevchenko - O'Connell etait « 16e de finale » dans le lot de 13:05 et
    « 32e de finale » dans celui de 22:15 : meme match, meme tour reel, deux
    etiquettes. Entre les deux, le scan avait vu 42 joueurs de plus, et
    l'etiquette suivait notre avancement plutot que le tournoi.

    Les deux populations reelles sont reproduites ici — 34 joueurs puis 76 — et
    **aucune ne forme un tableau** : le module se tait aux deux instants au lieu
    de nommer deux tours differents.

    Ce que le test **n'exige pas** : que le compte entre parentheses ne bouge
    pas. Il decrit nos scans, pas le match, et le voir grandir est une
    information juste."""
    competition_id = _match(migrated, "A", "B", "2026-08-08T12:00:00Z")
    for index in range(16):
        _match(migrated, f"P{index}", f"Q{index}", "2026-08-07T12:00:00Z")
    tot = tennis_round.lines(competition_id, "2026-08-08T12:00:00Z", migrated)

    for index in range(16, 37):
        _match(migrated, f"R{index}", f"S{index}", "2026-08-09T12:00:00Z")
    tard = tennis_round.lines(competition_id, "2026-08-08T12:00:00Z", migrated)

    tours = [valeur.split(" (")[0] for _, valeur in tot + tard]
    assert tours == [tennis_round.UNSET_ROUND] * 2, tot + tard
    assert "34 joueurs" in tot[0][1] and "76 joueurs" in tard[0][1], "les deux populations reelles"


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


def _vus(settings: Settings, joueurs: int) -> int:
    """`joueurs` joueurs distincts vus dans ce tournoi, avant le match analyse."""
    noms = [f"J{index}" for index in range(joueurs)]
    competition_id = 0
    for index in range(0, len(noms) - 1, 2):
        competition_id = _match(settings, noms[index], noms[index + 1], "2026-08-07T12:00:00Z")
    if len(noms) % 2:
        # Un joueur revu au tour suivant : c'est ce qui rend le compte impair.
        competition_id = _match(settings, noms[-1], noms[0], "2026-08-07T13:00:00Z")
    return competition_id


def test_une_vue_tronquee_se_declare_quand_elle_est_prouvable(migrated: Settings) -> None:
    """Le seul signal sur : le nombre de joueurs vus n'est pas une taille de
    tableau qui existe. Mesure en reel — le Canadian Open masculin en a montre
    **79**, ce qui ne forme aucun tableau, donc ses premieres journees precedent
    notre fenetre de scan.

    Le **nombre** de tours manquants, lui, n'est pas derivable : il demanderait
    la taille du tableau, et c'est la meme raison qui fait que ce module ne nomme
    jamais « 2e tour ». D'ou un booleen.
    """
    competition_id = _vus(migrated, 79)

    assert tennis_round.truncated(competition_id, "2026-08-10T22:00:00Z", migrated) is True


def test_une_vue_complete_ne_declare_rien(migrated: Settings) -> None:
    """**Faux negatif assume**, et c'est la meme limite qu'ailleurs : le tableau
    feminin du meme tournoi n'a montre que 64 joueuses sur 96, et 64 etant une
    taille de tableau valide, rien ne permet de le savoir. Un silence vaut mieux
    qu'une affirmation fausse, c'est la regle du module."""
    competition_id = _vus(migrated, 64)

    assert tennis_round.truncated(competition_id, "2026-08-10T22:00:00Z", migrated) is False


def test_un_tournoi_inconnu_ne_declare_rien(migrated: Settings) -> None:
    """Sans competition, il n'y a pas de vue du tout — donc rien a declarer."""
    assert tennis_round.truncated(None, "2026-08-10T22:00:00Z", migrated) is False
