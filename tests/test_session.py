"""Un match qui a commence quitte le prompt, mais pas la session.

Le board ne montre que la fenetre a venir : une fois l'heure passee, un match
coche disparait de l'ecran ou il aurait pu etre decoche. Sans regle explicite,
il continuerait a etre compte, enrichi et analyse.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import coverage, labels
from myassistantbet.services import session as session_service
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt
from myassistantbet.services.render import unserved_note
from myassistantbet.services.session import render_blocks

from .helpers import NOW

#: NOW vaut 10h00 UTC : ces deux bornes encadrent l'instant de reference.
COMMENCE_PASSE = "2026-08-03T08:00:00Z"
COMMENCE_A_VENIR = "2026-08-03T18:00:00Z"


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, home: str, away: str, commence: str) -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, ?, ?, ?, 'oddsapi', ?)",
        (sport["id"], home, away, commence, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _selection(settings: Settings) -> tuple[int, int, int]:
    """Une session avec un match deja commence et un match a venir."""
    passe = _match(settings, "Moutet", "Bergs", COMMENCE_PASSE)
    a_venir = _match(settings, "Fils", "Rune", COMMENCE_A_VENIR)
    session_id = 0
    for event_id in (passe, a_venir):
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, passe, a_venir


# -- La regle ---------------------------------------------------------------


def test_le_coup_d_envoi_fait_basculer() -> None:
    """A l'heure pile, il n'y a plus de pari avant-match a placer."""
    assert session_service.has_started("2026-08-03T10:00:00Z", NOW) is True
    assert session_service.has_started("2026-08-03T10:00:01Z", NOW) is False


def test_une_date_illisible_n_ecarte_pas_le_match() -> None:
    assert session_service.has_started("pas une date", NOW) is False


# -- Prompt -----------------------------------------------------------------


def test_un_match_commence_sort_du_prompt(migrated: Settings) -> None:
    session_id, _, _ = _selection(migrated)

    blocks = session_service.render_blocks(session_id, migrated, NOW)

    assert len(blocks) == 1
    assert "Fils – Rune" in blocks[0]
    assert "Moutet" not in blocks[0]
    assert blocks[0].startswith("### M1"), "la numerotation ne laisse pas de trou"


def test_le_prompt_nomme_ce_qu_il_a_ecarte(migrated: Settings) -> None:
    session_id, _, _ = _selection(migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 1
    assert prompt.started == ["Moutet – Bergs"]


# -- Shortlist et compteurs -------------------------------------------------


def test_le_match_commence_reste_dans_la_session(migrated: Settings) -> None:
    """L'historique des picks s'appuie dessus : on ne le supprime jamais tout seul."""
    session_id, _, _ = _selection(migrated)

    view = session_service.build_view(session_id, migrated, NOW)

    assert view.count == 2
    assert view.upcoming == 1
    assert view.started_count == 1
    assert [event.started for _, events in view.groups for event in events] == [True, False]
    assert len(db.query("SELECT * FROM session_events", settings=migrated)) == 2


def test_le_bandeau_ne_compte_que_les_matchs_a_venir(migrated: Settings) -> None:
    _selection(migrated)

    banner = board_service.banner(migrated, NOW)

    assert banner.selected_count == 1
    assert banner.started_count == 1, "le match commence est dit a part, jamais tu"


# -- Retrait manuel ---------------------------------------------------------


def test_retirer_un_match_de_la_shortlist(client: TestClient, isolated_settings: Settings) -> None:
    """Le board ne montre plus un match commence : la shortlist doit pouvoir le rendre."""
    session_id, passe, a_venir = _selection(isolated_settings)

    response = client.post(f"/session/{session_id}/events/{passe}/remove")

    assert response.status_code == 200
    restants = db.query("SELECT event_id FROM session_events", settings=isolated_settings)
    assert [int(row["event_id"]) for row in restants] == [a_venir]


def test_retrait_sur_une_session_inconnue(client: TestClient, isolated_settings: Settings) -> None:
    _, passe, _ = _selection(isolated_settings)

    assert client.post(f"/session/999/events/{passe}/remove").status_code == 404


# -- Provenance et marches abandonnes ---------------------------------------
#
# Le bloc doit dire d'ou vient chaque cote et ce que l'API n'a jamais servi :
# sans cela, une cote de reference passe pour jouable et une absence definitive
# passe pour un trou de collecte.


def _match_de_competition(settings: Settings, key: str = "tennis_atp_us_open") -> tuple[int, int]:
    """Un match a venir rattache a une competition d'API."""
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (key,), settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) "
        "VALUES (?, ?, 'evt-1', 'Fils', 'Rune', ?, 'oddsapi', ?)",
        (sport["id"], competition["id"], COMMENCE_A_VENIR, db.utcnow()),
        settings=settings,
    )
    event_id = int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])
    return event_id, int(competition["id"])


