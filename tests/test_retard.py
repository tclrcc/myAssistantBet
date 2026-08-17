"""Stratifier la population tardive par le retard.

L'ecart de residu entre principale et tardive — +0,145 par selection — est le
resultat le plus interessant de la page, et il etait rendu comme un bloc unique.
Or **une selection ecrite quatre minutes apres le coup d'envoi et une ecrite
quatre-vingt-dix minutes apres ne decrivent pas la meme chose** : la premiere
peut n'etre qu'un retard d'import, la seconde suppose de connaitre le
deroulement du match.

Les deux reponses possibles sont utiles — residu croissant, la contamination est
demontree ; residu plat, la population est un artefact d'import. L'absence de
stratification n'en donnait aucune.
"""

from __future__ import annotations

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import board as board_service
from myassistantbet.services.history import (
    LATE_BANDS,
    add_pick,
    late,
    populations,
    refresh_late,
    set_event,
    set_result,
)
from myassistantbet.services.manual import build, save

DEBUT = "2026-08-14T20:00:00Z"


def _match(settings: Settings, nom: str = "Lyon", debut: str = DEBUT) -> int:
    event_id = save(
        build(
            "football",
            "Amical",
            nom,
            f"Adv {nom}",
            "2099-01-01",
            "20:45",
            f"{nom} 2.00\nAdv {nom} 2.00",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    db.execute(
        "UPDATE events SET commence_time = ? WHERE id = ?", (debut, event_id), settings=settings
    )
    return event_id


def _tardive(
    settings: Settings, session_id: int, event_id: int, minutes: int, gagne: bool = True
) -> int:
    """Une selection ecrite `minutes` apres le coup d'envoi.

    Elle passe par `add_pick` — donc par le vrai chemin — puis son `created_at`
    est deplace et la regle rejouee. Ecrire la ligne en SQL testerait la
    fixture ; deplacer l'heure et rejouer la regle teste la regle.
    """
    pick_id = add_pick(
        session_id,
        tier="fun",
        market="O/U 2.5",
        selection=f"Over {minutes}-{gagne}-{event_id}",
        event_id=str(event_id),
        price="2.00",
        independence_note="angles indépendants (fixture)",
        settings=settings,
    )
    heure = f"2026-08-14T{20 + minutes // 60:02d}:{minutes % 60:02d}:00Z"
    db.execute("UPDATE picks SET created_at = ? WHERE id = ?", (heure, pick_id), settings=settings)
    refresh_late(event_id, settings)
    set_result(pick_id, "win" if gagne else "loss", settings)
    return pick_id


# -- La colonne, et la règle qui l'écrit -------------------------------------


def test_le_retard_est_ecrit_en_minutes(migrated: Settings) -> None:
    """Le drapeau dit *si*, la colonne dit *de combien*."""
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _tardive(migrated, session_id, event_id, minutes=42)

    ligne = db.query("SELECT tardive, late_minutes FROM picks", settings=migrated)[0]
    assert ligne["tardive"] == 1
    assert ligne["late_minutes"] == 42


def test_une_selection_anterieure_n_a_pas_un_retard_nul(migrated: Settings) -> None:
    """**NULL et non zéro**, et la distinction n'est pas cosmétique.

    Écrire 0 la ferait entrer dans la première bande et gonflerait de 178 lignes
    une population qui en porte 52 — le genre de défaut qui ne casse rien et se
    lit comme une mesure.
    """
    event_id = _match(migrated, debut="2099-01-01T20:45:00Z")
    session_id = board_service.toggle_selection(event_id, True, migrated)
    add_pick(
        session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        event_id=str(event_id),
        price="1.45",
        settings=migrated,
    )

    ligne = db.query("SELECT tardive, late_minutes FROM picks", settings=migrated)[0]
    assert ligne["tardive"] == 0
    assert ligne["late_minutes"] is None


def test_un_report_leve_le_retard_et_ses_minutes(migrated: Settings) -> None:
    """**Les deux colonnes dans le même UPDATE**, jamais deux règles parallèles.

    Un match reporté n'a pas commencé : la sélection écrite « après » l'ancien
    horaire n'a rien vu. Si seule `tardive` retombait, la ligne garderait un
    retard qui n'existe plus — c'est exactement le cas où deux règles écrites
    séparément divergent, et il coûte cher.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _tardive(migrated, session_id, event_id, minutes=90)
    assert db.query("SELECT late_minutes FROM picks", settings=migrated)[0]["late_minutes"] == 90

    db.execute(
        "UPDATE events SET commence_time = '2099-01-01T20:45:00Z' WHERE id = ?",
        (event_id,),
        settings=migrated,
    )
    refresh_late(event_id, migrated)

    ligne = db.query("SELECT tardive, late_minutes FROM picks", settings=migrated)[0]
    assert ligne["tardive"] == 0
    assert ligne["late_minutes"] is None, "un report ne laisse pas un retard derrière lui"


def test_detacher_un_match_efface_le_retard(migrated: Settings) -> None:
    """Le retard se démontre contre un coup d'envoi ; sans match, il n'y en a plus."""
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    pick_id = _tardive(migrated, session_id, event_id, minutes=30)

    set_event(pick_id, "", migrated)

    ligne = db.query("SELECT tardive, late_minutes FROM picks", settings=migrated)[0]
    assert ligne["tardive"] == 0 and ligne["late_minutes"] is None


def test_rattacher_un_match_recalcule_le_retard(migrated: Settings) -> None:
    """`set_event` passe par la **règle commune** et non par un second calcul.

    Recalculer le retard à la main y aurait fait une seconde écriture de la même
    règle, qui aurait divergé de `_LATE_RULE` au premier ajustement — et le
    premier ajustement est arrivé au lot suivant, avec les minutes.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    pick_id = add_pick(
        session_id,
        tier="fun",
        market="O/U 2.5",
        selection="Over 2.5",
        price="2.00",
        settings=migrated,
    )
    db.execute(
        "UPDATE picks SET created_at = '2026-08-14T21:15:00Z' WHERE id = ?",
        (pick_id,),
        settings=migrated,
    )

    set_event(pick_id, str(event_id), migrated)

    ligne = db.query("SELECT tardive, late_minutes FROM picks", settings=migrated)[0]
    assert ligne["tardive"] == 1 and ligne["late_minutes"] == 75


# -- Les trois bandes --------------------------------------------------------


def test_les_trois_bandes_sont_posees_d_avance() -> None:
    """Elles ne sortent pas des données.

    Un découpage choisi après avoir regardé serait la faute que cette page a mis
    huit lots à corriger — la cellule `SAFE ∩ confiance 4`. Les bornes viennent
    de ce qu'un retard **suppose** : sous un quart d'heure une saisie peut
    n'avoir rien vu ; au-delà d'une heure, un match de football est à la
    mi-temps.
    """
    assert [(bas, haut) for _, bas, haut in LATE_BANDS] == [(0, 15), (15, 60), (60, None)]


@pytest.mark.parametrize(
    ("minutes", "attendue"),
    [(0, 0), (14, 0), (15, 1), (59, 1), (60, 2), (240, 2)],
)
def test_la_borne_basse_est_incluse_et_la_haute_exclue(
    migrated: Settings, minutes: int, attendue: int
) -> None:
    """Même convention que les bandes de cote (`Tier.covers`).

    Deux conventions dans la même base se liraient à l'envers, et une sélection
    à 15 minutes pile tomberait dans la mauvaise moitié selon le lecteur.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _tardive(migrated, session_id, event_id, minutes=minutes)

    bandes = late(migrated).bands

    assert [band.settled for band in bandes][attendue] == 1
    assert sum(band.settled for band in bandes) == 1


def test_aucune_bande_n_est_fusionnee_sous_un_seuil(migrated: Settings) -> None:
    """**Le point du chantier.** Une bande à trois sélections s'affiche.

    Les fondre reproduirait le bloc unique que la stratification défait, et
    c'est l'intervalle — large — qui doit dire que la bande ne conclut rien.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    for index in range(3):
        _tardive(migrated, session_id, event_id, minutes=90 + index, gagne=True)

    bandes = late(migrated).bands

    assert len(bandes) == 3, "les trois bandes existent même vides"
    lointaine = bandes[2]
    assert lointaine.settled == 3 and lointaine.won == 3
    assert lointaine.interval is not None
    bas, haut = lointaine.interval
    assert bas < 0.5, "trois sur trois n'écarte rien : l'intervalle le dit"


def test_le_residu_se_lit_par_selection(migrated: Settings) -> None:
    """Trois bandes d'effectifs très différents ne se comparent pas par leur
    écart brut : celle qui porte le plus de lignes porte mécaniquement le plus
    d'écart.

    À la cote 2.00 chaque sélection paie 0,50 victoire : deux victoires sur deux
    valent +1,00 d'écart, soit +0,500 par sélection ; une sur quatre vaut
    −1,00, soit −0,250.
    """
    event_id = _match(migrated)
    autre = _match(migrated, "Nice")
    session_id = board_service.toggle_selection(event_id, True, migrated)
    board_service.toggle_selection(autre, True, migrated)
    for index in range(2):
        _tardive(migrated, session_id, event_id, minutes=index, gagne=True)
    for index in range(4):
        _tardive(migrated, session_id, autre, minutes=90 + index, gagne=index == 0)

    proche, _, lointaine = late(migrated).bands

    assert proche.settled == 2 and proche.per_selection == 0.5
    assert lointaine.settled == 4 and lointaine.per_selection == -0.25


def test_un_retard_inconnu_n_entre_dans_aucune_bande(migrated: Settings) -> None:
    """Le ranger dans la première le ferait passer pour une saisie immédiate.

    Le cas existe : une ligne antérieure à la migration 055 dont le match a été
    détaché depuis porte `tardive` sans minutes.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _tardive(migrated, session_id, event_id, minutes=42)
    db.execute("UPDATE picks SET late_minutes = NULL", settings=migrated)

    releve = late(migrated)

    assert releve.settled == 1, "elle reste dans la population tardive"
    assert sum(band.settled for band in releve.bands) == 0, "et dans aucune bande"


def test_l_exploratoire_reste_hors_des_bandes(migrated: Settings) -> None:
    """Les trois populations ne se mélangent jamais, bandes comprises."""
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    pick_id = add_pick(
        session_id,
        tier="giga_fun",
        market="1N2",
        selection="Lyon exploratoire",
        event_id=str(event_id),
        price="9.00",
        exploratory=True,
        settings=migrated,
    )
    db.execute(
        "UPDATE picks SET created_at = '2026-08-14T21:00:00Z' WHERE id = ?",
        (pick_id,),
        settings=migrated,
    )
    refresh_late(event_id, migrated)
    set_result(pick_id, "win", migrated)

    releve = late(migrated)

    assert releve.settled == 0
    assert sum(band.settled for band in releve.bands) == 0
    assert populations(migrated).consistent


def test_la_stratification_ne_deplace_aucune_population(isolated_settings: Settings) -> None:
    """**Règle de travail n°6.** Lignes écrites en SQL sous le schéma d'avant.

    Vérifié en plus sur une copie de la base servie : `recorded` 230, principale
    178, tardive 52, exploratoire 0 — identiques au relevé du lot 2. La
    migration n'ajoute qu'une colonne dérivée de `created_at` et
    `commence_time`, déjà en base.
    """
    from myassistantbet.services.history import analysis

    from .helpers import migre_jusqu_a

    migre_jusqu_a(isolated_settings, 54)
    event_id = _match(isolated_settings)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for heure, resultat in (("2026-08-14T19:00:00Z", "win"), ("2026-08-14T20:30:00Z", "loss")):
        db.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, result, tardive, created_at) "
            "VALUES (?, ?, 'safe', '1N2', ?, 2.00, 4, 0, ?, ?, ?)",
            (
                session_id,
                event_id,
                f"choix {heure}",
                resultat,
                1 if heure >= DEBUT else 0,
                heure,
            ),
            settings=isolated_settings,
        )

    db.run_migrations(isolated_settings)
    report, compte = analysis(isolated_settings), populations(isolated_settings)

    assert report.recorded == 2 and report.consistent
    assert (compte.total, compte.main, compte.exploratory, compte.late) == (2, 1, 0, 1)
    # Le rétro-remplissage suit exactement la dérivation : 20:30 moins 20:00.
    lignes = db.query(
        "SELECT tardive, late_minutes FROM picks ORDER BY created_at", settings=isolated_settings
    )
    assert [ligne["late_minutes"] for ligne in lignes] == [None, 30]
