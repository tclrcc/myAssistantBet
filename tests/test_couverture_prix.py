"""Les competitions qu'aucun book ne cote, ecartees du board et du prompt.

**La regle opere au niveau de la competition, jamais de l'evenement**, et c'est
la mesure qui l'impose. Releve du 26/08/2026 : 295 evenements n'ont jamais porte
de prix, et ils se repartissent en deux populations que rien ne permet de traiter
pareil.

· **154** viennent de trois tournois que The Odds API ne sert pas du tout — pas de
  cle, aucun prix par aucun chemin, jamais. Structurel, et certain ;
· **141** vivent **dans des competitions servies** — EFL Cup 43 sur 57, Leagues
  Cup 32 sur 40. Le book y cote certains matchs et pas d'autres.

**Le contre-exemple qui interdit la regle par evenement** : au moment de la
mesure, douze rencontres a venir n'avaient aucun prix, dont **Lyon - Fenerbahce**
a dix heures du coup d'envoi et Chelsea - Luton a trente-trois. Un filtre sur
« pas de cote maintenant » les aurait masquees.

**Et le ratio qui semblait dire l'inverse ne dit rien** : 283 passes contre 12 a
venir mesure une **duree de sejour**, pas une probabilite. Un evenement passe sans
prix y reste pour toujours ; un evenement a venir sans prix se resout en heures et
quitte la categorie. Les deux ne se comparent pas.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services.competitions import PRICE_WINDOW_DAYS, unpriced

MAINTENANT = datetime(2026, 8, 26, 9, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def _competition(settings: Settings, label: str, cle: str | None = None) -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO competitions (sport_id, oddsapi_key, label, active) VALUES (?, ?, ?, 1)",
        (sport["id"], cle, label),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM competitions", settings=settings)["id"])


def _event(settings: Settings, competition_id: int, heures: float, nom: str = "Lyon") -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, competition_id, home, away, commence_time, source, "
        "                    created_at) VALUES (?, ?, ?, 'Adv', ?, 'api', ?)",
        (
            sport["id"],
            competition_id,
            nom,
            _iso(MAINTENANT + timedelta(hours=heures)),
            _iso(MAINTENANT - timedelta(days=1)),
        ),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _prix(
    settings: Settings, event_id: int, jours: float, book: str = "betclic_fr", issue: str = ""
) -> None:
    """Un prix sur cet evenement. `issue` doit nommer l'equipe pour que le board
    le rende — il rapproche l'issue de l'affiche, pas seulement du marche."""
    if not issue:
        row = db.query_one("SELECT home FROM events WHERE id = ?", (event_id,), settings=settings)
        issue = str(row["home"])
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
        "VALUES (?, ?, 'h2h', ?, 1.85, ?)",
        (event_id, book, issue, _iso(MAINTENANT - timedelta(days=jours))),
        settings=settings,
    )


def _labels(settings: Settings) -> set[str]:
    return {entree.label for entree in unpriced(settings, MAINTENANT)}


# -- La regle -----------------------------------------------------------------


def test_une_competition_jamais_cotee_est_ecartee(migrated: Settings) -> None:
    """Les trois tournois du 24/08 : aucune cle, aucun prix, jamais."""
    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    _event(migrated, tournoi, heures=10)
    _event(migrated, tournoi, heures=30)

    assert _labels(migrated) == {"ATP Winston-Salem Open"}


def test_un_match_a_venir_sans_prix_reste_si_sa_competition_est_cotee(
    migrated: Settings,
) -> None:
    """**Le contre-exemple qui decide de tout : Lyon - Fenerbahce.**

    Dix heures avant le coup d'envoi, sans aucun prix, dans une competition que le
    book sert par ailleurs. A l'echelle de l'evenement, rien ne distingue « ne
    sera pas cote » de « pas encore cote » — et en cas de doute, rien.
    """
    qualif = _competition(migrated, "UEFA Champions League Qualification", cle="soccer_ucl_qual")
    _prix(migrated, _event(migrated, qualif, heures=-48, nom="Celtic"), jours=1.5)
    _event(migrated, qualif, heures=10, nom="Lyon")

    assert _labels(migrated) == set()


def test_une_competition_sans_match_a_venir_n_est_pas_ecartee(migrated: Settings) -> None:
    """Il n'y a rien a cacher : elle ne parait deja plus au board.

    Mesure du 26/08 : les trois tournois vises etaient **dans ce cas** — leurs
    tableaux etaient termines. La population cible de la regle est vide la
    plupart du temps, et c'est normal.
    """
    tournoi = _competition(migrated, "ATP US Open Qualifications")
    _event(migrated, tournoi, heures=-10)

    assert _labels(migrated) == set()


def test_la_bascule_joue_dans_les_deux_sens(migrated: Settings) -> None:
    """**Reversible, et pas seulement dans un sens.**

    Un prix suffit a faire revenir une competition ; un releve trop vieux la fait
    partir, sans quoi un tournoi annuel reapparaitrait servi douze mois apres.

    Le tournoi monte ici est **sans cle**, comme Winston-Salem : c'est la seule
    forme ou la regle mord. Monterrey, souvent cite a cote, n'en relevait pas —
    elle etait au catalogue avec `api_active` a 1, donc servie a l'instant et
    simplement inactive, et le geste correct y etait d'activer.
    """
    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    event_id = _event(migrated, tournoi, heures=10)

    assert _labels(migrated) == {"ATP Winston-Salem Open"}

    _prix(migrated, event_id, jours=1)
    assert _labels(migrated) == set(), "un prix recent la ramene"

    db.execute(
        "UPDATE odds SET fetched_at = ?",
        (_iso(MAINTENANT - timedelta(days=PRICE_WINDOW_DAYS + 1)),),
        settings=migrated,
    )
    assert _labels(migrated) == {"ATP Winston-Salem Open"}, "un prix trop vieux ne la garde pas"


def test_la_fenetre_garde_une_competition_servie_par_phases(migrated: Settings) -> None:
    """**Mesure qui fixe la borne basse** : la Leagues Cup porte deux matchs a
    venir et son dernier prix date de **13,5 jours** — elle joue par phases.

    Une fenetre de sept jours l'aurait ecartee ; elle est servie, huit de ses
    quarante evenements portent des prix. La borne mesuree est donc « plus de
    quatorze jours », et la base — vingt-deux jours en tout — **ne permet pas de
    departager trois semaines de quatre**. Le choix se fait du cote sur : le
    badge « aucun prix » couvre deja le cas ou la competition traine.
    """
    coupe = _competition(migrated, "Leagues Cup")
    _prix(migrated, _event(migrated, coupe, heures=-336, nom="Toluca"), jours=13.5)
    _event(migrated, coupe, heures=15)

    assert _labels(migrated) == set()
    assert PRICE_WINDOW_DAYS > 14, "sous quinze jours, la Leagues Cup serait masquee a tort"


def test_une_cote_saisie_a_la_main_compte_comme_un_prix(migrated: Settings) -> None:
    """**Le second chemin.** La regle dit « aucune cote obtenable par aucun des
    deux chemins » : un releve de substitution ou une saisie manuelle valent
    autant qu'un prix du fournisseur.

    Sans cette lecture, la regle naive — « competition sans cle Odds API » —
    aurait ecarte quatre selections tranchees reelles : Community Shield,
    Supercoupe d'Europe et Trophee des Champions.
    """
    supercoupe = _competition(migrated, "Supercoupe d'Europe")
    _prix(migrated, _event(migrated, supercoupe, heures=-24), jours=2, book="manual")
    _event(migrated, supercoupe, heures=10)

    assert _labels(migrated) == set()


# -- Le board, et ce qu'il dit de ce qu'il ne montre plus ---------------------


def test_le_board_ecarte_les_matchs_et_le_bandeau_les_annonce(migrated: Settings) -> None:
    """**Un retrait silencieux ne se distingue pas d'une absence de matchs.**

    Le bandeau nomme les competitions retirees **avec leur compte**, la ou les
    non rattachees sont nommees sans compte : celles-la attendent une saisie et
    c'est le nom qui dit laquelle, celles-ci disparaissent avec leurs matchs et
    c'est le compte qui dit ce qu'on ne voit plus.
    """
    from myassistantbet.services import board as board_service

    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    _event(migrated, tournoi, heures=10, nom="Machac")
    _event(migrated, tournoi, heures=20, nom="Bublik")
    servie = _competition(migrated, "EFL Cup", cle="soccer_efl_cup")
    _prix(migrated, _event(migrated, servie, heures=-24, nom="Chelsea"), jours=1)
    _event(migrated, servie, heures=9, nom="Fulham")

    lignes, _ = board_service._collect(settings=migrated, now=MAINTENANT)
    affiches = {ligne.home for ligne in lignes}

    assert "Fulham" in affiches, "un match sans prix dans une compétition servie reste"
    assert "Machac" not in affiches and "Bublik" not in affiches

    bandeau = board_service.banner(migrated, MAINTENANT)
    assert [e.label for e in bandeau.unpriced_competitions] == ["ATP Winston-Salem Open"]
    assert bandeau.hidden_events == 2


def test_un_match_sans_prix_porte_son_badge(migrated: Settings) -> None:
    """Le troisieme geste, et le meilleur des trois : **un fait affiche plutot
    qu'un filtre applique**, exactement comme la densite dit un bloc pauvre.

    Les 43 rencontres non cotees de l'EFL Cup restent visibles avec leur etat
    lisible, et personne n'a a deviner pourquoi un match n'a pas de cote.
    """
    from myassistantbet.services import board as board_service

    servie = _competition(migrated, "EFL Cup", cle="soccer_efl_cup")
    cote = _event(migrated, servie, heures=9, nom="Chelsea")
    _prix(migrated, cote, jours=1)
    _event(migrated, servie, heures=10, nom="Bradford")

    lignes = {
        ligne.home: ligne for ligne in board_service._collect(settings=migrated, now=MAINTENANT)[0]
    }

    assert lignes["Bradford"].unpriced is True
    assert lignes["Chelsea"].unpriced is False


# -- Le prompt lit la meme fonction que le board ------------------------------


def test_le_prompt_ecarte_les_memes_competitions_que_le_board(migrated: Settings) -> None:
    """**Sixieme occurrence du motif, et la question posee a l'ecriture.**

    Deux surfaces qui filtrent la meme chose : si chacune ecrit sa regle, elles
    divergeront — et l'ecart ne ferait echouer aucun test, les deux etant justes
    chacune de son cote. Le prompt appelle donc `competitions.unpriced`, la meme
    fonction que le board, et ce test compare les **deux sorties reelles** plutot
    que de verifier un appel.
    """
    from myassistantbet.services import board as board_service
    from myassistantbet.services import session as session_service

    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    ecarte = _event(migrated, tournoi, heures=10, nom="Machac")
    servie = _competition(migrated, "EFL Cup", cle="soccer_efl_cup")
    _prix(migrated, _event(migrated, servie, heures=-24, nom="Chelsea"), jours=1)
    garde = _event(migrated, servie, heures=9, nom="Fulham")

    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (_iso(MAINTENANT),),
        settings=migrated,
    )
    session_id = int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=migrated)["id"])
    for event_id in (ecarte, garde):
        db.execute(
            "INSERT INTO session_events (session_id, event_id) VALUES (?, ?)",
            (session_id, event_id),
            settings=migrated,
        )

    au_board = {
        ligne.home for ligne in board_service._collect(settings=migrated, now=MAINTENANT)[0]
    }
    au_prompt = {
        bloc.home for bloc in session_service.renderable_events(session_id, migrated, MAINTENANT)
    }

    assert "Machac" not in au_board and "Machac" not in au_prompt
    assert "Fulham" in au_board and "Fulham" in au_prompt
    assert au_board == au_prompt, "les deux surfaces doivent ecarter exactement la meme chose"


# -- Le journal : les transitions, jamais un instantane ----------------------


def test_le_journal_date_les_deux_sens_et_ne_repete_pas_un_etat(migrated: Settings) -> None:
    """**Ce sont les transitions qui informent.**

    Un instantane a chaque scan grossirait sans rien apprendre et noierait la
    bascule au milieu du bruit. Et les deux sens sont dates : sans le retour, la
    regle ne serait reversible que dans un, et le journal laisserait croire
    qu'une exclusion est definitive — alors que Monterrey a bascule en un jour.
    """
    from myassistantbet.services import changelog
    from myassistantbet.services.competitions import (
        UNPRICED_ARMED,
        UNPRICED_ENTERED,
        UNPRICED_LEFT,
        note_price_coverage,
    )

    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    event_id = _event(migrated, tournoi, heures=10)

    assert note_price_coverage(migrated, MAINTENANT) == [
        (UNPRICED_ARMED, "1 compétition(s) déjà dans l'état"),
        (UNPRICED_ENTERED, "ATP Winston-Salem Open"),
    ], "la mise en service precede les transitions du meme passage"
    assert note_price_coverage(migrated, MAINTENANT) == [], "un etat stable n'ecrit rien"

    _prix(migrated, event_id, jours=1)
    assert note_price_coverage(migrated, MAINTENANT) == [(UNPRICED_LEFT, "ATP Winston-Salem Open")]
    assert note_price_coverage(migrated, MAINTENANT) == []

    entrees = [e for e in changelog.journal(migrated).entries if "board" in e.label]
    assert len(entrees) == 2, "une entree par transition, pas une par scan"

    # **La fenetre appliquee voyage avec l'entree.** Elle se reglera un jour — la
    # base ne peut pas departager trois semaines de quatre — et une entree de
    # journal qui ne dit pas sous quelle regle elle a ete ecrite ne se relit plus.
    entree = next(e for e in entrees if e.label == UNPRICED_ENTERED)
    assert "1 match(s) à venir" in entree.description
    assert "aucun prix connu" in entree.description
    assert f"fenêtre de {PRICE_WINDOW_DAYS} jours" in entree.description


def test_le_journal_separe_aucune_transition_de_jamais_evaluee(migrated: Settings) -> None:
    """**Deux causes, une observation**, et c'est ce que la mise en service rompt.

    Sans elle, un journal sans entree dit « aucune competition n'a bascule » et
    « la regle n'a jamais tourne » du meme silence — l'une se lit et ne demande
    rien, l'autre est une panne de deploiement. Elle est donc datee au **premier
    scan qui l'evalue**, meme quand ce scan ne retire rien : c'est aussi ce qui
    rend vraie la formule du sixieme point de rupture, qui porte sur l'instant ou
    la composition des lots devient soumise a la regle.
    """
    from myassistantbet.services import changelog
    from myassistantbet.services.competitions import (
        UNPRICED_ARMED,
        UNPRICED_ENTERED,
        UNPRICED_LEFT,
        note_price_coverage,
    )

    libelles = {UNPRICED_ARMED, UNPRICED_ENTERED, UNPRICED_LEFT}

    def journal_de_la_regle() -> list[str]:
        return [e.label for e in changelog.journal(migrated).entries if e.label in libelles]

    servie = _competition(migrated, "Coupe servie", cle="soccer_coupe_servie")
    _prix(migrated, _event(migrated, servie, heures=10), jours=1)

    assert journal_de_la_regle() == [], "avant toute evaluation, le journal est muet"

    assert note_price_coverage(migrated, MAINTENANT) == [
        (UNPRICED_ARMED, "aucune compétition dans l'état")
    ], "la regle a tourne sans rien retirer, et ca se date quand meme"
    assert journal_de_la_regle() == [UNPRICED_ARMED]

    assert note_price_coverage(migrated, MAINTENANT) == [], "une fois et une seule"
    assert journal_de_la_regle() == [UNPRICED_ARMED], "la garde se lit sur le journal lui-meme"

    # La fenetre appliquee voyage avec la mise en service comme avec les
    # transitions : une entree qui ne dit pas sous quelle regle elle a ete
    # ecrite ne se relit plus, et celle-ci sert de borne a toutes les autres.
    mise_en_service = next(
        e for e in changelog.journal(migrated).entries if e.label == UNPRICED_ARMED
    )
    assert f"fenêtre de {PRICE_WINDOW_DAYS} jours" in mise_en_service.description


def test_une_panne_de_cotes_ne_vide_pas_le_board(migrated: Settings) -> None:
    """**L'absence de prix ne suffit pas : il faut que le fournisseur le dise.**

    Une competition servie dont l'appel de cotes echoue n'a pas de prix non plus.
    La masquer viderait le board sur une panne — et c'est exactement ce que
    quatre tests de board ont fait apparaitre avant la livraison, en montant des
    competitions sans cotes qui n'avaient rien d'anormal.

    `api_active` est la **declaration du fournisseur**, ecrite par
    `sync_from_api` ; une cle absente dit qu'il ne connait pas la competition du
    tout. On lit ce que la source dit, plutot que de le deduire d'un silence — et
    ca garde la reversibilite dans les deux sens, la synchronisation faisant
    passer `api_active` a 0 le jour ou une competition cesse d'etre servie.
    """
    servie = _competition(migrated, "EFL Cup", cle="soccer_efl_cup")
    _event(migrated, servie, heures=10)
    _event(migrated, servie, heures=20)

    assert _labels(migrated) == set(), "aucun prix, mais le fournisseur la sert"

    db.execute("UPDATE competitions SET api_active = 0 WHERE id = ?", (servie,), settings=migrated)
    assert _labels(migrated) == {"EFL Cup"}, "le fournisseur ne la sert plus : elle sort"


def test_les_rencontres_retirees_restent_atteignables_par_la_competition(
    migrated: Settings,
) -> None:
    """**Une entree propre, pas une porte dans le filtre.**

    Une exception laissee visible au board finit par y rester, et le filtre cesse
    d'en etre un. L'entree par la competition est le bon niveau — c'est la que la
    regle opere — et c'est elle qui rend « les fixtures entrent » vrai en
    pratique : sans ce chemin, un tournoi importe deviendrait invisible et
    incotable, ce qui reviendrait a couper a l'ingestion par un detour.
    """
    from myassistantbet.services.competitions import hidden_events

    tournoi = _competition(migrated, "ATP Winston-Salem Open")
    cotable = _event(migrated, tournoi, heures=10, nom="Machac")
    _event(migrated, tournoi, heures=20, nom="Bublik")
    _event(migrated, tournoi, heures=-10, nom="Passe")

    matchs = hidden_events(migrated, MAINTENANT)[tournoi]
    assert [m.home for m in matchs] == ["Machac", "Bublik"], "les matchs passés n'y sont pas"

    # **Et la liste se vide des qu'une cote entre**, parce que la regle opere au
    # niveau de la competition : une seule saisie manuelle la ramene au board avec
    # toutes ses rencontres. C'est ce qui rend `HiddenEvent.priced` inatteignable,
    # et pourquoi ce drapeau a ete retire au lieu d'etre affiche.
    _prix(migrated, cotable, jours=1, book="manual")
    assert hidden_events(migrated, MAINTENANT) == {}