def _cote(
    settings: Settings, event_id: int, book: str, market: str, name: str, price: float
) -> None:
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, point, price, "
        "fetched_at) VALUES (?, ?, ?, ?, ?, ?, '2026-08-03T12:08:00Z')",
        (event_id, book, market, name, -3.5 if market == "spreads" else None, price),
        settings=settings,
    )


def test_le_bloc_distingue_la_cote_jouable_de_la_cote_de_reference(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, _ = _match_de_competition(isolated_settings)
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    _cote(isolated_settings, event_id, "pinnacle", "spreads", "Fils", 1.84)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert "MARCHES (Betclic, releve" in block, "l'en-tete ne nomme que la source jouable"
    assert "  Vainqueur   1.81" in block
    assert "[Pinnacle (ref.)]" in block.split("Hand. jeux")[1].splitlines()[0]


def test_le_bloc_enumere_les_marches_que_l_api_ne_sert_pas(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, competition_id = _match_de_competition(isolated_settings)
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(
            competition_id,
            ("h2h", "spreads", "h2h_s1"),
            {"bookmakers": [{"key": "betclic_fr", "markets": [{"key": "h2h"}]}]},
            isolated_settings,
        )

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert f"  Non servis  Hand. jeux, Set 1 — {unserved_note()}" in block


def test_un_marche_abandonne_ailleurs_ne_deteint_pas_sur_ce_match(
    isolated_settings: Settings,
) -> None:
    db.run_migrations(isolated_settings)
    event_id, _ = _match_de_competition(isolated_settings)
    autre = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_wta_us_open'",
        settings=isolated_settings,
    )
    _cote(isolated_settings, event_id, "betclic_fr", "h2h", "Fils", 1.81)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for _ in range(coverage.GIVE_UP_AFTER):
        coverage.record(
            int(autre["id"]),
            ("h2h", "spreads"),
            {"bookmakers": [{"key": "betclic_fr", "markets": [{"key": "h2h"}]}]},
            isolated_settings,
        )

    block = session_service.render_blocks(session_id, isolated_settings, NOW)[0]

    assert "Non servis" not in block


# -- Non servis : la couverture est par competition, le service par match -----


def _tennis_event(settings: Settings, home: str, away: str) -> tuple[int, int]:
    """Un match de tennis dans une session, sans aucune cote."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'",
        settings=settings,
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, '2026-08-07T18:00:00Z', 'api', ?)",
        (competition["sport_id"], competition["id"], home, away, db.utcnow()),
        settings=settings,
    )
    event = db.query_one("SELECT id FROM events WHERE home = ?", (home,), settings=settings)
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('x', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    session = db.query_one("SELECT id FROM sessions ORDER BY id DESC LIMIT 1", settings=settings)
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
        (session["id"], event["id"]),
        settings=settings,
    )
    return int(session["id"]), int(event["id"])


def _odds(settings: Settings, event_id: int, market: str, name: str = "A") -> None:
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
        "VALUES (?, 'betclic_fr', ?, ?, 1.90, ?)",
        (event_id, market, name, db.utcnow()),
        settings=settings,
    )


def test_un_marche_demande_et_absent_sur_ce_match_est_annonce(migrated: Settings) -> None:
    """`coverage` raisonne par competition quand le service se fait par match :
    un handicap jeux servi sur une affiche et pas sur l'autre produisait un
    silence sur la seconde, indiscernable d'un marche jamais reclame."""
    session_id, event_id = _tennis_event(migrated, "Fils", "Navone")
    _odds(migrated, event_id, "h2h", "Fils")
    _odds(migrated, event_id, "totals", "Over")
    _odds(migrated, event_id, "h2h_s1", "Fils")  # marche profond : l'etage B a tourne

    bloc = render_blocks(session_id, migrated, now=NOW)[0]

    assert "Non servis" in bloc
    assert "Hand. jeux" in bloc, "le handicap demande et non revenu est nomme"


def test_un_evenement_d_etage_a_n_annonce_pas_les_marches_profonds(
    migrated: Settings,
) -> None:
    """Ils n'ont pas ete reclames : les lister ferait chercher une panne la ou
    il n'y a qu'un enrichissement jamais lance."""
    session_id, event_id = _tennis_event(migrated, "Fils", "Navone")
    _odds(migrated, event_id, "h2h", "Fils")
    _odds(migrated, event_id, "totals", "Over")

    bloc = render_blocks(session_id, migrated, now=NOW)[0]

    assert "Non servis" not in bloc


# -- Generer un prompt par competition ----------------------------------------


def test_le_prompt_se_restreint_a_une_competition(migrated: Settings) -> None:
    """Sur une soiree a trente matchs la recherche par match s'etiole, et
    decocher pour scinder ferait perdre le rattachement des picks."""
    session_id, _ = _tennis_event(migrated, "Fils", "Navone")
    autre = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE oddsapi_key = 'tennis_wta_us_open'",
        settings=migrated,
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, 'Swiatek', 'Golubic', '2026-08-07T19:00:00Z', 'api', ?)",
        (autre["sport_id"], autre["id"], db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT id FROM events WHERE home = 'Swiatek'", settings=migrated)
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
        (session_id, event["id"]),
        settings=migrated,
    )

    complet = build_prompt(session_id, settings=migrated, now=NOW)
    restreint = build_prompt(
        session_id, settings=migrated, now=NOW, competition_id=int(autre["id"])
    )

    assert complet.blocks == 2
    assert restreint.blocks == 1
    assert "Swiatek" in restreint.body
    assert "Navone" not in restreint.body


