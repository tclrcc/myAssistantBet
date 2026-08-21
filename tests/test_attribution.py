"""Ce qu'un fait du bloc porte, et ce qui garantit qu'il le porte.

**Le defaut que ces tests ferment est le defaut caracteristique du projet** :
une tranche ajoutee a l'assembleur sans attribution produit exactement la meme
sortie texte qu'une tranche attribuee. Rien ne casse, l'interface a l'air
normale, et le fait sort sans source ni date — donc plafonne a `niveau 4` — sans
qu'aucune ligne ne le signale.
"""

from __future__ import annotations

from typing import Any

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services.attribution import (
    AUTHORITY_LEVEL,
    COLLECTED_LEVEL,
    TRANCHES,
    UNKNOWN_LEVEL,
    Fait,
    attribue,
    lignes,
)
from myassistantbet.services.context import (
    KIND_FORM,
    KIND_RECENT,
    KIND_STANDINGS,
    context_facts,
    context_lines,
    load,
    store,
)
from myassistantbet.services.session import block_facts, context_block

EVENT = {
    "home": "BK Hacken",
    "away": "Djurgardens IF",
    "commence_time": "2026-08-03T15:30:00Z",
}


def _seed_event(settings: Settings) -> None:
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (1, ?, ?, 'evt-1', ?, ?, ?, 'api', ?)",
        (
            competition["sport_id"],
            competition["id"],
            EVENT["home"],
            EVENT["away"],
            EVENT["commence_time"],
            db.utcnow(),
        ),
        settings=settings,
    )


def _date(settings: Settings, kind: str, moment: str) -> None:
    """Force la date d'un releve : `store` prend l'horloge, un test veut choisir."""
    db.execute(
        "UPDATE context SET fetched_at = ? WHERE event_id = 1 AND kind = ?",
        (moment, kind),
        settings=settings,
    )


def _forme(settings: Settings) -> None:
    """Un classement, une forme et des buts recents — trois types, trois dates."""
    store(
        1,
        KIND_STANDINGS,
        {
            "home": {"rank": 1, "points": 40, "played": 20, "goalsDiff": 12},
            "away": {"rank": 8, "points": 22, "played": 20, "goalsDiff": -3},
        },
        settings,
    )
    store(
        1, KIND_FORM, {"home": {"form": "WWDLW", "played": 20}, "away": {"form": "LLDWW"}}, settings
    )
    store(
        1,
        KIND_RECENT,
        {
            "home": {"goals_for": 14, "goals_against": 3, "matches": 5},
            "away": {"goals_for": 4, "goals_against": 6, "matches": 5},
        },
        settings,
    )


def test_un_fait_porte_sa_source_sa_date_et_son_niveau(migrated: Settings) -> None:
    """L'information dormait en base : `context.fetched_at` la porte depuis toujours."""
    _seed_event(migrated)
    _forme(migrated)
    _date(migrated, KIND_STANDINGS, "2026-08-01T06:00:00Z")

    faits = context_facts(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)
    classement = next(fait for fait in faits if fait.label == "Classement")

    assert classement.source == "api-football"
    assert classement.date == "2026-08-01T06:00:00Z"
    assert classement.niveau == COLLECTED_LEVEL


def test_une_ligne_qui_melange_deux_relevés_prend_le_plus_ancien(migrated: Settings) -> None:
    """**Annoncer le plus recent surestimerait la fraicheur de l'autre moitie.**

    `Forme 5` tire ses lettres de la competition (`form`) et ses buts des cinq
    derniers matchs toutes competitions (`recent`) : deux appels, deux dates. La
    borne qui vaut est la plus ancienne — meme regle que l'estimation de cout,
    qui se trompe du bon cote.
    """
    _seed_event(migrated)
    _forme(migrated)
    _date(migrated, KIND_FORM, "2026-08-02T06:00:00Z")
    _date(migrated, KIND_RECENT, "2026-07-30T06:00:00Z")

    faits = context_facts(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)
    forme = next(fait for fait in faits if fait.label == "Forme 5")

    assert forme.date == "2026-07-30T06:00:00Z"


def test_un_releve_jamais_fait_ne_donne_aucune_date(migrated: Settings) -> None:
    """**None, et jamais une date de repli.** Une date inventee se lirait comme
    un releve, alors que tout l'objet de l'attribution est de pouvoir verifier."""
    _seed_event(migrated)
    contexte = load(1, migrated)

    assert contexte.fetched_at(KIND_FORM) is None


