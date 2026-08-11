"""Le marche dont une selection est issue, garde au moment de l'analyse.

Ce lot n'affiche rien : il ferme une perte. `odds` ne conserve que le dernier
releve, si bien qu'une heure apres un prompt, l'etat du marche que l'analyse a
lu n'existe plus nulle part. Les tests portent donc tous sur la meme question —
**ce qui a ete vu est-il encore la** — et sur son corollaire, qui est la seule
facon de se tromper ici : rattacher une selection au **mauvais** marche vaut
pire que ne la rattacher a rien.
"""

from __future__ import annotations

import sqlite3

import pytest

from myassistantbet import db
from myassistantbet.config import PACKAGE_DIR, Settings
from myassistantbet.services import board as board_service
from myassistantbet.services.history import (
    SCALE_VERSION,
    add_pick,
    list_picks,
    set_event,
)
from myassistantbet.services.manual import build, save
from myassistantbet.services.market_families import market_key_for
from myassistantbet.services.prompt import DEFAULT_TEMPLATE, build_prompt, save_prompt
from myassistantbet.services.render import MARKET_ORDER_BY_SPORT

MIGRATION = PACKAGE_DIR / "migrations" / "033_marche_a_la_prise.sql"
ROLLBACK = PACKAGE_DIR.parent.parent / "deploy" / "rollback" / "033_marche_a_la_prise.down.sql"


# -- Le vocabulaire : reconnaitre un mot qu'on a soi-meme imprime -----------
#
# Aucune base, aucun reglage. C'est une fonction du libelle et du sport, et
# c'est ce qui la rend sure : le bloc n'ecrit que ce vocabulaire-la.


@pytest.mark.parametrize(
    ("sport", "libelle", "attendu"),
    [
        # Le sport decide, et c'est le piege principal : le meme mot ne designe
        # pas le meme marche d'un sport a l'autre.
        ("tennis", "Vainqueur", "h2h"),
        ("cycling", "Vainqueur", "outright"),
        ("football", "1N2", "h2h"),
        # Marches fusionnes : `spreads` se lit sous `alternate_spreads`, et les
        # deux libelles tombent donc sur la meme cle canonique.
        ("football", "Handicap", "alternate_spreads"),
        ("tennis", "Hand. jeux", "alternate_spreads"),
        ("football", "O/U", "totals"),
        ("tennis", "Jeux O/U", "totals"),
        ("football", "Eq. buts", "team_totals"),
        # L'accent ne fait pas un autre marche : la normalisation le retire,
        # comme pour la cle de famille.
        ("football", "Éq. buts", "team_totals"),
        ("football", "BTTS", "btts"),
        ("football", "DC", "double_chance"),
        ("tennis", "Jeux S1", "totals_s1"),
    ],
)
def test_un_libelle_du_bloc_se_resout_en_cle_de_marche(
    sport: str, libelle: str, attendu: str
) -> None:
    assert market_key_for(sport, libelle) == attendu


@pytest.mark.parametrize(
    ("sport", "libelle"),
    [("football", "O/U 2.5"), ("football", "O/U 3.5"), ("tennis", "Jeux O/U 18.5")],
)
def test_la_ligne_d_un_total_ne_fait_pas_un_autre_marche(sport: str, libelle: str) -> None:
    """« O/U 2.5 » et « O/U 3.5 » sont le meme marche a un parametre pres.

    La ligne fait partie du libelle recopie, jamais du marche : elle se retire
    donc pour la seconde tentative de resolution.

    Les trois libelles viennent de la base, et « Jeux O/U 18.5 » y est **du
    tennis** : c'est le sport qui porte le vocabulaire, la ligne n'y change rien.
    """
    assert market_key_for(sport, libelle) == "totals"


def test_un_numero_de_set_n_est_pas_une_ligne() -> None:
    """Et c'est pourquoi l'essai exact passe **avant** le retrait du nombre.

    « Set 1 » et « Set 2 » sont deux marches distincts dont le numero **est** le
    nom. Retirer le nombre d'emblee les confondrait, et une selection sur le
    premier set se rattacherait au marche du second — l'erreur exactement
    inverse de celle qu'on cherche a eviter.
    """
    assert market_key_for("tennis", "Set 1") == "h2h_s1"
    assert market_key_for("tennis", "Set 2") == "h2h_s2"


