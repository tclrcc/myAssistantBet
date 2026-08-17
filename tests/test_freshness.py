"""Une source morte repond encore.

**La panne redoutee n'a pas eu lieu, et c'est ce qui rend ces tests
necessaires.** Aucune ligne tennis ne passait par les depots Sackmann supprimes :
les six URL amont repondent 200 et les collectes ont tourne le matin du
17/08/2026. Rien n'etait casse — et rien ne l'aurait dit.

Un depot supprime rend 404 et se voit. Un fichier hebdomadaire qui cesse d'etre
publie rend 200, le meme classeur, indefiniment : la sortie de l'echec est
identique a celle du cas ordinaire, une septieme fois.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import freshness
from myassistantbet.services import ingestion as ingestion_service

MAINTENANT = datetime(2026, 8, 17, 12, 0, tzinfo=UTC)


def _jours(n: int) -> str:
    return (MAINTENANT - timedelta(days=n)).date().isoformat()


def _heures(n: int) -> str:
    return (MAINTENANT - timedelta(hours=n)).strftime("%Y-%m-%dT%H:%M:%SZ")


# -- L'escalade dans le bloc -------------------------------------------------


@pytest.mark.parametrize(
    ("age", "attendu"),
    [
        (0, ""),
        (3, ""),
        (7, ""),
        (8, "source non rafraichie"),
        (21, "source non rafraichie"),
        (22, "SOURCE FIGEE"),
        (30, "SOURCE FIGEE"),
    ],
)
def test_les_trois_etats_se_calculent_sur_l_age(age: int, attendu: str) -> None:
    """**Les bornes, et elles sont celles du brief.**

    Sous huit jours rien ne se dit : trois a quatre jours de retard sont le
    régime normal d'un fichier hebdomadaire publié après coup, et une ligne qui
    crierait chaque jour cesserait d'informer au bout d'un lot — le défaut de
    « A relever » sur vingt-quatre blocs.
    """
    note = freshness.note_for(_jours(age), MAINTENANT)

    assert attendu in note
    if not attendu:
        assert note == "", "rien ne se dit sous le seuil"


def test_une_source_a_j_moins_30_porte_la_mention_d_escalade() -> None:
    """**Critère d'acceptation du §0.2.**

    La mention nomme les lignes concernées : sans elles, « source figée » dit
    qu'il y a un problème sans dire lequel des douze lignes du bloc il touche.
    """
    note = freshness.note_for(_jours(30), MAINTENANT)

    assert note.startswith("SOURCE FIGEE depuis le 18/07")
    for ligne in ("Forme", "Usure", "Profil", "Marge", "Niveau adv."):
        assert ligne in note
    assert "ne les traite pas comme la forme du moment" in note


def test_une_date_illisible_ne_produit_aucune_mention() -> None:
    """En cas de doute, rien — la règle du projet.

    Une mention « source figée » posée sur une date qu'on n'a pas su lire
    accuserait la collecte d'un défaut de lecteur.
    """
    assert freshness.note_for("", MAINTENANT) == ""
    assert freshness.note_for("pas une date", MAINTENANT) == ""


# -- La stagnation -----------------------------------------------------------


def test_un_premier_releve_ne_conclut_rien(migrated: Settings) -> None:
    """On ne dit pas qu'une source ne bouge plus quand on ne l'a vue qu'une fois."""
    fige = freshness.record(freshness.TENNISDATA, "atp", _jours(2), migrated, now=_heures(0))

    assert fige is None
    etat = freshness.state(freshness.TENNISDATA, "atp", migrated, MAINTENANT)
    assert etat.source_as_of == _jours(2)
    assert etat.moved_at == _heures(0)


def test_une_source_qui_stagne_48h_produit_une_ligne_source_figee(migrated: Settings) -> None:
    """**Critère d'acceptation du §0.2.**

    C'est le mécanisme qui manquait : `tennis_history_state` date la
    **tentative**, qui avance tous les matins quoi qu'il arrive. Ici c'est le
    **contenu** qui est daté, et c'est lui qui cesse de bouger.
    """
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(60))
    # Deux jours et demi plus tard, la source répond encore et rend le même
    # dernier match.
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))

    assert fige is not None
    assert fige.block_type == ingestion_service.SOURCE
    assert fige.reason == ingestion_service.SOURCE_FIGEE
    assert "n'avance plus" in fige.detail and "14/08" in fige.detail
    assert "tennisdata" in fige.payload and "atp" in fige.payload


def test_une_stagnation_sous_le_seuil_ne_dit_rien(migrated: Settings) -> None:
    """Une publication hebdomadaire manquée d'un jour est le cas ordinaire."""
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(30))
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))

    assert fige is None


