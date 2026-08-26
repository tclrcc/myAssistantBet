"""La liste de refus : les pages qui vendent un operateur n'entrent pas au faisceau.

**Sa valeur est prospective, pas retrospective**, et c'est la seule facon de la
lire honnetement. Elle n'existe pas pour trier ce qui est deja entre — 12 faits
sur 271 au 26/08/2026, **sans contraste avant/apres** (3,5 % contre 4,9 %,
p = 0,76) et le plus souvent correctement etiquetes niveau 4 par le modele. Elle
existe pour que ces pages **cessent d'entrer**, et pour que leur part soit
lisible dans la serie du faisceau le jour ou elle bougera.

**Une liste de refus est sure la ou une liste d'admission ne l'est pas** : son
faux negatif ne coute qu'un signal manquant, quand le faux positif d'une liste
d'admission attribue un niveau faux. C'est ce qui autorise une liste **curee et
incomplete**, la ou la meme forme etait inacceptable pour attribuer un niveau.
"""

from __future__ import annotations

import json

from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services.confidence import is_refused
from myassistantbet.services.history import add_pick, evidence_shift

LOIN = "2099-01-01T00:00:00Z"


def _bloc(editeurs: list[tuple[str, int]]) -> str:
    return json.dumps(
        {
            "match": "M1",
            "type": "issue",
            "source_level": 4,
            "faits": [
                {
                    "enonce": "compo probable publiee",
                    "date": "2026-08-12",
                    "editeur": editeur,
                    "niveau": niveau,
                }
                for editeur, niveau in editeurs
            ],
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


def _pose(settings: Settings, session_id: int, bloc: str) -> int:
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"]
    return add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        price="1.50",
        event_id=str(event_id),
        confidence="2",
        source_level="4",
        claim=bloc,
        result="win",
        independence_note="angles distincts",
        settings=settings,
    )


# -- Ce que la liste reconnait, et ce qu'elle ne doit pas reconnaitre ---------


def test_les_domaines_mesures_sont_reconnus() -> None:
    """Les sept releves sur la base servie au 26/08/2026.

    Le premier sondage n'en trouvait que deux, parce qu'il cherchait les noms
    d'operateurs **devines** au lieu de relire les 181 domaines. Un compte faible
    sur un rapprochement est un defaut d'appariement jusqu'a preuve du
    contraire — et la regle vaut aussi pour qui mesure.
    """
    for domaine in (
        "betfair.es",
        "sportytrader.com",
        "freetips.com",
        "footballpredictions.net",
        "etoto.pl",
        "extra.toto.nl",
        "betmines.com",
    ):
        assert is_refused(domaine), domaine


def test_un_libelle_qui_contient_une_marque_en_sous_chaine_n_est_pas_refuse() -> None:
    """**La comparaison porte sur le label du domaine, jamais en sous-chaine.**

    `betterrugby.com` contient « bet » et n'est pas un operateur ; `parisien.fr`
    contient « paris » et c'est un journal. Une liste de refus qui se declenche
    sur une sous-chaine attribuerait un soupcon a des editeurs legitimes — et un
    faux positif, ici, se lit comme une accusation.
    """
    for domaine in (
        "betterrugby.com",
        "leparisien.fr",
        "sports.orange.fr",
        "sportsmole.co.uk",
        "footballcritic.com",
        "lastwordonsports.com",
    ):
        assert not is_refused(domaine), domaine


def test_un_domaine_ambigu_reste_dehors() -> None:
    """`scores24.live` a ete examine et **ecarte**.

    C'est un agregateur de scores qui publie aussi des pronostics : un niveau 3
    au sens de la table, pas une page adossee a un operateur. Le doute se tranche
    vers l'exclusion — un faux negatif de liste de refus ne coute qu'un signal,
    un faux positif accuse.
    """
    assert not is_refused("scores24.live")


# -- Un signalement, jamais un refus ------------------------------------------


def test_un_fait_d_operateur_entre_quand_meme_et_se_compte(migrated: Settings) -> None:
    """**La ligne s'importe.** Le modele n'a pas menti sur le niveau dans la
    majorite des cas — trois des quatre premiers releves sont correctement
    etiquetes 4 et tombent en cran 2. Ce qui se signale est leur **entree dans le
    faisceau**, pas leur etiquetage.

    Refuser la ligne la ferait disparaitre sans laisser de trace, ce qui est le
    rejet silencieux que ce projet retire partout.
    """
    session_id = _session(migrated)
    pick_id = _pose(migrated, session_id, _bloc([("betfair.es", 4)]))

    assert pick_id > 0
    ligne = db.query_one(
        "SELECT claim_raw_json FROM picks WHERE id = ?", (pick_id,), settings=migrated
    )
    assert ligne["claim_raw_json"], "le bloc est enregistre tel quel"

    serie = evidence_shift(migrated)
    assert serie[0].refused == 1
    assert serie[0].facts == 1


def test_l_origine_prime_sur_le_relais(migrated: Settings) -> None:
    """Meme regle que partout : `Fact.source` tranche, et il est ecrit une fois.

    Un operateur relaye par un agregateur reste un operateur ; un club relaye par
    un operateur reste un club. Sans cette lecture, la liste compterait le relais.
    """
    session_id = _session(migrated)
    bloc = json.loads(_bloc([("onefootball.com", 4)]))
    bloc["faits"][0]["editeur_origine"] = "betfair.es"
    _pose(migrated, session_id, json.dumps(bloc, ensure_ascii=False))

    autre = _session(migrated)
    relaye = json.loads(_bloc([("betfair.es", 1)]))
    relaye["faits"][0]["editeur_origine"] = "arsenal.com"
    _pose(migrated, autre, json.dumps(relaye, ensure_ascii=False))

    serie = {entry.session_id: entry for entry in evidence_shift(migrated)}
    assert serie[session_id].refused == 1, "l'origine operateur compte"
    assert serie[autre].refused == 0, "un club relaye par un operateur reste un club"


def test_la_part_refusee_rejoint_la_serie_du_faisceau(isolated_settings: Settings) -> None:
    """La quatrieme colonne du moniteur, livree avec la liste qui l'alimente.

    Elle n'etait pas dans le premier chantier, et c'etait deliberе : une grandeur
    qui vit a deux endroits avant d'avoir sa surface est exactement ce qui produit
    les divergences que ce lot passe son temps a corriger.
    """
    with TestClient(app) as client:
        session_id = _session(isolated_settings)
        _pose(isolated_settings, session_id, _bloc([("betfair.es", 4), ("arsenal.com", 1)]))

        page = client.get("/stats").text
        charge = client.get("/api/stats/export?format=json").json()

    assert "Pages d'opérateur" in page
    serie = charge["evidence_shift"]
    assert serie[0]["refused"] == 1
    assert serie[0]["facts"] == 2


def test_le_collage_signale_les_pages_d_operateur_sans_refuser_la_ligne(
    isolated_settings: Settings,
) -> None:
    """**Le signal passe par `notes`, jamais par `ignored`.**

    `ImportPreview.ignored` cache tout le formulaire : il n'a qu'un sens
    legitime, « il n'y a rien a montrer ». Y verser une remarque ferait
    disparaitre l'import entier pour un detail — la lecon des cinq collages
    complets refuses. Ce qui accompagne un apercu **lisible** descend dans
    `notes`, qui affiche sans empecher d'importer.
    """
    from myassistantbet.services import picks_import

    tableau = (
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Type | Source |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Nice | 1N2 | Lyon | 1.65 | 🟢 SAFE | 2 | issue | 4 |\n"
    )
    with TestClient(app):
        session_id = _session(isolated_settings)
        rendu = tableau + "\n```conf\n" + _bloc([("betfair.es", 4)]) + "\n```\n"
        preview = picks_import.build_preview(session_id, rendu, isolated_settings)

    assert not preview.ignored, "un apercu lisible ne se cache pas pour une remarque"
    assert preview.picks, "la ligne reste proposee a l'import"
    assert any("betfair.es" in note for note in preview.notes)
    # **Et il se dit meme quand l'appariement echoue.** Cette session n'a aucun
    # prompt archive, donc aucun bloc ne se rattache — c'est exactement le cas de
    # la session 23. Un signal pose apres l'appariement se serait taise ici,
    # c'est-a-dire sur les collages qu'il faut le plus regarder.
    assert all(pick.claim is None for pick in preview.picks), (
        "aucun prompt a apparier : le montage reproduit bien le cas muet"
    )
