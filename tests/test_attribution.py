"""Ce qu'un fait du bloc porte, et ce qui garantit qu'il le porte.

**Le defaut que ces tests ferment est le defaut caracteristique du projet** :
une tranche ajoutee a l'assembleur sans attribution produit exactement la meme
sortie texte qu'une tranche attribuee. Rien ne casse, l'interface a l'air
normale, et le fait sort sans source ni date — donc plafonne a `niveau 4` — sans
qu'aucune ligne ne le signale.
"""

from __future__ import annotations

import re
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
    _Faits,
    context_facts,
    context_lines,
    load,
    store,
)
from myassistantbet.services.session import block_facts, context_block

#: Un age compte depuis l'instant du rendu. **Ce que le payload ne transporte
#: jamais** : vrai a la seconde ou il est ecrit, faux pour toujours ensuite.
AGE_DEPUIS_MAINTENANT = re.compile(r"il y a \d+\s*(h|j|jours?|min)\b")

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
            "home": {
                "goals_for": 14,
                "goals_against": 3,
                "matches": 5,
                "last_date": "2026-07-27T17:30:00+00:00",
            },
            "away": {
                "goals_for": 4,
                "goals_against": 6,
                "matches": 5,
                "last_date": "2026-07-27T15:00:00+00:00",
            },
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


# -- Les trois facons dont l'attribution peut mentir en restant verte ---------
#
# **Un fait faux ressemble trait pour trait a un fait juste.** Le test « aucun
# fait sans source » attrape la tranche oubliee ; il ne dit rien d'une tranche
# attribuee **de travers**, qui sort avec sa source, sa date et son niveau — donc
# parfaitement credible. Les trois cas ci-dessous sont les trois formes que ce
# mensonge peut prendre, et chacun a son garde-fou.


def test_chaque_ligne_est_datee_du_type_qui_l_a_produite(migrated: Settings) -> None:
    """**Mauvais kind : la ligne sort datee d'un releve qui ne l'a pas produite.**

    Le classement viendrait de l'heure ou la forme a ete lue. Rien ne casse — la
    date est plausible, du bon jour, au bon format, et **presente parmi les
    relevés de l'evenement** : c'est ce qui rend le cas indetectable par un
    controle de vraisemblance. La ligne devient un fait date faux, ce que le
    contrat nomme pire qu'un fait sans date.

    Ce test enonce donc la correspondance **independamment du code** : trois
    types seedes a trois dates distinctes, et chaque libelle doit porter celle du
    sien. C'est la seconde ecriture que le code s'interdit — ici elle est le
    garde-fou, et sa divergence est precisement ce qu'on veut voir echouer.
    """
    _seed_event(migrated)
    _forme(migrated)
    _date(migrated, KIND_STANDINGS, "2026-08-01T06:00:00Z")
    _date(migrated, KIND_FORM, "2026-08-02T06:00:00Z")
    _date(migrated, KIND_RECENT, "2026-07-30T06:00:00Z")

    dates = {
        fait.label: fait.date
        for fait in context_facts(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)
    }

    assert dates["Classement"] == "2026-08-01T06:00:00Z", "le classement vient du classement"
    assert dates["Repos"] == "2026-07-30T06:00:00Z", "le repos vient des matchs recents"
    # Deux types pour une ligne : la borne est le plus ancien des deux. Les
    # lettres viennent de la forme, les buts des cinq derniers matchs.
    assert dates["Forme 5"] == "2026-07-30T06:00:00Z"
    assert dates["Classement"] != dates["Repos"], "sans dates distinctes, le test ne prouve rien"


