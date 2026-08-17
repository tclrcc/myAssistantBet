"""Ou depenser un budget de recherche fini.

Mesure qui a fait naitre le module : sur un lot reel de 21 manches retour,
3 dossiers ont ete traites — choisis au juge sur les matchs les plus lisibles —
et 18 selections sont retombees en `lecture`, donc a confiance 1.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import context as context_service
from myassistantbet.services import research
from myassistantbet.services.context import KIND_H2H, KIND_TEAMS, store
from myassistantbet.services.render import RenderableEvent
from myassistantbet.services.thresholds import value_of

PARIS = ZoneInfo("Europe/Paris")
COUP_ENVOI = datetime(2026, 8, 13, 20, 0, tzinfo=PARIS)
LEAGUE = 113


#: Assez de lignes pour depasser le seuil de densite : un evenement de
#: remplissage ne doit pas se retrouver dans la fiche pour un bloc vide.
REMPLI = {
    "football": [
        "Classement",
        "Enjeu",
        "Forme 5",
        "Dom/Ext",
        "H2H",
        "Absents",
        "Repos",
        "Buts marq.",
        "Buts pris",
        "Clean sheet",
        "1re MT",
        "Cartons tps",
        "Formations",
    ],
    "tennis": [
        "Elo",
        "Surface",
        "Forme",
        "Profil",
        "Marge",
        "Niveau adv.",
        "Usure",
        "Precedent",
    ],
}


def _dense(sport: str = "football") -> list[tuple[str, str]]:
    return [(label, "valeur") for label in REMPLI[sport]]


def _event(
    index: int,
    event_id: int = 0,
    sport: str = "football",
    context: list[tuple[str, str]] | None = None,
) -> RenderableEvent:
    return RenderableEvent(
        index=index,
        sport_key=sport,
        competition="Allsvenskan",
        home=f"Club {index}",
        away=f"Adv {index}",
        commence_local=COUP_ENVOI,
        event_id=event_id,
        context_lines=context if context is not None else _dense(sport),
    )


def _en_base(settings: Settings, event_id: int, index: int, rattachee: bool = True) -> None:
    """Un evenement reel, rattache ou non a un fournisseur de contexte."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = ?",
        (LEAGUE,),
        settings=settings,
    )
    if not rattachee:
        db.execute(
            "UPDATE competitions SET apifootball_league_id = NULL, tennisdata_tournaments = NULL "
            "WHERE id = ?",
            (competition["id"],),
            settings=settings,
        )
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, source, "
        "created_at) VALUES (?, ?, ?, ?, ?, '2026-08-13T18:00:00Z', 'api', ?)",
        (
            event_id,
            competition["sport_id"],
            competition["id"],
            f"Club {index}",
            f"Adv {index}",
            db.utcnow(),
        ),
        settings=settings,
    )


def _aller(settings: Settings, event_id: int, buts_adversaire: int, buts_nous: int = 0) -> None:
    """L'aller, joue chez l'adversaire. `buts_adversaire` est son score a lui."""
    store(event_id, KIND_TEAMS, {"home": 1, "away": 2, "league": LEAGUE}, settings)
    store(
        event_id,
        KIND_H2H,
        {
            "home_id": 1,
            "matches": [
                {
                    "home_id": 2,
                    "home_goals": buts_adversaire,
                    "away_goals": buts_nous,
                    "date": (datetime(2026, 8, 13, 18, tzinfo=UTC) - timedelta(days=7)).isoformat(),
                    "league_id": LEAGUE,
                }
            ],
        },
        settings,
    )


# -- Le budget ---------------------------------------------------------------


def test_un_lot_qui_tient_dans_le_budget_ne_produit_aucune_fiche(migrated: Settings) -> None:
    """Classer trois dossiers sur trois n'apprend rien, et la ligne de budget
    ferait renoncer a un match qu'il y avait tout le temps de traiter."""
    fiche = research.sheet([_event(i) for i in range(1, 4)], migrated)

    assert fiche.crowded is False
    assert fiche.dossiers == []


def test_un_lot_trop_grand_est_declare_a_l_etroit(migrated: Settings) -> None:
    """**Le critere est une propriete, jamais la valeur du jour.**

    Le budget se regle : ecrire son nombre ici ferait casser le test le jour ou
    on le change, sans qu'aucune regle ait bouge.
    """
    fiche = research.sheet([_event(i) for i in range(1, 22)], migrated)

    assert fiche.lot == 21
    assert fiche.budget == value_of("recherche_dossiers", migrated)
    assert fiche.crowded is (fiche.lot > fiche.budget)


