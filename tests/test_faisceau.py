"""Le moniteur de faisceau : la matiere premiere d'une analyse, session par session.

**Pourquoi cet instrument existe**, et il faut le lire avant d'y toucher. La
phase 1 de l'audit a etabli que le residu au prix du regime actuel vaut
−1,60 point par selection avec un intervalle de [−7,36 ; +3,97] : a n = 281,
l'effet detectable est de l'ordre de huit points, donc l'indicateur principal de
l'application **ne bougera pas avant des trimestres**.

Le 21/08/2026, la procedure de recherche a cesse d'etre transmise au modele. Le
residu n'a rien vu. Les grandeurs de faisceau, elles, montrent des ecarts de 12 a
17 points sur 237 faits, tous au seuil — et elles les montrent **en jours**.

D'ou la regle que cet instrument applique : la sante mesurable de cette
application se lit sur ses **intrants**, pas sur ses sorties. Le residu reste la
mesure de verite ; il n'est pas un instrument de pilotage a cette echelle.

**Ce que ces grandeurs ne disent pas, et le test le garde** : un faisceau qui
remonte ne prouve pas que les analyses s'ameliorent. Elles mesurent la matiere
premiere, jamais le jugement qui s'exerce dessus. La dissymetrie est le coeur du
sujet — une baisse est une alarme, une hausse dit seulement qu'il y avait plus a
lire ce jour-la.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services.history import add_pick, evidence_shift

LOIN = "2099-01-01T00:00:00Z"


def _fait(editeur: str = "motherwellfc.co.uk", niveau: int = 1) -> dict[str, object]:
    return {
        "enonce": "retour de Johnny Koutroumbis",
        "date": "2026-08-12",
        "editeur": editeur,
        "niveau": niveau,
    }


def _bloc(faits: list[dict[str, object]], source_level: object = 1) -> str:
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


def _session(settings: Settings, libelle: str) -> int:
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, 'Lyon', 'Nice', ?, 'oddsapi', ?)",
        (sport["id"], LOIN, db.utcnow()),
        settings=settings,
    )
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES (?, ?)",
        (libelle, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)["id"])


def _pose(
    settings: Settings, session_id: int, blocs: list[str], *, exploratory: bool = False
) -> None:
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"]
    for bloc in blocs:
        add_pick(
            session_id,
            "safe",
            "1N2",
            "Lyon",
            price="1.50",
            event_id=str(event_id),
            confidence="4",
            claim=bloc,
            exploratory=exploratory,
            independence_note="angles distincts",
            settings=settings,
        )


def _ligne(series: list, session_id: int):
    return next(entry for entry in series if entry.session_id == session_id)


def test_deux_densites_rendent_deux_lignes_distinctes(migrated: Settings) -> None:
    """La serie doit **separer** deux sessions dont la matiere differe.

    C'est tout l'objet de l'instrument : agregee, la difference disparait — c'est
    exactement ce qui s'est produit le 21/08, ou la part de niveaux 1-2 declares
    n'a pas bouge d'un point pendant que le faisceau perdait un quart de son
    volume et voyait tripler sa part de niveau 4.
    """
    riche = _session(migrated, "riche")
    _pose(migrated, riche, [_bloc([_fait(), _fait("bbc.co.uk", 2)])] * 3)

    pauvre = _session(migrated, "pauvre")
    _pose(migrated, pauvre, [_bloc([_fait("sportytrader.com", 4)])] * 3)

    serie = evidence_shift(migrated)

    assert _ligne(serie, riche).facts_per_block == 2.0
    assert _ligne(serie, pauvre).facts_per_block == 1.0
    assert _ligne(serie, riche).share(1) == 0.5, "un fait sur deux vient de l'instance"
    assert _ligne(serie, pauvre).share(4) == 1.0, "tout le faisceau vient d'un agregateur"


def test_une_session_sans_bloc_ne_figure_pas_et_le_dit(migrated: Settings) -> None:
    """**Une session sans bloc n'a pas un faisceau vide : elle n'en a pas.**

    Les seize premieres sessions de la base sont dans ce cas — le gabarit ne
    demandait pas les blocs avant le 18/08. Leur preter un faisceau de zero
    inventerait une degradation qui n'a pas eu lieu, et c'est le defaut
    caracteristique du projet : une sortie identique pour l'absence de donnee et
    pour l'absence de mesure.
    """
    muette = _session(migrated, "sans bloc")
    _pose(migrated, muette, ["", ""])

    parlante = _session(migrated, "avec bloc")
    _pose(migrated, parlante, [_bloc([_fait()])])

    serie = evidence_shift(migrated)

    assert [entry.session_id for entry in serie] == [parlante]
    assert _ligne(serie, parlante).blocks == 1


def test_un_point_maigre_est_marque_et_le_seuil_descend_dans_l_objet(migrated: Settings) -> None:
    """Un point a n = 4 se lit comme les autres sur un graphique, et c'est le
    defaut a corriger : l'effectif affiche a cote ne suffit pas.

    **Le seuil n'est pas invente** : c'est `feedback_min_rows`, celui de la page,
    parce que « sous quel compte une proportion ne veut plus rien dire » est une
    propriete des donnees et non de la surface qui les montre. Il porte sur le
    **compte de faits** et non de blocs : trois des quatre grandeurs sont des
    parts de faits, et c'est ce denominateur-la qui gouverne leur precision.

    Et il **descend dans l'objet** plutot que d'etre relu a l'acces : une ligne
    qui irait chercher son propre reglage rendrait deux releves du meme lot
    indiscernables des que la valeur change entre les deux.
    """
    maigre = _session(migrated, "maigre")
    _pose(migrated, maigre, [_bloc([_fait()])])

    fournie = _session(migrated, "fournie")
    _pose(migrated, fournie, [_bloc([_fait()] * 30)])

    serie = evidence_shift(migrated)

    assert _ligne(serie, maigre).thin is True
    assert _ligne(serie, fournie).thin is False
    assert _ligne(serie, maigre).minimum == _ligne(serie, fournie).minimum
    assert _ligne(serie, maigre).minimum > 0, "le seuil voyage avec la ligne"


def test_la_serie_se_lit_du_plus_ancien_au_plus_recent(migrated: Settings) -> None:
    """Une serie se lit dans l'ordre ou elle s'est produite.

    La page range partout du plus recent au plus ancien ; suivre cette convention
    ici rendrait une deformation illisible. Meme regle que `scale_shift`.
    """
    premiere = _session(migrated, "premiere")
    _pose(migrated, premiere, [_bloc([_fait()])])
    seconde = _session(migrated, "seconde")
    _pose(migrated, seconde, [_bloc([_fait()])])

    serie = evidence_shift(migrated)

    assert [entry.session_id for entry in serie] == [premiere, seconde]


def test_la_section_c_bis_n_entre_pas_dans_le_faisceau(migrated: Settings) -> None:
    """Population principale seule, comme `scale_shift` et `labelling()`.

    Une selection exploratoire est produite **sans exigence de fait date** : son
    faisceau est maigre par construction, et l'y compter ferait lire une regle du
    cadre comme une degradation de la collecte.
    """
    session_id = _session(migrated, "mixte")
    _pose(migrated, session_id, [_bloc([_fait(), _fait("bbc.co.uk", 2)])])
    _pose(migrated, session_id, [_bloc([], source_level="lecture")], exploratory=True)

    ligne = _ligne(evidence_shift(migrated), session_id)

    assert ligne.blocks == 1, "le bloc exploratoire ne compte pas"
    assert ligne.facts == 2


# -- La surface, livree avec le service ---------------------------------------


def test_le_faisceau_se_rend_sur_la_page_et_dans_l_export(
    isolated_settings: Settings,
) -> None:
    """**Le service et sa surface se livrent ensemble.**

    La lecon est payee deux fois dans ce depot : un service qui produit une
    valeur que rien ne permet de lire, et un service qui accepte une valeur que
    rien ne permet de saisir. Un test qui appellerait `evidence_shift` sans
    regarder la page passerait dans les deux cas — c'est exactement ce qui a
    laisse un banc vert pendant que le formulaire d'import refusait cinq
    collages complets.

    Il verifie donc les **deux rendus reels**, et que la reserve qui empeche de
    lire une hausse comme une victoire y figure : c'est elle qui decide de
    l'usage, et une phrase absente ne casse rien.
    """
    with TestClient(app) as client:
        # Le client applique les migrations au demarrage : la session se monte
        # **apres**, sans quoi il n'y a pas encore de schema ou l'ecrire.
        session_id = _session(isolated_settings, "faisceau")
        _pose(isolated_settings, session_id, [_bloc([_fait(), _fait("bbc.co.uk", 2)])])

        page = client.get("/stats").text
        fichier = client.get("/api/stats/export?format=md").text
        charge = client.get("/api/stats/export?format=json").json()

    assert "Faisceau session par session" in page
    assert "Faisceau session par session" in fichier
    for rendu in (page, fichier):
        plat = " ".join(rendu.split())
        assert "hausse ne prouve rien" in plat, (
            "sans cette reserve, le premier point qui remonte se lira comme une victoire"
        )

    serie = charge["evidence_shift"]
    assert [entry["session_id"] for entry in serie] == [session_id]
    assert serie[0]["facts_per_block"] == 2.0
    assert serie[0]["minimum"] > 0, "le seuil voyage avec la ligne, il ne se recalcule pas"