@pytest.mark.parametrize(
    "libelle",
    [
        # Ce que le bloc ecrit est « DC ». « Double chance » est une saisie
        # humaine, et la reconnaitre serait deduire d'un libelle.
        "Double chance",
        "Nombre total de buts (t. rég)",
        "Les 2 équipes marquent (t. rég)",
        "",
    ],
)
def test_un_libelle_hors_vocabulaire_ne_se_devine_pas(libelle: str) -> None:
    """Trois libelles reels de la base, et aucun n'est du vocabulaire du bloc.

    Ils restent non resolus et se reclament. Leur donner la cle « probable »
    rattacherait la selection a un marche qu'elle ne nomme pas, et le releve
    historique rendrait alors un favori qui n'est pas le sien.
    """
    assert market_key_for("football", libelle) is None


def test_un_libelle_partage_par_deux_marches_ne_tranche_pas(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Le cas n'existe pas dans le vocabulaire actuel, et c'est pourquoi il se
    fabrique ici.

    Deux cles **non fusionnees** sous un meme libelle rendraient la resolution
    arbitraire : en choisir une rattacherait la selection au mauvais marche du
    releve, donc lui donnerait un favori qui n'est pas le sien. Le jour ou un
    marche ajoute a `render` produit cette collision, il faut un silence et un
    log, pas un tirage au sort.
    """
    monkeypatch.setitem(MARKET_ORDER_BY_SPORT, "handball", [("h2h", "Issue"), ("totals", "Issue")])

    assert market_key_for("handball", "Issue") is None


def test_tout_libelle_du_bloc_se_resout_dans_son_sport() -> None:
    """Le garde-fou de non-regression du vocabulaire.

    Meme regle que les identifiants du sprite ou les libelles gardes par une
    porte du preambule : un marche ajoute a `render` sans etre resoluble ici ne
    casserait rien — il ferait seulement disparaitre la cle en silence.
    """
    muets = [
        (sport, label)
        for sport, order in MARKET_ORDER_BY_SPORT.items()
        for _, label in order
        if market_key_for(sport, label) is None
    ]
    assert muets == []


# -- Le releve : ce que l'analyse avait sous les yeux ------------------------


def _match(settings: Settings, home: str = "Lyon", away: str = "Nice") -> int:
    """Un match saisi a la main, avec ses trois cotes. Aucun reseau.

    L'horaire est **loin devant**, et ce n'est pas un detail : un match dont
    l'heure est passee sort du prompt (`session.has_started`), donc n'entre dans
    aucun releve. Un lot date d'hier aurait fait passer ces tests pour verts en
    ne mesurant rien.
    """
    return save(
        build(
            "football",
            "Match amical",
            home,
            away,
            "2099-01-01",
            "20:45",
            f"{home} 2.10\nNul 3.40\n{away} 3.20",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _lot(settings: Settings, matchs: int = 1) -> tuple[int, list[int]]:
    session_id = 0
    events = []
    for index in range(matchs):
        event_id = _match(settings, f"Lyon {index}", f"Nice {index}")
        events.append(event_id)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, events


def _archive(settings: Settings, session_id: int) -> int:
    return save_prompt(session_id, build_prompt(session_id, DEFAULT_TEMPLATE, settings), settings)


def test_le_marche_complet_part_avec_le_prompt(migrated: Settings) -> None:
    """Les trois issues, et pas seulement celle qui sera jouee.

    C'est tout l'objet du lot : une selection seule ne dit ni qui etait favori
    ni ce que le marche entier valait, et ces deux questions sont les seules qui
    permettraient de comparer un taux a une reference exterieure.
    """
    session_id, (event_id,) = _lot(migrated)
    _archive(migrated, session_id)

    releve = db.query(
        "SELECT outcome_name, price FROM prompt_odds "
        "WHERE session_id = ? AND event_id = ? ORDER BY price",
        (session_id, event_id),
        settings=migrated,
    )
    assert [(row["outcome_name"], row["price"]) for row in releve] == [
        ("Lyon 0", 2.10),
        ("Nice 0", 3.20),
        ("Nul", 3.40),
    ]


def test_regenerer_le_prompt_ne_duplique_aucune_cote(migrated: Settings) -> None:
    """Une session reelle genere jusqu'a vingt prompts.

    Le releve est **par session et par match**, pas par prompt : vingt copies du
    meme marche ne diraient pas vingt fois plus, elles rendraient seulement tout
    comptage faux. Meme regle d'idempotence que partout ailleurs.
    """
    session_id, _ = _lot(migrated)
    for _ in range(3):
        _archive(migrated, session_id)

    compte = db.query_one(
        "SELECT COUNT(*) AS n FROM prompt_odds WHERE session_id = ?",
        (session_id,),
        settings=migrated,
    )
    assert compte["n"] == 3, "trois issues, une seule fois"


def test_le_releve_suit_le_dernier_etat_connu_du_marche(migrated: Settings) -> None:
    """Un match entre parfois dans un prompt **avant** d'etre enrichi.

    Figer le premier releve garderait alors l'etat le plus pauvre, quand le
    dernier prompt qui porte le match est celui dont l'etat est le plus proche
    de la decision.
    """
    session_id, (event_id,) = _lot(migrated)
    _archive(migrated, session_id)

    db.execute(
        "UPDATE odds SET price = 1.75 WHERE event_id = ? AND outcome_name = 'Lyon 0'",
        (event_id,),
        settings=migrated,
    )
    _archive(migrated, session_id)

    releve = db.query_one(
        "SELECT price FROM prompt_odds WHERE session_id = ? AND outcome_name = 'Lyon 0'",
        (session_id,),
        settings=migrated,
    )
    assert releve["price"] == 1.75


def test_le_releve_distingue_l_heure_du_prix_et_celle_de_la_capture(migrated: Settings) -> None:
    """Deux horodatages, et ils ne disent pas la meme chose.

    `fetched_at` est l'heure a laquelle le prix a ete releve chez le
    fournisseur ; `captured_at` celle a laquelle il a ete fige pour cette
    session. Les confondre ferait passer un prix de la veille pour un prix du
    matin.
    """
    session_id, _ = _lot(migrated)
    _archive(migrated, session_id)

    ligne = db.query_one(
        "SELECT fetched_at, captured_at FROM prompt_odds WHERE session_id = ?",
        (session_id,),
        settings=migrated,
    )
    assert ligne["fetched_at"] and ligne["captured_at"]
    assert ligne["captured_at"] >= ligne["fetched_at"]


def test_un_lot_sans_cote_ne_leve_pas(migrated: Settings) -> None:
    """Un match sans aucune cote n'a pas de marche a figer, et ce n'est pas une
    erreur : les tours preliminaires arrivent par API-Football, sans prix."""
    session_id, _ = _lot(migrated)
    db.execute("DELETE FROM odds", settings=migrated)
    _archive(migrated, session_id)

    compte = db.query_one("SELECT COUNT(*) AS n FROM prompt_odds", settings=migrated)
    assert compte["n"] == 0


# -- La cle de marche : figee a l'ecriture, resolue a la lecture -------------


def _selection(settings: Settings, session_id: int, event_id: int, market: str) -> int:
    return add_pick(
        session_id,
        "safe",
        market,
        "Lyon 0",
        event_id=str(event_id),
        price="2.10",
        settings=settings,
    )


def test_la_cle_de_marche_est_figee_a_l_ecriture(migrated: Settings) -> None:
    session_id, (event_id,) = _lot(migrated)
    _selection(migrated, session_id, event_id, "O/U 2.5")

    ligne = db.query_one("SELECT market_key FROM picks", settings=migrated)
    assert ligne["market_key"] == "totals"


def test_un_libelle_hors_vocabulaire_reste_sans_cle(migrated: Settings) -> None:
    """Et surtout : la selection est enregistree quand meme.

    Une cle absente vaut « on ne sait pas », jamais un refus — meme regle que
    l'angle, le niveau de source ou la provenance de la cote. Refuser la saisie
    ferait perdre la selection entiere pour une colonne d'appoint.
    """
    session_id, (event_id,) = _lot(migrated)
    _selection(migrated, session_id, event_id, "Double chance")

    ligne = db.query_one("SELECT market_key, market FROM picks", settings=migrated)
    assert (ligne["market_key"], ligne["market"]) == (None, "Double chance")


def test_une_selection_sans_match_n_a_pas_de_cle(migrated: Settings) -> None:
    """Sans match, aucun sport, donc aucun vocabulaire : le meme libelle
    designerait deux marches differents."""
    session_id, _ = _lot(migrated)
    add_pick(session_id, "safe", "Vainqueur", "Lyon", settings=migrated)

    ligne = db.query_one("SELECT market_key FROM picks", settings=migrated)
    assert ligne["market_key"] is None


def test_une_selection_anterieure_resout_sa_cle_a_la_lecture(migrated: Settings) -> None:
    """Les 114 selections deja en base n'ont pas de colonne remplie.

    Elles la resolvent a la lecture plutot que par un retro-remplissage : la
    regle vit en Python, et la recopier en SQL l'aurait fait diverger au premier
    marche ajoute. Meme arbitrage que la famille d'un marche.
    """
    session_id, (event_id,) = _lot(migrated)
    pick_id = _selection(migrated, session_id, event_id, "O/U 2.5")
    db.execute("UPDATE picks SET market_key = NULL WHERE id = ?", (pick_id,), settings=migrated)

    pick = list_picks(session_id, migrated)[0]
    assert (pick.market_key, pick.market_key_effective) == ("", "totals")


def test_la_cle_figee_prime_sur_le_libelle(migrated: Settings) -> None:
    """C'est la raison d'etre de la colonne.

    Un libelle renomme dans `render` ne doit pas couper le lien entre une
    selection et le releve de marche pris le meme jour. Ce qui a ete vu reste
    ce qui a ete vu.
    """
    session_id, (event_id,) = _lot(migrated)
    pick_id = _selection(migrated, session_id, event_id, "O/U 2.5")
    db.execute(
        "UPDATE picks SET market_key = 'totals', market = 'libellé retiré depuis' WHERE id = ?",
        (pick_id,),
        settings=migrated,
    )

    assert list_picks(session_id, migrated)[0].market_key_effective == "totals"


def test_corriger_le_rattachement_recalcule_la_cle(migrated: Settings) -> None:
    """Le seul endroit ou la cle bouge apres coup, et c'est justifie : elle
    etait **fausse**, pas perimee. Un rattachement corrige peut changer de
    sport, donc de vocabulaire."""
    session_id, (event_id,) = _lot(migrated)
    pick_id = _selection(migrated, session_id, event_id, "O/U 2.5")

    set_event(pick_id, "", migrated)
    assert db.query_one("SELECT market_key FROM picks", settings=migrated)["market_key"] is None

    set_event(pick_id, str(event_id), migrated)
    assert db.query_one("SELECT market_key FROM picks", settings=migrated)["market_key"] == "totals"


# -- La boucle, et l'echelle -------------------------------------------------


def test_la_session_porte_l_echelle_en_vigueur(migrated: Settings) -> None:
    session_id, _ = _lot(migrated)
    _archive(migrated, session_id)

    ligne = db.query_one(
        "SELECT scale_version FROM sessions WHERE id = ?", (session_id,), settings=migrated
    )
    assert ligne["scale_version"] == SCALE_VERSION


def test_l_echelle_se_fige_au_premier_prompt(migrated: Settings) -> None:
    """Changer d'echelle en cours de session ne doit pas reetiqueter les
    selections deja rendues sous l'ancienne."""
    session_id, _ = _lot(migrated)
    _archive(migrated, session_id)
    db.execute(
        "UPDATE sessions SET scale_version = 'echelle-precedente' WHERE id = ?",
        (session_id,),
        settings=migrated,
    )
    _archive(migrated, session_id)

    ligne = db.query_one(
        "SELECT scale_version FROM sessions WHERE id = ?", (session_id,), settings=migrated
    )
    assert ligne["scale_version"] == "echelle-precedente"


def test_un_prompt_sans_recul_ne_referme_aucune_boucle(migrated: Settings) -> None:
    """Base vierge : le bloc de retour d'experience ne publie aucun taux.

    C'est l'etat normal, et c'est ce que la colonne doit dire. Des qu'un agregat
    de resultats entre dans le prompt, les selections suivantes ne sont plus des
    tirages independants — l'analyse lit son propre tableau de bord.
    """
    session_id, _ = _lot(migrated)
    prompt_id = _archive(migrated, session_id)

    ligne = db.query_one(
        "SELECT feedback_active FROM prompts WHERE id = ?", (prompt_id,), settings=migrated
    )
    assert ligne["feedback_active"] == 0


def test_la_migration_retro_remplit_depuis_le_corps_archive(migrated: Settings) -> None:
    """Le corps du prompt **est** la preuve, et il est archive depuis toujours.

    La question « depuis quand le bloc etait-il servi » n'avait aucune reponse
    ailleurs. Le test rejoue le fichier de migration plutot que d'en recopier le
    critere : deux ecritures de la meme regle divergeraient sans un mot, et le
    retro-remplissage ne repasse jamais.
    """
    session_id, _ = _lot(migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'x.md.j2', 'Taux de réussite de mes 60 dernières, jouées ou non :', 10, ?)",
        (session_id, db.utcnow()),
        settings=migrated,
    )
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'x.md.j2', 'recul insuffisant, non transmis', 10, ?)",
        (session_id, db.utcnow()),
        settings=migrated,
    )

    critere = MIGRATION.read_text(encoding="utf-8").split("ALTER TABLE prompts")[1]
    critere = "UPDATE prompts" + critere.split("UPDATE prompts")[1]
    with db.connect(migrated) as conn:
        conn.execute(critere)

    etats = [
        row["feedback_active"]
        for row in db.query("SELECT feedback_active FROM prompts ORDER BY id", settings=migrated)
    ]
    assert etats == [1, 0]