def test_la_fiche_produit_le_minimum_du_budget_et_du_lot(migrated: Settings) -> None:
    """**`min(budget, lot)`**, et une fiche de vingt lignes reproduirait le
    probleme qu'elle corrige."""
    events = []
    for index in range(1, 22):
        event_id = 100 + index
        _en_base(migrated, event_id, index)
        _aller(migrated, event_id, buts_adversaire=1)
        events.append(_event(index, event_id))

    fiche = research.sheet(events, migrated)

    assert len(fiche.dossiers) == min(fiche.budget, fiche.lot) == fiche.available


def test_sur_un_lot_plus_court_que_le_budget_la_fiche_liste_tout(
    migrated: Settings,
) -> None:
    """**Changement voulu du lot 5.** Elle ne se rendait pas sous le seuil, au
    motif que « classer trois dossiers sur trois n'apprend rien ». C'est vrai
    d'un **tri** et faux d'un **ordre de traitement** : tous les matchs sont
    ouvrables, et le classement dit encore par lequel commencer.
    """
    events = []
    for index in range(1, 4):
        event_id = 300 + index
        _en_base(migrated, event_id, index)
        _aller(migrated, event_id, buts_adversaire=1)
        events.append(_event(index, event_id))

    fiche = research.sheet(events, migrated)

    assert not fiche.crowded
    assert fiche.available == 3
    assert len(fiche.dossiers) == 3


# -- Le classement -----------------------------------------------------------


def test_un_tie_ouvert_passe_devant_un_tie_joue(migrated: Settings) -> None:
    """Le critere le plus rentable mesure : les quatre selections de maniere d'un
    lot reel venaient toutes d'un tie a un but d'ecart ou d'un aller nul."""
    events = []
    for index, ecart in enumerate([3, 1, 3, 3, 3, 3, 3, 3, 3, 3], start=1):
        event_id = 200 + index
        _en_base(migrated, event_id, index)
        _aller(migrated, event_id, buts_adversaire=ecart)
        events.append(_event(index, event_id))

    fiche = research.sheet(events, migrated)

    assert [item.index for item in fiche.dossiers] == [2], "seul le tie ouvert est propose"
    assert "tie ouvert : ecart 1" in fiche.dossiers[0].motifs


def test_un_ecart_de_deux_buts_est_un_etat_a_part_entiere(migrated: Settings) -> None:
    """**Les « trois etats » etaient documentes, pas produits.**

    Le code rendait ouvert (+3), *rien du tout*, mort (-3) : a deux buts aucune
    raison ne se declenchait. Mesure sur le lot du 13/08/2026 — M12 (Egnatia,
    ecart 2) marquait comme un match sans manche aller, et se retrouvait a
    egalite avec M10, dont l'ecart valait 1. Un tour a deux buts se remonte, donc
    il ne vaut pas zero ; il ne vaut pas non plus un tour a un but.
    """
    events = []
    for index, ecart in enumerate([2] + [3] * 9, start=1):
        event_id = 300 + index
        _en_base(migrated, event_id, index)
        _aller(migrated, event_id, buts_adversaire=ecart)
        events.append(_event(index, event_id))

    fiche = research.sheet(events, migrated)
    premier = fiche.dossiers[0]

    assert premier.index == 1
    assert "tie ouvert : ecart 2" in premier.motifs
    # L'echelle est graduee : moins qu'un ecart de 1, plus que rien.
    assert research.OPEN_TIE_WEIGHTS[2] < research.OPEN_TIE_WEIGHTS[1]
    assert research.OPEN_TIE_WEIGHTS[1] < research.OPEN_TIE_WEIGHTS[0]


