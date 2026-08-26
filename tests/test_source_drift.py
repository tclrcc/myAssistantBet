"""`source_drift` : l'ecart entre le niveau declare et ce que les faits portent.

**Ce que ce module n'est pas, et il faut le lire avant d'y toucher.** Le chantier
d'origine visait un `source_level_computed` — une table d'attribution par domaine
transposee du cadre dans l'application, qui aurait **calcule** le niveau au lieu
de le croire. La mesure du 26/08/2026 l'a ferme :

- **181 domaines distincts pour 271 faits**, 1,50 fait par domaine. Une table des
  domaines vus au moins deux fois couvre 47,6 % ; au moins trois fois, 29,2 %. La
  queue est precisement la ou vivent les niveaux 1 — les sites de clubs, cites
  une fois chacun ;
- deriver ces domaines des noms d'equipes deja en base rend 33,9 % avec des faux
  positifs visibles a l'oeil (`sportsmole.co.uk` rapproche de « sport »), soit le
  meme piege que le rapprochement automatique des ligues, deja essaye et rejete.

Ce qui reste decidable **sans aucune table** est ce que ce module fait : la
coherence d'un domaine avec lui-meme, et l'existence d'un fait au niveau declare.
Il **expose**, il ne corrige pas — meme forme que `tier_drift`.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services.history import add_pick, source_drift

LOIN = "2099-01-01T00:00:00Z"


def _fait(editeur: str, niveau: int) -> dict[str, object]:
    return {
        "enonce": "retour de Johnny Koutroumbis",
        "date": "2026-08-12",
        "editeur": editeur,
        "niveau": niveau,
    }


def _bloc(faits: list[dict[str, object]], source_level: object) -> str:
    return json.dumps(
        {
            "match": "M1",
            "type": "issue",
            "source_level": source_level,
            "faits": faits,
            "manque_touche_facteur": False,
        },
        ensure_ascii=False,
    )


def _session(settings: Settings) -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Lyon', 'Nice', ?, 'oddsapi', ?)",
        (sport["id"], LOIN, db.utcnow()),
        settings=settings,
    )
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)["id"])


def _pose(
    settings: Settings, session_id: int, bloc: str, niveau: str, resultat: str = "pending"
) -> int:
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"]
    return add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        price="1.50",
        event_id=str(event_id),
        confidence="3",
        source_level=niveau,
        claim=bloc,
        result=resultat,
        independence_note="angles distincts",
        settings=settings,
    )


# -- Le cas qui decide de la regle -------------------------------------------


def test_un_fait_faible_dans_le_faisceau_ne_contamine_pas_la_declaration(
    migrated: Settings,
) -> None:
    """**Le pick 552, en negatif, et c'est le test qui compte le plus ici.**

    Releve reel du 24/08/2026 : `Under 2.5`, `source_level: 2`, trois faits —
    `granadacf.es` niveau 2, `ultimahora.es` niveau 2, `betfair.es` niveau 4. Il
    est **conforme** : le niveau d'une selection est celui du fait qui **porte
    l'angle**, ni le maximum ni le minimum des faits cites. Le betfair est
    accessoire, et un faisceau qui melange des niveaux est le **cas ordinaire**.

    Un controle qui exigerait l'accord avec le maximum des niveaux cites
    declarerait donc le faux a chaque bloc de ce genre. C'est l'erreur que
    l'instruction a faite avant la mesure, et ce test est ce qui l'empeche de
    revenir dans six mois quand personne ne se souviendra pourquoi la regle est
    « le fait qui porte l'angle ».
    """
    session_id = _session(migrated)
    _pose(
        migrated,
        session_id,
        _bloc(
            [
                _fait("granadacf.es", 2),
                _fait("ultimahora.es", 2),
                _fait("betfair.es", 4),
            ],
            source_level=2,
        ),
        niveau="2",
    )

    drift = source_drift(migrated)

    assert drift.unsupported == [], "un fait faible en appui ne rend pas la declaration fausse"
    assert drift.comparable == 1


def test_un_niveau_declare_meilleur_que_tous_ses_faits_est_signale(migrated: Settings) -> None:
    """Le cas reel, mesure deux fois sur 211 blocs.

    `pick 353` et `pick 393` : `source_level: 2` sur un **unique** fait de
    niveau 4 — `sportytrader.com`, `sportsmole.co.uk`. `Claim.rung` lit
    `source_level` comme une entree et ne le confronte jamais aux faits, si bien
    que les deux ont recu un cran 3 la ou un niveau 4 declare les aurait mis a 2.
    """
    session_id = _session(migrated)
    pick_id = _pose(
        migrated,
        session_id,
        _bloc([_fait("sportytrader.com", 4)], source_level=2),
        niveau="2",
    )

    drift = source_drift(migrated)

    assert drift.unsupported == [pick_id]


def test_une_lecture_declaree_n_est_jamais_un_ecart(migrated: Settings) -> None:
    """`lecture` n'est pas un niveau de l'echelle : c'est un etat de la selection.

    Le gabarit la presente comme une reponse **normale et frequente**, et un bloc
    sans fait l'impose. La confronter aux faits n'aurait aucun sens — il n'y en a
    pas — et la compter comme un ecart ferait du cas ordinaire une anomalie.
    """
    session_id = _session(migrated)
    _pose(migrated, session_id, _bloc([], source_level="lecture"), niveau="lecture")

    drift = source_drift(migrated)

    assert drift.unsupported == []
    assert drift.comparable == 0, "il n'y a rien a confronter"


def test_un_domaine_qui_porte_deux_niveaux_est_expose_sans_etre_arbitre(
    migrated: Settings,
) -> None:
    """**Le niveau est une propriete de l'editeur** — les deux documents le disent.

    Un domaine ne peut donc pas etre a la fois 1 et 4 : par construction, au moins
    une declaration est fausse. Et ca se detecte **sans savoir laquelle**, ce qui
    est tout l'interet — aucune table n'est necessaire, et rien n'est corrige.

    Mesure du 26/08/2026 : 14 domaines sur 181, portant 60 faits. Quatre d'entre
    eux sont des sites officiels de club, c'est-a-dire la categorie qui produit
    les niveaux 1 et celle sur laquelle le modele est le plus instable.
    """
    session_id = _session(migrated)
    _pose(migrated, session_id, _bloc([_fait("atptour.com", 1)], source_level=1), niveau="1")
    _pose(migrated, session_id, _bloc([_fait("atptour.com", 2)], source_level=2), niveau="2")
    _pose(migrated, session_id, _bloc([_fait("bbc.co.uk", 2)], source_level=2), niveau="2")

    drift = source_drift(migrated)

    conflits = {conflit.publisher: conflit for conflit in drift.conflicts}
    assert set(conflits) == {"atptour.com"}, "un domaine constant n'est pas un conflit"
    assert conflits["atptour.com"].facts == 2
    assert dict(conflits["atptour.com"].levels) == {1: 1, 2: 1}


def test_l_editeur_d_origine_prime_sur_celui_qui_publie(migrated: Settings) -> None:
    """Meme regle que pour compter les facteurs independants.

    Un agregateur qui reprend un club sort sur son propre domaine ; c'est
    l'origine qui porte le niveau, et la lire ailleurs ferait deux editeurs la ou
    il n'y en a qu'un. `Fact.publisher_of` tranche deja cette question — la
    recopier ici l'aurait fait diverger.
    """
    session_id = _session(migrated)
    relaye = _fait("onefootball.com", 1)
    relaye["editeur_origine"] = "arsenal.com"
    _pose(migrated, session_id, _bloc([relaye], source_level=1), niveau="1")
    _pose(migrated, session_id, _bloc([_fait("arsenal.com", 4)], source_level=4), niveau="4")

    drift = source_drift(migrated)

    assert [conflit.publisher for conflit in drift.conflicts] == ["arsenal.com"]


def test_le_drift_se_rend_sur_la_page_et_dans_l_export(isolated_settings: Settings) -> None:
    """**Le service et sa surface se livrent ensemble.**

    Un compteur d'audit que rien n'affiche est une donnee sans lecteur, et le
    depot a deja retire un type entier pour cette raison — la migration 022.
    """
    with TestClient(app) as client:
        session_id = _session(isolated_settings)
        _pose(
            isolated_settings,
            session_id,
            _bloc([_fait("sportytrader.com", 4)], source_level=2),
            niveau="2",
            # **Tranchee**, sans quoi la carte des niveaux ne se rend pas et le
            # test passerait pour une raison qui n'est pas la sienne.
            resultat="win",
        )

        page = client.get("/stats").text
        fichier = client.get("/api/stats/export?format=md").text
        charge = client.get("/api/stats/export?format=json").json()

    assert "Niveau de source déclaré" in page
    assert "Niveau de source déclaré" in fichier
    # Le compteur vit **a cote de la carte qu'il qualifie**, dans `groups` :
    # il ne decrit pas la population entiere mais la lecture de son echelle de
    # sources, comme `tier_drift` qualifie la carte des paliers.
    assert charge["groups"]["source_drift"]["unsupported"] == 1


# -- La distribution se montre, elle ne s'arbitre pas ------------------------


def test_le_libelle_n_attribue_aucune_norme(migrated: Settings) -> None:
    """**Le calcul etait juste et le libelle le contredisait.**

    Une version intermediaire designait un niveau « dominant » et comptait ce qui
    « s'en ecarte ». Or l'instrument ne sait pas laquelle des deux declarations
    est la bonne — c'est tout son principe, et c'est ce qui le separe d'un
    correcteur. « S'ecarter » suppose une norme et designe des fautifs ; deux
    comptes cote a cote ne supposent rien.

    Ce test garde la retenue, pas une tournure : il refuse le vocabulaire qui
    attribue, et il refuse le pourcentage. Une reecriture qui garde les comptes
    passe ; une reecriture qui reintroduit un verdict casse.
    """
    session_id = _session(migrated)
    for _ in range(9):
        _pose(migrated, session_id, _bloc([_fait("atptour.com", 1)], source_level=1), niveau="1")
    _pose(migrated, session_id, _bloc([_fait("atptour.com", 2)], source_level=2), niveau="2")

    libelle = source_drift(migrated).conflicts[0].label

    assert "niveau 1 ×9" in libelle and "niveau 2 ×1" in libelle
    assert "%" not in libelle, (
        "un taux sans son compte ne se lit pas, et ici le compte est le sujet"
    )
    for verdict in ("écart", "ecart", "dominant", "faux", "erron"):
        assert verdict not in libelle.lower(), (
            f"« {verdict} » attribue une norme que l'instrument ne connait pas"
        )


def test_un_partage_un_un_se_rend_comme_toute_autre_distribution(migrated: Settings) -> None:
    """**Neuf des quatorze conflits reels sont dans ce cas**, et ce n'est pas un cas
    particulier.

    Un libelle bati sur une norme en faisait une exception — « aucun niveau
    dominant » — alors que c'est une distribution a deux modes egaux, et la forme
    la plus frequente. Le rendu ne la distingue donc pas : les comptes disent tout.
    """
    session_id = _session(migrated)
    _pose(migrated, session_id, _bloc([_fait("uefa.com", 1)], source_level=1), niveau="1")
    _pose(migrated, session_id, _bloc([_fait("uefa.com", 2)], source_level=2), niveau="2")

    conflit = source_drift(migrated).conflicts[0]

    assert conflit.label == "niveau 1 ×1 · niveau 2 ×1"
    assert conflit.facts == 2