# -- Le retour arriere -------------------------------------------------------


def test_le_retour_arriere_defait_tout_ce_que_la_migration_a_pose(migrated: Settings) -> None:
    """Un script de retour arriere qui oublie une colonne est pire qu'absent :
    on le joue en croyant revenir en arriere, et le schema reste hybride."""
    # Connexion brute et non `db.connect` : le script porte son propre
    # `BEGIN`/`COMMIT`, parce qu'il se joue a la main via `sqlite3 base < fichier`.
    # Le passer dans une transaction deja ouverte le ferait echouer — et c'est
    # bien la forme reelle du script qu'on veut eprouver.
    conn = sqlite3.connect(migrated.db_path_absolute)
    try:
        conn.executescript(ROLLBACK.read_text(encoding="utf-8"))
    finally:
        conn.close()

    tables = set(db.list_tables(migrated))
    assert "prompt_odds" not in tables
    for table, colonne in (
        ("picks", "market_key"),
        ("sessions", "scale_version"),
        ("prompts", "feedback_active"),
    ):
        colonnes = {
            row["name"] for row in db.query(f"PRAGMA table_info({table})", settings=migrated)
        }
        assert colonne not in colonnes, f"{table}.{colonne} survit au retour arriere"

    reste = db.query("SELECT version FROM schema_migrations WHERE version = 33", settings=migrated)
    assert reste == []


def test_le_retour_arriere_ne_vit_pas_dans_le_dossier_des_migrations() -> None:
    """`discover_migrations` lit tout `*.sql` du dossier et leve sur une version
    dupliquee : un `033_..._down.sql` pose a cote de son aller empecherait
    l'application de demarrer."""
    assert ROLLBACK.exists()
    assert not list((PACKAGE_DIR / "migrations").glob("*down*"))