def test_l_equipe_menee_qui_recoit_est_un_modificateur_pas_un_critere(
    migrated: Settings,
) -> None:
    """L'obligation asymetrique reste exploitable, mais **recevoir ne cree pas
    l'enjeu** : elle change le scenario d'un tour qui existe deja.

    Pesee `STRONG`, elle egalait le fait qu'il y ait encore un tour a jouer et
    doublait donc le score d'un tie ouvert. Mesure sur le lot du 13/08/2026 : M1
    et M5 montaient a 7 quand M10 restait a 5, pour le meme ecart au cumul.
    """
    _en_base(migrated, 401, 1)
    _aller(migrated, 401, buts_adversaire=1)  # l'adversaire mene : nous sommes menes a domicile
    _en_base(migrated, 402, 2)
    _aller(migrated, 402, buts_adversaire=0, buts_nous=1)  # nous menons, l'autre est mene dehors

    events = [_event(1, 401), _event(2, 402)] + [_event(i) for i in range(3, 12)]
    fiche = research.sheet(events, migrated)
    scores = {item.index: item.score for item in fiche.dossiers}

    assert scores[1] > scores[2], "l'obligation a domicile pese plus"
    assert "l'equipe menee recoit" in next(i for i in fiche.dossiers if i.index == 1).motifs
    # Modificateur, donc strictement moins qu'un tour ouvert du meme ecart.
    assert research.OPEN_TIE_WEIGHTS[1] > research.MEDIUM


def test_un_dossier_sans_aucun_critere_n_est_pas_propose(migrated: Settings) -> None:
    """La fiche dirait « cherche ici » sur un dossier dont rien ne le justifie."""
    fiche = research.sheet([_event(i) for i in range(1, 22)], migrated)

    assert fiche.dossiers == []


def test_un_bloc_quasi_vide_sans_source_est_ecarte(migrated: Settings) -> None:
    """Le cas mesure : 2 lignes sur 24 sur une competition sans identifiant de
    ligue — chercher n'y a rien donne, et le savoir d'avance epargne une
    requete. Un bloc pauvre **rattache**, lui, est une piste."""
    # Nous menons de 1 : le tour est ouvert, mais l'obligation est pour l'autre,
    # qui se deplace. Un seul critere positif, donc le malus le compense.
    _en_base(migrated, 501, 1, rattachee=False)
    _aller(migrated, 501, buts_adversaire=0, buts_nous=1)

    fiche = research.sheet(
        [_event(1, 501, context=[])] + [_event(i) for i in range(2, 22)], migrated
    )

    assert fiche.dossiers == [], "le malus annule le tie ouvert"