def test_les_competitions_de_la_session_sont_listees(migrated: Settings) -> None:
    """Le selecteur ne propose que ce que la session contient, avec le compte
    qui permet de juger si un lot merite d'etre coupe."""
    session_id, _ = _tennis_event(migrated, "Fils", "Navone")

    lots = session_service.competitions_of(session_id, migrated)

    assert [(lot["label"], lot["total"]) for lot in lots] == [("ATP — US Open", 1)]


def _football_event(settings: Settings, home: str, away: str) -> tuple[int, int]:
    """Un match de football dans une session, sans aucune cote."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE oddsapi_key = 'soccer_france_ligue_one'",
        settings=settings,
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) "
        "VALUES (?, ?, 'evt-foot', ?, ?, '2026-08-07T18:00:00Z', 'api', ?)",
        (competition["sport_id"], competition["id"], home, away, db.utcnow()),
        settings=settings,
    )
    event = db.query_one("SELECT id FROM events WHERE home = ?", (home,), settings=settings)
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('x', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    session = db.query_one("SELECT id FROM sessions ORDER BY id DESC LIMIT 1", settings=settings)
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
        (session["id"], event["id"]),
        settings=settings,
    )
    return int(session["id"]), int(event["id"])


def _unserved(bloc: str) -> list[str]:
    """Marches nommes par la ligne « Non servis », un par un.

    Le rapprochement se fait sur le libelle **entier** : chercher « 1N2 » dans
    la ligne le trouverait dans « Corners 1N2 », et le test passerait pour de
    mauvaises raisons.
    """
    corps = bloc.split("Non servis", 1)[-1].split("—")[0]
    return [item.strip() for item in corps.split(",") if item.strip()]


def test_le_1n2_absent_au_football_est_annonce(migrated: Settings) -> None:
    """Le silence qui a motive le correctif, constate en reel sur Beijing FC -
    Shenzhen Peng City.

    Le 1N2 vient de l'etage A, chez le book principal seul. Quand celui-ci ne
    sert pas la competition — Super League chinoise, Veikkausliiga — il
    n'arrive jamais. Et comme la ligne « Non servis » se calculait sur les seuls
    marches profonds, il ne pouvait pas non plus etre declare manquant : le
    marche disparaissait du bloc sans laisser de trace, et l'analyse s'est
    rabattue sur le handicap sans savoir pourquoi.
    """
    session_id, event_id = _football_event(migrated, "Beijing", "Shenzhen")
    # L'etage B a tourne — un book de reference a servi le handicap — mais
    # l'etage A n'a rien ramene : aucune cote 1N2.
    _odds(migrated, event_id, "alternate_spreads", "Beijing")

    bloc = render_blocks(session_id, migrated, now=NOW)[0]

    assert "Non servis" in bloc
    assert "1N2" in _unserved(bloc), "le marche demande, jamais servi et jamais dit"


def test_le_1n2_servi_n_est_pas_annonce_absent(migrated: Settings) -> None:
    """Le cas ordinaire : le book principal sert la competition, l'etage A a
    ramene le 1N2. L'annoncer absent le ferait chercher juste sous la ligne qui
    l'affiche — l'erreur exacte que `MERGED_MARKETS` evite deja ailleurs."""
    session_id, event_id = _football_event(migrated, "Lyon", "Nice")
    _odds(migrated, event_id, "h2h", "Lyon")
    _odds(migrated, event_id, "alternate_spreads", "Lyon")

    bloc = render_blocks(session_id, migrated, now=NOW)[0]

    assert "1N2" not in _unserved(bloc)


def test_les_props_buteurs_ont_un_libelle(migrated: Settings) -> None:
    """Tout marche demande doit avoir son libelle, servi ou non. Sans entree
    dans `MARKET_ORDER`, les deux props sortaient en **cle brute** :
    « player_goal_scorer_anytime » s'affichait tel quel dans la ligne
    « Non servis » d'un match de Ligue 1, seule competition ou elles sont
    demandees. Meme piege que `alternate_totals` avant lui."""
    session_id, event_id = _football_event(migrated, "Lyon", "Nice")
    _odds(migrated, event_id, "h2h", "Lyon")
    _odds(migrated, event_id, "alternate_spreads", "Lyon")

    manquants = _unserved(render_blocks(session_id, migrated, now=NOW)[0])

    assert not [item for item in manquants if item.startswith("player_")]
    assert {"Buteur", "1er buteur"} <= set(manquants)


# -- Densite du bloc CONTEXTE -----------------------------------------------


def test_la_densite_compte_les_lignes_attendues_du_sport() -> None:
    """Sur un lot de cinq matchs, la shortlist affichait le meme badge
    « 3 marches » pour tous, quand deux blocs etaient complets, deux tres pauvres
    et un entierement vide."""
    plein = session_service.context_density(list(labels.CONTEXT_EXPECTED["football"]), "football")

    assert plein.filled == plein.expected == len(labels.CONTEXT_EXPECTED["football"])
    assert plein.label == f"{plein.expected}/{plein.expected}"
    assert not plein.thin and not plein.empty


def test_un_h2h_compte_quel_que_soit_son_nombre() -> None:
    """« H2H (5) » et « H2H (1) » sont la meme ligne : seul le nombre de
    confrontations change, et il varie d'un match a l'autre."""
    assert session_service.context_density(["H2H (5)"], "football").filled == 1
    assert session_service.context_density(["H2H (1)"], "football").filled == 1


def test_une_ligne_hors_referentiel_ne_fait_pas_monter_la_densite() -> None:
    """« Aller » n'existe que sur une manche retour : c'est un bonus, pas un dû,
    et le compter ferait passer un bloc au-dessus de son plafond."""
    densite = session_service.context_density(["Classement", "Aller", "Statut"], "football")

    assert densite.filled == 1, "seul « Classement » est attendu"


def test_un_sport_sans_referentiel_n_a_pas_de_densite() -> None:
    """Le cyclisme est saisi a la main : une densite y mesurerait la saisie."""
    densite = session_service.context_density(["Infos", "Profil"], "cycling")

    assert not densite.known
    assert densite.label == "—"
    assert not densite.empty and not densite.thin, "sans referentiel, aucun jugement"


def test_un_bloc_vide_se_distingue_d_un_bloc_pauvre() -> None:
    vide = session_service.context_density([], "football")
    pauvre = session_service.context_density(["Classement", "Forme 5"], "football")

    assert vide.empty and not vide.thin
    assert pauvre.thin and not pauvre.empty


def test_le_bloc_pauvre_porte_sa_ligne_de_densite(migrated: Settings) -> None:
    """Un match a 0 ligne sur 24 se lisait comme un match sur lequel il n'y avait
    rien a dire, alors que c'est notre collecte qui n'a rien rapporte."""
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
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)

    event = session_service.renderable_events(session_id, migrated, NOW)[0]
    lignes = dict(event.context_lines)

    assert "Densite" in lignes
    assert "aucun contexte récupéré" in lignes["Densite"]


def test_la_densite_a_son_pictogramme() -> None:
    """Toute ligne ajoutee a un bloc entre dans `CONTEXT_ICONS` le meme jour :
    sans quoi la colonne se vide sans rien dire."""
    assert labels.context_icon("Densite")


def _match_sans_rattachement(migrated: Settings) -> int:
    """Un match de football dont la competition n'a aucune ligue de contexte.

    C'est la situation exacte des trois matchs saoudiens du 14/08 : une
    competition creee sans correspondance, donc muette, sans que rien ne le
    dise. Un evenement manuel en cree une par effet de bord, sans identifiant.
    """
    return save(
        build(
            "football",
            "Saudi Pro League",
            "Neom",
            "Al-Fayha",
            "2026-08-04",
            "20:45",
            "Neom 2.10",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )


def test_la_ligne_densite_porte_le_motif_de_l_echec(migrated: Settings) -> None:
    """**Le motif prolonge la densite, il n'occupe pas une seconde ligne.**

    Il qualifie la densite plutot que d'ajouter un fait sur le match, et il
    parle a une analyse qui va chercher : savoir si ce qu'elle ne lit pas est
    une absence de fait ou une absence de collecte decide si la recherche web
    peut combler le trou.
    """
    session_id = board_service.toggle_selection(_match_sans_rattachement(migrated), True, migrated)

    event = session_service.renderable_events(session_id, migrated, NOW)[0]
    lignes = dict(event.context_lines)

    # Derive du referentiel, jamais recopie : voir le meme banc cote routes.
    assert f"0/{len(labels.expected_context('football'))} ligne" in lignes["Densite"]
    assert "n'est rattachée à aucune source de contexte" in lignes["Densite"]


def test_la_shortlist_dit_le_geste_et_non_la_consequence(migrated: Settings) -> None:
    """**Deux lecteurs, deux redactions.** Le bloc dit a l'analyse que ce qui
    manque ne parle pas des equipes ; l'ecran dit a qui repare ou cliquer."""
    session_id = board_service.toggle_selection(_match_sans_rattachement(migrated), True, migrated)

    view = session_service.build_view(session_id, settings=migrated)
    event = view.groups[0][1][0]

    assert event.cause_label == "compétition non rattachée — à relier depuis /competitions"
    assert event.repairable, "un geste repare ce bloc, contrairement a une competition non couverte"