def test_une_date_ne_se_prend_jamais_sur_l_horloge(migrated: Settings) -> None:
    """**`now()` a la place du releve : le fait parait frais pour toujours.**

    C'est la forme la plus couteuse des trois, parce qu'elle survit a
    l'archivage : un payload relu dans six mois annoncerait un releve du jour.
    Mesure du 21/08/2026 — sur les cinq libelles du bloc qui portent une duree,
    quatre la comptent depuis le coup d'envoi et restent donc vraies ; seule la
    meteo comptait depuis maintenant, et c'est elle qui a revele le motif.

    Ce qui l'attrape : **toute date rendue est anterieure au releve le plus
    recent de l'evenement**. Une date prise sur l'horloge lui est posterieure.
    """
    _seed_event(migrated)
    _forme(migrated)
    for kind in (KIND_STANDINGS, KIND_FORM, KIND_RECENT):
        _date(migrated, kind, "2026-08-01T06:00:00Z")

    faits = context_facts(1, EVENT["home"], EVENT["away"], EVENT["commence_time"], migrated)

    plus_recent = "2026-08-01T06:00:00Z"
    for fait in faits:
        assert fait.date is None or fait.date <= plus_recent, (
            f"« {fait.label} » est date du {fait.date}, posterieur a tout releve connu"
        )


def test_aucune_valeur_du_bloc_ne_porte_un_age_compte_depuis_maintenant(
    migrated: Settings,
) -> None:
    """Le corollaire de la regle precedente, cote **valeur** et non cote date.

    Le payload transporte des horodatages ; la fraicheur se derive a la lecture.
    Une valeur qui porte « il y a N h » est vraie a la seconde ou elle est ecrite
    et fausse pour toujours ensuite.

    **Les durees comptees depuis le coup d'envoi ne sont pas concernees** — le
    payload porte l'heure du match, donc « dans 8j » reste verifiable. C'est bien
    l'age depuis *maintenant* que ce test interdit.
    """
    _seed_event(migrated)
    _forme(migrated)

    faits = block_facts(
        1, EVENT["home"], EVENT["away"], EVENT["commence_time"], "football", settings=migrated
    )

    fautifs = [fait.label for fait in faits if AGE_DEPUIS_MAINTENANT.search(fait.valeur)]
    assert fautifs == [], f"valeurs portant un age d'horloge : {fautifs}"


def test_un_fournisseur_qui_n_est_pas_l_instance_ne_peut_pas_atteindre_le_niveau_1(
    migrated: Settings,
) -> None:
    """**Niveau pousse a 1 sur un fournisseur non emetteur.**

    Le niveau 1 autorise une confiance 4 ou 5 : pousse a tort, il fait passer une
    statistique tierce pour une publication de l'instance, et le tableau
    principal s'ouvre a des selections que rien n'a verifiees.

    Ce qui l'attrape : **le registre lui-meme**. Une seule tranche a le droit
    d'atteindre le niveau 1 — l'alerte officielle, dont l'emetteur est recopie de
    la charge utile — et elle ne le fait pas en le declarant : elle le fait ligne
    par ligne, sur ce que la source dit d'elle-meme.
    """
    montees = [tranche.cle for tranche in TRANCHES.values() if tranche.niveau < COLLECTED_LEVEL]

    assert montees == [], (
        f"tranches declarees au-dessus du plafond de collecte : {montees} — "
        "aucun fournisseur du pipeline n'est l'instance qui publie"
    )


def test_une_date_manquante_ne_se_remplace_par_aucun_repli(migrated: Settings) -> None:
    """**Le trou que la mutation « horloge » a laissé voir.**

    Remplacer `min(dates)` par l'horloge est attrape par quatre tests ; poser
    l'horloge **en repli**, quand aucun releve n'est connu, ne l'etait par aucun.
    C'est pourtant le cas que le contrat interdit nommement : une date de repli a
    l'apparence d'un fait, et elle est pire qu'une date absente.

    Le cas ne se provoque pas par la base — `context.fetched_at` est `NOT NULL` —
    donc il se teste la ou la regle vit.
    """
    _seed_event(migrated)
    collecteur = _Faits(load(1, migrated))

    collecteur.add("Machin", "valeur", "type-jamais-releve")

    assert collecteur.items[0].date is None
    assert collecteur.items[0].source == "api-football", "la source, elle, reste connue"