def test_un_bloc_pauvre_mais_rattache_est_une_piste(migrated: Settings) -> None:
    """**Pauvre, et non vide** : la fixture d'origine montait un bloc a zero
    ligne, qui releve du cinquieme cas — vide sans cause typee — et non du bloc
    pauvre que ce test decrit. Elle disait l'etat du jour au lieu de la regle.
    """
    _en_base(migrated, 502, 1)
    _aller(migrated, 502, buts_adversaire=1)
    partiel = _dense()[: len(_dense()) // 4]

    fiche = research.sheet(
        [_event(1, 502, context=partiel)] + [_event(i) for i in range(2, 22)], migrated
    )

    assert [item.index for item in fiche.dossiers] == [1]
    assert "bloc pauvre" in fiche.dossiers[0].motifs


# -- Les questions et les liens ----------------------------------------------


def test_chaque_critere_emet_sa_question(migrated: Settings) -> None:
    """C'est la question qui fait la valeur, pas la liste de matchs : « cherche
    sur ce match » ne fait rien gagner, « ou se joue reellement ce match » clot
    un point en une requete."""
    _en_base(migrated, 601, 1)
    _aller(migrated, 601, buts_adversaire=1)
    contexte = [
        ("Effectif", "Tzur, plus vu depuis le 23/07"),
        ("Calendrier", "Club 1 dans 3j (Ekstraklasa)"),
    ]

    fiche = research.sheet(
        [_event(1, 601, context=contexte)] + [_event(i) for i in range(2, 22)], migrated
    )
    questions = fiche.dossiers[0].questions

    assert any("onze offensif" in q for q in questions), "l'equipe menee recoit"
    assert any("rejoue depuis" in q for q in questions), "la piste d'absence se confirme"
    assert any("rotation" in q for q in questions), "le match rapproche"


def test_les_questions_ne_se_repetent_pas(migrated: Settings) -> None:
    """Deux criteres peuvent viser la meme verification."""
    _en_base(migrated, 602, 1)
    _aller(migrated, 602, buts_adversaire=1)

    fiche = research.sheet([_event(1, 602)] + [_event(i) for i in range(2, 22)], migrated)
    questions = fiche.dossiers[0].questions

    assert len(questions) == len(set(questions))


def test_le_tennis_reclame_les_tours_non_recenses(migrated: Settings) -> None:
    """Le trou le plus couteux d'une journee de tennis : aucune de nos lignes ne
    porte le score ni la duree des tours deja joues ici."""
    contexte = [("Fraicheur", "arretees au 03/08\nLehecka 3 non comptes (Zverev, Fils)")]
    events = [_event(1, sport="tennis", context=contexte)] + [
        _event(i, sport="tennis") for i in range(2, 22)
    ]

    fiche = research.sheet(events, migrated)

    assert [item.index for item in fiche.dossiers] == [1]
    assert "tours de ce tournoi non recenses" in fiche.dossiers[0].motifs
    assert any("statistiques de service" in q for q in fiche.dossiers[0].questions)


def test_chaque_dossier_porte_une_requete_de_recherche(migrated: Settings) -> None:
    """Aucun lien profond n'est construisible sans identifiants que la base ne
    porte pas. La requete, elle, est le chemin qui a reellement fonctionne : les
    scores ATP d'une journee reelle ont ete obtenus par des extraits de recherche
    pointant vers atptour.com, la page refusant nos agents."""
    _en_base(migrated, 701, 1)
    _aller(migrated, 701, buts_adversaire=1)

    fiche = research.sheet([_event(1, 701)] + [_event(i) for i in range(2, 22)], migrated)

    assert fiche.dossiers[0].links == ['rechercher "Club 1 Adv 1 Allsvenskan 13/08/2026"']


# -- Ce que la fiche ne fait pas ---------------------------------------------


def test_aucun_critere_ne_regarde_une_cote(migrated: Settings) -> None:
    """Trier sur le prix rendrait le tri circulaire : on ne chercherait jamais la
    ou le marche est confiant, donc on ne trouverait jamais l'information qui le
    contredit. Le module ne lit donc aucun marche — un lot dont les cotes
    changent du tout au tout rend la meme fiche."""
    from myassistantbet.services.render import Outcome

    _en_base(migrated, 801, 1)
    _aller(migrated, 801, buts_adversaire=1)
    autres = [_event(i) for i in range(2, 22)]

    nu = research.sheet([_event(1, 801)] + autres, migrated)
    cote = _event(1, 801)
    cote.markets = {"h2h": [Outcome("Club 1", 1.02), Outcome("Adv 1", 40.0)]}
    charge = research.sheet([cote] + autres, migrated)

    assert [item.motifs for item in nu.dossiers] == [item.motifs for item in charge.dossiers]
    assert [item.score for item in nu.dossiers] == [item.score for item in charge.dossiers]


# -- Ce que le prompt en rend ------------------------------------------------


def _lot(settings: Settings, matchs: int) -> int:
    from myassistantbet.services import board as board_service

    session_id = 0
    for index in range(1, matchs + 1):
        event_id = 900 + index
        _en_base(settings, event_id, index)
        _aller(settings, event_id, buts_adversaire=1)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id


def test_un_lot_qui_tient_dans_le_budget_recoit_un_ordre_et_non_un_tri(
    migrated: Settings,
) -> None:
    """**Le texte change avec le lot, et c'est tout ce que `crowded` decide.**

    « Les matchs non recherches se rendent en lecture » est vrai au-dela du
    budget et **hors sujet en deca** : sur un lot de 3 avec un budget de 10,
    aucun match n'est ecarte faute de place, et laisser la phrase ferait
    renoncer a des dossiers qu'il y avait tout le temps d'ouvrir.
    """
    from myassistantbet.services.prompt import build_prompt

    rendu = build_prompt(
        _lot(migrated, 3), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
    ).body
    corps = " ".join(rendu.split())

    assert "BUDGET DE RECHERCHE" in corps
    assert "**tous les matchs sont ouvrables**" in corps
    assert "un **ordre de traitement**" in corps
    assert "les matchs **non recherchés** se rendent en `lecture`" not in corps


def test_le_plafond_n_est_jamais_un_objectif(migrated: Settings) -> None:
    """**Une liste gonflee rend `lecture` indiscernable d'un fait date**, et
    c'est la seule comparaison que ce releve puisse produire."""
    from myassistantbet.services.prompt import build_prompt

    corps = " ".join(
        build_prompt(
            _lot(migrated, 12), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
        ).body.split()
    )

    assert "Ce nombre est un **plafond, jamais un objectif**." in corps
    assert "si un bloc n'appelle aucune recherche, ne l'ouvre pas" in corps


def test_le_prompt_ouvre_la_fiche_au_dela_du_budget(migrated: Settings) -> None:
    """Sur 21 matchs, la consigne « si la section A laisse trop de trous, c'est
    un PASSE » appliquee a la lettre produirait 18 PASSE. Le prompt dit donc
    qu'un dossier non ouvert par manque de budget est un resultat attendu."""
    from myassistantbet.services.prompt import build_prompt

    rendu = build_prompt(
        _lot(migrated, 21), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
    ).body
    # Sur le texte **remis a plat** : la coupure de ligne n'est pas la regle, et
    # une reformulation qui la deplace ne doit pas casser une assertion de sens.
    corps = " ".join(rendu.split())

    assert "Ce lot comporte 21 matchs" in corps
    # **Le budget se compte par prompt, et l'annoncer « par session » etait
    # faux.** Mesure du 14/08/2026 : une session genere 3 a 20 prompts, et le
    # meme nombre etait ecrit dans chacun — vingt instances lisant « une session
    # couvre 7 dossiers » sans qu'aucune sache que dix-neuf autres lisaient la
    # meme phrase. Le nombre est vrai par prompt et faux par session.
    assert "**Ce prompt** ouvre" in corps
    assert "le budget se compte par prompt, et une journée en produit plusieurs" in corps
    assert "Une session couvre environ" not in corps
    # « non recherches » et non « non couverts » : le second designait aussi un
    # match dont la **collecte** n'a pas eu lieu, ou `lecture` est vide de sens.
    assert "les matchs **non recherchés** se rendent en `lecture`" in corps
    assert "**c'est un résultat attendu, pas un manquement**" in corps
    assert "Leur bloc est là, plein : il y a quelque chose à lire." in corps
    assert "À CHERCHER EN PRIORITÉ" in rendu
    # **Une propriete, jamais la valeur du jour** : le budget se regle, et
    # recopier son nombre ferait casser ce test le jour ou on le change sans
    # qu'aucune regle ait bouge.
    budget = value_of("recherche_dossiers", migrated)
    assert rendu.count("   -> rechercher") == min(budget, 21), (
        "une entree par dossier, plafonnee a min(budget, lot)"
    )


def test_le_prompt_dit_qu_aucune_cote_ne_trie(migrated: Settings) -> None:
    """Le preambule limite deja les cotes a deux usages : en ajouter un
    troisieme affaiblirait les deux autres, et trier sur le prix ne ferait
    jamais chercher la ou le marche est confiant."""
    from myassistantbet.services.prompt import build_prompt

    corps = " ".join(
        build_prompt(
            _lot(migrated, 21), settings=migrated, now=datetime(2026, 8, 13, 12, tzinfo=UTC)
        ).body.split()
    )

    assert "Elle ne regarde aucune cote" in corps


# -- Le terrain neutre, et la question qui nomme ce qui manque ----------------


def test_un_terrain_neutre_monte_le_dossier(migrated: Settings) -> None:
    """Le fait portait deux des huit selections d'un lot reel. La question ne
    porte pas sur le lieu, qui est deja ecrit, mais sur ce qu'il change : le
    public est le vrai sujet."""
    from myassistantbet.services.context import NEUTRAL_MARK

    contexte = _dense() + [
        (
            "Lieu",
            f"Stadion Beroe, Stara Zagora (BGR) — {NEUTRAL_MARK}, Club 1 recoit hors de son pays",
        )
    ]
    fiche = research.sheet(
        [_event(1, context=contexte)] + [_event(i) for i in range(2, 22)], migrated
    )

    assert [item.index for item in fiche.dossiers] == [1]
    assert "terrain neutre" in fiche.dossiers[0].motifs
    assert "quel public est attendu" in fiche.dossiers[0].questions[0]


def test_un_lieu_ordinaire_ne_monte_rien(migrated: Settings) -> None:
    """La ligne « Lieu » est desormais systematique : sa seule presence ne dit
    rien, c'est le marqueur qui compte."""
    contexte = _dense() + [("Lieu", "Bravida Arena, Goteborg (SWE)")]

    fiche = research.sheet(
        [_event(1, context=contexte)] + [_event(i) for i in range(2, 22)], migrated
    )

    assert fiche.dossiers == []


def test_la_question_d_un_bloc_pauvre_nomme_les_lignes_manquantes(migrated: Settings) -> None:
    """« Ce que le bloc ne porte pas » etait un doublon mou et ne disait pas ou
    aller. L'application connait les lignes absentes : elle les nomme."""
    _en_base(migrated, 1001, 1)
    _aller(migrated, 1001, buts_adversaire=1)

    fiche = research.sheet(
        [_event(1, 1001, context=[("Classement", "1er")])] + [_event(i) for i in range(2, 22)],
        migrated,
    )
    question = next(q for q in fiche.dossiers[0].questions if q.startswith("Bloc a "))

    assert "ni enjeu, ni forme 5" in question
    assert "compte rendu" in question
    assert "ce que le bloc ne porte pas" not in question.lower()


def test_un_joueur_dont_les_lignes_tiennent_sur_deux_matchs_est_designe(
    migrated: Settings,
) -> None:
    """**Le critere de densite regarde le bloc, celui-ci regarde le joueur**, et
    les deux ne se recouvrent pas. Sur la soiree du 12/08, les deux blocs les
    plus vides du lot au niveau joueur — `Forme D/1` pour Lajal, `Forme VD/2`
    pour Mejia — avaient un bloc complet par ailleurs : aucun critere ne les
    designait, et la fiche a propose six dossiers portant tous la meme question.

    Seuil mesure avant d'etre ecrit : sur les 406 blocs de tennis archives,
    « moins de trois matchs » designe 5 blocs, soit 1 %."""
    maigre = _event(
        1, sport="tennis", context=[("Forme", "Mark Lajal D/1 | Dalibor Svrcina VDVDDDDVVD/10")]
    )
    fourni = _event(2, sport="tennis", context=[("Forme", "A VDVDDDDVVD/10 | B DDVDDDVDVD/10")])
    lot = [maigre, fourni] + [_event(i, sport="tennis", context=[]) for i in range(3, 9)]

    fiche = research.sheet(lot, migrated)

    dossiers = {item.index: item for item in fiche.dossiers}
    assert 1 in dossiers, "le joueur a un match derriere ses lignes"
    assert 2 not in dossiers or "match(s) derriere" not in dossiers[2].motifs
    assert any("Mark Lajal" in question for question in dossiers[1].questions)
    assert all("Dalibor Svrcina" not in question for question in dossiers[1].questions), (
        "seul le joueur maigre est designe"
    )


def test_le_departage_ne_se_fait_plus_sur_l_heure_du_coup_d_envoi(migrated: Settings) -> None:
    """**Le tri par heure n'est pas neutre, il est oriente.**

    A egalite de score, les dossiers etaient departages par l'index du bloc,
    c'est-a-dire par l'heure du coup d'envoi. Or les diffuseurs programment les
    grosses affiches en dernier, et l'audience est correlee a la couverture
    presse, donc a ce qu'une recherche peut trouver : trier par heure croissante
    trie approximativement par interet decroissant, systematiquement plutot
    qu'accidentellement.

    Deux criteres signifiants le remplacent — ecart au cumul croissant, puis
    densite decroissante. L'index ne sert plus que de dernier recours, pour que
    l'ordre reste deterministe.
    """
    serre = research.Dossier(index=9, label="tard", gap=0, density=40)
    large = research.Dossier(index=1, label="tot", gap=2, density=40)
    for dossier in (serre, large):
        dossier.reasons.append(research.Reason(research.MEDIUM, "meme score", ""))

    assert sorted([large, serre], key=lambda item: item.rank_key)[0] is serre, (
        "a score egal, le tie le plus serre passe avant, meme s'il part plus tard"
    )

    # A ecart egal, c'est le bloc le mieux fourni : la recherche y complete au
    # lieu de tout reconstruire.
    fourni = research.Dossier(index=9, label="fourni", gap=1, density=90)
    pauvre = research.Dossier(index=1, label="pauvre", gap=1, density=30)
    for dossier in (fourni, pauvre):
        dossier.reasons.append(research.Reason(research.MEDIUM, "meme score", ""))

    assert sorted([pauvre, fourni], key=lambda item: item.rank_key)[0] is fourni


# -- Le motif de l'echec decide du budget ------------------------------------


def test_un_bloc_vide_faute_de_rattachement_ne_vaut_aucun_budget(migrated: Settings) -> None:
    """**Deux blocs vides ne valent pas le meme budget.**

    Un bloc vide parce que personne n'a pose la question ne se comble pas par
    une recherche : il se comble par une saisie, et le dossier couterait alors
    une place a un match ou chercher sert vraiment. Le motif le nomme au lieu
    de le laisser deviner d'une densite a zero.
    """
    _en_base(migrated, 701, 1, rattachee=False)
    _aller(migrated, 701, buts_adversaire=0, buts_nous=1)

    fiche = research.sheet(
        [_event(1, 701, context=[])] + [_event(i) for i in range(2, 22)], migrated
    )

    assert fiche.dossiers == []


def test_un_bloc_vide_faute_de_couverture_est_le_meilleur_dossier(migrated: Settings) -> None:
    """L'inverse exact : la competition est rattachee, le fournisseur a ete
    interroge et ne sert rien. La recherche y est le **seul** chemin, donc c'est
    la que le budget rapporte le plus."""
    _en_base(migrated, 702, 1)
    context_service.record_outcome(702, context_service.CAUSE_NOT_COVERED, migrated)

    fiche = research.sheet(
        [_event(1, 702, context=[])] + [_event(i) for i in range(2, 22)], migrated
    )

    assert [item.index for item in fiche.dossiers] == [1]
    assert "non interrogés" in fiche.dossiers[0].motifs
    question = next(q for q in fiche.dossiers[0].questions if "non couverte" in q)
    assert "compte rendu" in question


def test_une_source_injoignable_garde_un_budget_ordinaire(migrated: Settings) -> None:
    """**Le motif dit pourquoi c'est vide, pas que ce sera rempli a temps.**

    Mesure du 14/08/2026 : rien ne rejoue le contexte tout seul. Le
    planificateur porte le scan, les sources gratuites et un balayage de
    compositions — lequel exige un `apifootball_fixture_id`, donc ne peut meme
    pas reparer le cas ou le rapprochement a echoue. Le coup d'envoi, lui, ne
    recule pas.
    """
    _en_base(migrated, 703, 1)
    context_service.record_outcome(703, context_service.CAUSE_UNREACHABLE, migrated)

    fiche = research.sheet(
        [_event(1, 703, context=[])] + [_event(i) for i in range(2, 22)], migrated
    )

    assert [item.index for item in fiche.dossiers] == [1]
    assert "source injoignable" in fiche.dossiers[0].motifs
    # Le meme poids qu'un bloc pauvre ordinaire : ni promu, ni ecarte.
    assert fiche.dossiers[0].score == research.MEDIUM


def test_un_bloc_vide_sans_cause_typee_est_un_dossier_fort(migrated: Settings) -> None:
    """**Apres le typage, il ne devrait plus en exister.**

    Les quatre causes couvrent le football entier ; s'il en reste un, c'est un
    cinquieme cas que personne n'a nomme. Le ranger par defaut dans l'une des
    quatre lui donnerait un budget decide au hasard — on ne sait pas pourquoi il
    est vide, donc on ne peut pas affirmer qu'une recherche n'y servirait a rien.
    """
    _en_base(migrated, 704, 1)

    fiche = research.sheet(
        [_event(1, 704, context=[])] + [_event(i) for i in range(2, 22)], migrated
    )

    assert [item.index for item in fiche.dossiers] == [1]
    assert "cause inconnue" in fiche.dossiers[0].motifs
    assert fiche.dossiers[0].score == research.STRONG


def test_un_bloc_de_tennis_vide_n_est_pas_un_cinquieme_cas(migrated: Settings) -> None:
    """Le typage ne couvre que le football : le contexte sportif n'existe pas
    ailleurs, et un bloc de tennis pauvre a d'autres sources. L'y ranger ferait
    journaliser un defaut sur un comportement normal."""
    fiche = research.sheet(
        [_event(1, sport="tennis", context=[])] + [_event(i) for i in range(2, 22)],
        migrated,
    )

    motifs = " ".join(item.motifs for item in fiche.dossiers)
    assert "cause inconnue" not in motifs