def test_la_stagnation_se_mesure_depuis_le_dernier_mouvement(migrated: Settings) -> None:
    """**Jamais entre deux exécutions consécutives**, et c'est le cœur du calcul.

    Une source relancée trois fois par jour ferait sinon trois comparaisons de
    moins de 48 h et ne stagnerait jamais, quel que soit son âge réel. Le
    planificateur tourne tous les jours : ce défaut-là aurait rendu le
    mécanisme entièrement inopérant.
    """
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(72))
    for heures in (48, 24, 12):
        freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(heures))

    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))

    assert fige is not None, "trois relances rapprochées ne remettent pas le compteur à zéro"


def test_une_source_qui_avance_remet_le_compteur(migrated: Settings) -> None:
    """Une source vivante avance ; c'est la définition retenue."""
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-07", migrated, now=_heures(96))
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))

    assert fige is None
    etat = freshness.state(freshness.TENNISDATA, "atp", migrated, MAINTENANT)
    assert etat.moved_at == _heures(0) and etat.source_as_of == "2026-08-14"


def test_une_source_qui_recule_ne_compte_pas_comme_un_mouvement(migrated: Settings) -> None:
    """**Un fichier republié amputé ne doit pas passer pour un rafraîchissement.**

    Sackmann le pratiquait, et le lot 3 le notait. Faire repartir le compteur en
    perdant des données serait l'inverse de ce qu'un témoin de fraîcheur doit
    dire.
    """
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(72))
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-01", migrated, now=_heures(0))

    assert fige is not None, "un recul est une stagnation, pas une avancée"
    etat = freshness.state(freshness.TENNISDATA, "atp", migrated, MAINTENANT)
    assert etat.source_as_of == "2026-08-14", "la date la plus avancée est conservée"


def test_les_deux_circuits_vivent_leur_vie(migrated: Settings) -> None:
    """L'ATP et la WTA sont deux fichiers ; l'un peut geler quand l'autre vit."""
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(72))
    freshness.record(freshness.TENNISDATA, "wta", "2026-07-01", migrated, now=_heures(72))

    assert (
        freshness.record(freshness.TENNISDATA, "atp", "2026-08-16", migrated, now=_heures(0))
        is None
    )
    assert (
        freshness.record(freshness.TENNISDATA, "wta", "2026-07-01", migrated, now=_heures(0))
        is not None
    )


def test_le_pire_des_circuits_fait_foi(migrated: Settings) -> None:
    """**Rendre le meilleur des deux tairait exactement le cas qu'on veut voir.**

    Un bloc de tennis peut porter deux joueurs de deux circuits.
    """
    freshness.record(freshness.TENNISDATA, "atp", _jours(2), migrated, now=_heures(0))
    freshness.record(freshness.TENNISDATA, "wta", _jours(40), migrated, now=_heures(0))

    pire = freshness.worst({"atp", "wta"}, migrated, MAINTENANT)

    assert pire.scope == "wta" and pire.level == freshness.FROZEN
    assert "SOURCE FIGEE" in pire.note


def test_un_circuit_jamais_releve_ne_produit_aucune_mention(migrated: Settings) -> None:
    """Une base neuve n'est pas une source figée.

    Écrire « figée » là où l'on n'a jamais collecté ferait chercher une panne
    amont là où il n'y a qu'un rafraîchissement jamais lancé — même règle que
    l'Elo sur une base vierge.
    """
    assert freshness.worst({"atp"}, migrated, MAINTENANT).note == ""
    assert freshness.state(freshness.TENNISDATA, "atp", migrated, MAINTENANT).note == ""


# -- La chaîne complète ------------------------------------------------------


def test_la_ligne_source_figee_arrive_en_base(migrated: Settings) -> None:
    """Le rejet se journalise là où tout ce qui se perd se journalise déjà.

    `session_id` vaut NULL : ce n'est pas une session qui a perdu quelque chose,
    c'est la collecte. Une seconde table de pertes aurait divergé de celle-ci.
    """
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(72))
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))
    assert fige is not None

    ingestion_service.record(None, [fige], migrated)

    lignes = db.query(
        "SELECT session_id, block_type, reason, detail FROM ingestion_rejects",
        settings=migrated,
    )
    assert len(lignes) == 1
    assert lignes[0]["session_id"] is None
    assert lignes[0]["block_type"] == ingestion_service.SOURCE
    assert lignes[0]["reason"] == ingestion_service.SOURCE_FIGEE


def test_le_motif_figure_au_releve_de_la_page(migrated: Settings) -> None:
    """Un motif hors vocabulaire retomberait sur « refusée à l'écriture ».

    Le compte de `/stats` se fait sur `(block_type, reason)` : un motif inconnu
    y serait rangé sous un libellé qui décrit un tout autre problème.
    """
    freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(72))
    fige = freshness.record(freshness.TENNISDATA, "atp", "2026-08-14", migrated, now=_heures(0))
    assert fige is not None
    ingestion_service.record(None, [fige], migrated)

    releve = ingestion_service.summary(migrated)

    assert [(row.block_type, row.reason) for row in releve.rows] == [
        (ingestion_service.SOURCE, ingestion_service.SOURCE_FIGEE)
    ]
    assert "source figée" in releve.rows[0].label