def test_aucun_fait_du_bloc_ne_sort_sans_source(migrated: Settings) -> None:
    """**Le garde-fou anti-divergence, et c'est le seul qui compte.**

    Une tranche ajoutee a l'assembleur sans passer par `attribue` produit la meme
    sortie texte qu'une tranche attribuee : rien ne casse, et le fait sort muet.
    Ce test le fait echouer.
    """
    _seed_event(migrated)
    _forme(migrated)

    faits = block_facts(
        1, EVENT["home"], EVENT["away"], EVENT["commence_time"], "football", settings=migrated
    )

    assert faits, "le bloc doit porter des faits, sans quoi le test ne prouve rien"
    muets = [fait.label for fait in faits if not fait.source]
    assert muets == [], f"faits sans source : {muets}"


def test_la_collecte_ne_peut_pas_produire_un_niveau_de_verification(
    migrated: Settings,
) -> None:
    """L'echelle classe **par editeur**, et aucun fournisseur du pipeline n'est
    l'instance qui publie. Seule une alerte officielle atteint le niveau 1, et
    elle recopie son emetteur de la charge utile."""
    _seed_event(migrated)
    _forme(migrated)

    faits = block_facts(
        1, EVENT["home"], EVENT["away"], EVENT["commence_time"], "football", settings=migrated
    )

    assert all(fait.niveau >= COLLECTED_LEVEL for fait in faits)
    assert AUTHORITY_LEVEL < COLLECTED_LEVEL < UNKNOWN_LEVEL


def test_le_rendu_texte_est_exactement_la_projection_des_faits(migrated: Settings) -> None:
    """**Aucun fait ne se perd, aucun ne s'ajoute, l'ordre ne bouge pas.**

    C'est la propriete que la migration doit tenir : l'attribution s'ajoute a
    cote du bloc, elle ne le reecrit pas. Un test d'empreinte casserait a chaque
    changement legitime et se ferait recopier ; celui-ci enonce la regle.
    """
    _seed_event(migrated)
    _forme(migrated)

    faits = block_facts(
        1, EVENT["home"], EVENT["away"], EVENT["commence_time"], "football", settings=migrated
    )
    rendu = context_block(
        1, EVENT["home"], EVENT["away"], EVENT["commence_time"], "football", settings=migrated
    )

    assert rendu == [(fait.label, fait.valeur) for fait in faits]
    assert rendu == lignes(faits)


def test_context_lines_reste_l_adaptateur_de_context_facts(migrated: Settings) -> None:
    """Meme regle un cran plus bas : deux assemblages paralleles ont deja
    diverge deux fois dans ce projet."""
    _seed_event(migrated)
    _forme(migrated)

    faits = context_facts(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)
    rendu = context_lines(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)

    assert rendu == lignes(faits)


def test_une_tranche_inconnue_ne_ment_pas_sur_sa_source(migrated: Any) -> None:
    """**Un fait non attribuable est emis, jamais supprime**, et il le dit.

    La regle d'origine du contrat le traitait comme absent : appliquee a
    l'existant, elle supprimait 80 % du bloc. Le plafond empeche la meme chose et
    le rend visible.
    """
    faits = attribue([("Machin", "valeur")], "producteur-inexistant")

    assert faits == [Fait("Machin", "valeur", source=None, date=None, niveau=UNKNOWN_LEVEL)]


def test_une_tranche_n_ecrase_pas_une_date_plus_fine(migrated: Any) -> None:
    """`context` date **par type de releve**, plus finement que sa tranche.

    Sans cette regle, tout un bloc prendrait l'heure du releve le plus recent, et
    les douze dates que la table porte seraient perdues a l'assemblage.
    """
    deja = Fait("Classement", "1er", source="api-football", date="2026-08-01T06:00:00Z", niveau=3)

    assert attribue([deja], "weather") == [deja]


def test_chaque_tranche_declaree_porte_une_source_et_un_niveau() -> None:
    """Une tranche sans source rendrait des faits muets sans qu'aucun test ne
    tombe : c'est le registre lui-meme qui doit etre complet."""
    assert TRANCHES
    for cle, tranche in TRANCHES.items():
        assert tranche.cle == cle, "la cle du registre et celle de la tranche divergent"
        assert tranche.source, f"tranche sans source : {cle}"
        assert AUTHORITY_LEVEL <= tranche.niveau <= UNKNOWN_LEVEL
