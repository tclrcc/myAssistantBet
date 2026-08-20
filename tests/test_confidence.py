"""Le cran de confiance, calcule au lieu d'etre devine.

**Ce que la mesure a montre**, sur les 141 selections tranchees du 13/08/2026 :
90 % du volume sur deux crans, aucune en cran 1 sur 149, et un ordre qui n'est
pas monotone — cran 2 a 77 %, cran 4 a 60 %, cran 3 a 44 %. Le palier, lui,
ordonne a p = 0,000 ; l'echelle de confiance non, p = 0,131.

Le cran est pourtant defini dans le gabarit comme une fonction de trois choses
verifiables. Le modele appliquait la table lui-meme — exactement le cas que le
projet a deja tranche pour la famille d'un marche : une regle deterministe
laissee au modele coute des tokens, se refait a chaque session, et ne se mesure
jamais.
"""

from __future__ import annotations

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import picks_import
from myassistantbet.services.confidence import (
    OPEN_ABSENT,
    OPEN_EMPTY,
    OPEN_MALFORMED,
    OPEN_READ,
    OVERRIDE_AUCUN_DOSSIER,
    OVERRIDE_HORS_DOSSIERS,
    OVERRIDE_LIGNE_ABSENTE,
    OVERRIDE_LIGNE_ILLISIBLE,
    OVERRIDE_REPERES,
    OVERRIDE_SANS_FAIT,
    Claim,
    ClaimError,
    Fact,
    Opened,
    is_collection_fault,
    parse,
    publisher_of,
    read_blocks,
    read_opened,
)
from myassistantbet.services.history import (
    add_pick,
    analysis,
    prompt_priorities,
    set_open_dossiers,
    set_result,
)

LOIN = "2099-01-01T20:45:00Z"


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _fait(publisher: str = "motherwellfc.co.uk", level: int = 1) -> dict[str, object]:
    return {
        "enonce": "retour de Johnny Koutroumbis",
        "date": "2026-08-12",
        "editeur": publisher,
        "niveau": level,
    }


def _bloc(**champs: object) -> str:
    payload: dict[str, object] = {"match": "M8", "type": "issue"}
    payload.update(champs)
    return json.dumps(payload, ensure_ascii=False)


# -- La table des crans, cas par cas ----------------------------------------


def test_deux_editeurs_distincts_en_niveau_haut_donnent_cinq() -> None:
    claim = parse(
        _bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
            manque_touche_facteur=False,
        )
    )

    assert claim.distinct_publishers == 2
    assert claim.rung == 5


def test_un_fait_dominant_sans_manque_donne_quatre() -> None:
    claim = parse(_bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False))

    assert claim.rung == 4


def test_un_manque_qui_touche_le_facteur_ramene_a_trois() -> None:
    """Le cran 3 ne se distingue du 4 que par la : c'est pour ca que le champ
    n'a pas de defaut."""
    claim = parse(_bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True))

    assert claim.rung == 3


def test_un_manque_qui_touche_le_facteur_ramene_meme_deux_editeurs_a_trois() -> None:
    claim = parse(
        _bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
            manque_touche_facteur=True,
        )
    )

    assert claim.rung == 3


@pytest.mark.parametrize("niveau", ["3", "4"])
def test_une_source_faible_plafonne_a_deux(niveau: str) -> None:
    """La regle du preambule, ecrite une seule fois dans le module."""
    claim = parse(
        _bloc(source_level=niveau, faits=[_fait(level=int(niveau))], manque_touche_facteur=False)
    )

    assert claim.rung == 2


def test_aucun_fait_donne_un() -> None:
    claim = parse(_bloc(source_level="lecture", faits=[], manque_touche_facteur=False))

    assert claim.rung == 1


def test_une_liste_vide_impose_lecture_meme_si_le_niveau_dit_autre_chose() -> None:
    """**La liste de faits fait foi, pas le niveau declare.** Une selection sans
    fait est une lecture des blocs, quelle que soit la qualite du fournisseur
    qui les a remplis — c'est ce qui empeche un bloc de contexte d'etre promu au
    rang de source citee."""
    claim = parse(_bloc(source_level=1, faits=[], manque_touche_facteur=False))

    assert claim.rung == 1


# -- L'unicite d'editeur ----------------------------------------------------


def test_deux_articles_du_meme_editeur_ne_font_qu_un_facteur() -> None:
    """« Il faut deux origines qui puissent se tromper separement. »"""
    claim = parse(
        _bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("https://www.motherwellfc.co.uk/news/2")],
            manque_touche_facteur=False,
        )
    )

    assert claim.distinct_publishers == 1
    assert claim.rung == 4, "deux pages du meme site ne valent pas un faisceau"


def test_un_fait_de_niveau_faible_ne_compte_pas_vers_le_cran_cinq() -> None:
    """« au moins deux faits d'editeurs distincts, **tous en niveau 1-2** »."""
    claim = parse(
        _bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk", 1), _fait("agregateur.com", 4)],
            manque_touche_facteur=False,
        )
    )

    assert claim.distinct_publishers == 1
    assert claim.rung == 4


@pytest.mark.parametrize(
    ("brut", "attendu"),
    [
        ("https://www.motherwellfc.co.uk/news/2026/08/", "motherwellfc.co.uk"),
        ("MotherwellFC.co.uk", "motherwellfc.co.uk"),
        ("bbc.co.uk/sport?x=1", "bbc.co.uk"),
        # Ni un domaine, donc rien : l'unicite se teste sur le domaine, et ce
        # qui n'est pas verifiable ne compte pas.
        ("Motherwell FC", ""),
        ("communiqué du club", ""),
        ("", ""),
    ],
)
def test_le_domaine_se_normalise(brut: str, attendu: str) -> None:
    assert publisher_of(brut) == attendu


# -- Ce qui laisse le cran inconnu ------------------------------------------


def test_sans_le_drapeau_de_manque_le_cran_reste_inconnu() -> None:
    """**Trois etats, et le troisieme n'est pas un defaut.** Les crans 3, 4 et 5
    ne se distinguent que par lui : le deviner reviendrait a choisir un cran a
    la place de l'analyse."""
    claim = parse(_bloc(source_level=1, faits=[_fait()]))

    assert claim.gap_touches_factor is None
    assert claim.rung is None


def test_un_fait_sans_editeur_exploitable_est_refuse() -> None:
    with pytest.raises(ClaimError, match="editeur"):
        parse(_bloc(source_level=1, faits=[_fait("le club")], manque_touche_facteur=False))


def test_un_fait_sans_date_est_refuse() -> None:
    """Une date rend le fait verifiable en une recherche ; sans elle il ne porte
    pas un cran."""
    with pytest.raises(ClaimError, match="date"):
        parse(
            _bloc(
                source_level=1,
                faits=[{"enonce": "x", "editeur": "bbc.co.uk", "niveau": 1}],
                manque_touche_facteur=False,
            )
        )


def test_un_niveau_de_source_hors_echelle_est_refuse() -> None:
    with pytest.raises(ClaimError, match="source_level"):
        parse(_bloc(source_level="peut-etre", faits=[], manque_touche_facteur=False))


def test_un_json_casse_est_refuse() -> None:
    with pytest.raises(ClaimError, match="illisible"):
        parse("{ceci n'est pas du JSON}")


def test_les_blocs_rejetes_sont_dits_et_non_tus() -> None:
    """Meme regle que les lignes rejetees de la saisie manuelle : un bloc qui ne
    passe pas doit se voir, sans quoi la colonne reste vide sans que personne
    sache pourquoi."""
    bon = _bloc(source_level=1, faits=[], manque_touche_facteur=False)
    rendu = f"prose\n```conf\n{bon}\n```\n```conf\n{{cassé}}\n```"

    lecture = read_blocks(rendu)

    assert len(lecture.claims) == 1
    assert len(lecture.rejected) == 1
    assert "bloc 2" in lecture.rejected[0]


def test_l_ecart_ne_se_declare_pas_sans_les_deux_valeurs() -> None:
    assert not Claim(source_level="1", facts=(Fact("x", "2026-08-12", "bbc.co.uk", 1),)).disagrees


# -- Le parcours complet : du copier-coller a la base -----------------------


TABLEAU = """| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Type | Source |
|---|-------|--------|-----------|------|--------|--------|------|--------|
| 1 | Lyon – Nice | 1N2 | Lyon | 1.65 | 🟢 SAFE | 4 | issue | 1 |
"""


def _rendu(**champs: object) -> str:
    return TABLEAU + "\n```conf\n" + _bloc(**champs) + "\n```\n"


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


def test_un_bloc_dont_le_repere_ne_designe_aucune_ligne_fait_tomber_le_lot(
    migrated: Settings,
) -> None:
    """**La somme de controle n'a pas bouge en passant a l'appariement par
    repere.** Un bloc dont le repere ne designe aucune ligne du tableau ne se
    range nulle part, et le lot entier reste sans cran : poser les autres
    reviendrait a retenir les paires qui arrangent.

    Le message, lui, a change : il ne parle plus d'un compte de blocs — le compte
    a cesse de decider — mais du repere qui bloque.
    """
    session_id = _session(migrated)
    deux_blocs = _rendu(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    deux_blocs += "```conf\n" + _bloc(source_level="lecture", faits=[]) + "\n```\n"

    preview = picks_import.build_preview(session_id, deux_blocs, migrated)

    assert preview.picks[0].claim is None
    assert any("repères de match" in note for note in preview.notes)


def test_le_cran_calcule_arrive_en_base(migrated: Settings) -> None:
    session_id = _session(migrated)
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)["id"]

    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        event_id=str(event_id),
        confidence="4",
        claim=_bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
            manque_touche_facteur=False,
        ),
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT confidence, confidence_computed, distinct_publishers, gap_touches_factor, "
        "       claim_raw_json, source_level FROM picks",
        settings=migrated,
    )
    assert ligne["confidence"] == 4, "le cran annonce reste ecrit tel quel"
    assert ligne["confidence_computed"] == 5
    assert ligne["distinct_publishers"] == 2
    assert ligne["gap_touches_factor"] == 0
    assert "motherwellfc.co.uk" in ligne["claim_raw_json"]
    assert ligne["source_level"] == "1"


def test_un_bloc_illisible_laisse_le_cran_nul_sans_repli(migrated: Settings) -> None:
    """**Aucun repli silencieux sur la valeur declaree.** Retomber sur l'annonce
    ferait passer pour calculee une note qui ne l'est pas, et le taux de
    desaccord annoncerait alors un accord parfait."""
    session_id = _session(migrated)

    add_pick(session_id, "safe", "1N2", "Lyon", confidence="4", claim="{cassé}", settings=migrated)

    ligne = db.query_one("SELECT confidence, confidence_computed FROM picks", settings=migrated)
    assert ligne["confidence"] == 4
    assert ligne["confidence_computed"] is None


def test_rien_n_est_retro_rempli(migrated: Settings) -> None:
    """Les selections d'avant ce chantier n'ont pas de bloc : un faisceau
    d'information ne s'invente pas apres coup."""
    session_id = _session(migrated)

    add_pick(session_id, "safe", "1N2", "Lyon", confidence="3", settings=migrated)

    ligne = db.query_one("SELECT confidence_computed FROM picks", settings=migrated)
    assert ligne["confidence_computed"] is None


# -- Ce que la page en fait -------------------------------------------------


def _tranchee(settings: Settings, session_id: int, confiance: str, bloc: str) -> None:
    pick_id = add_pick(
        settings=settings,
        session_id=session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        confidence=confiance,
        claim=bloc,
    )
    set_result(pick_id, "win", settings)


def test_la_page_mesure_le_desaccord_entre_les_deux_crans(migrated: Settings) -> None:
    """**C'est la mesure pour laquelle les deux colonnes coexistent.** Un cran
    calcule qui retomberait toujours sur l'annonce dirait que le modele
    appliquait deja la table ; un desaccord large dit qu'il notait a l'estime."""
    session_id = _session(migrated)
    accord = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)  # cran 4
    desaccord = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)  # cran 3
    _tranchee(migrated, session_id, "4", accord)
    _tranchee(migrated, session_id, "5", desaccord)

    report = analysis(settings=migrated)

    assert report.notation.comparable == 2
    assert report.notation.agreed == 1
    assert report.notation.disagreed == 1
    assert report.notation.rate == 0.5
    assert report.notation.drift == 1.0, "le modele s'est note un cran trop haut en moyenne"
    assert "trop haut" in report.notation.line


def test_une_selection_sans_bloc_ne_compte_pas_comme_un_accord(migrated: Settings) -> None:
    """Un cran manquant n'est pas un cran d'accord — l'erreur que la page a deja
    payee sur les lignes maigres."""
    session_id = _session(migrated)
    _tranchee(migrated, session_id, "4", "")

    report = analysis(settings=migrated)

    assert report.notation.comparable == 0
    assert report.notation.uncomputed == 1
    assert report.notation.rate is None


def test_l_axe_calcule_vit_a_cote_de_l_annonce(migrated: Settings) -> None:
    """Rendu a cote et jamais a la place : tant que les deux populations ne se
    recouvrent pas, comparer leurs taux comparerait deux echantillons."""
    session_id = _session(migrated)
    lecture = _bloc(source_level=1, faits=[], manque_touche_facteur=False)
    _tranchee(migrated, session_id, "4", lecture)

    report = analysis(settings=migrated)

    assert [row.key for row in report.by_confidence] == ["4"]
    assert [row.key for row in report.by_confidence_computed] == ["1"]


# -- L'editeur d'origine : ce que le domaine ne peut pas voir ---------------
#
# **La normalisation de domaine sur-compte l'independance exactement la ou le
# gabarit previent.** Un article qui reprend un autre editeur, ou deux titres
# qui rapportent la meme conference de presse, sortent sur deux domaines
# distincts et feraient un cran 5 — alors que ca ne fait qu'un seul facteur.


def test_deux_relais_de_la_meme_origine_ne_font_qu_un_facteur() -> None:
    """Le cas que `publisher_of` ne peut pas trancher seul : c'est une propriete
    du contenu, pas de l'URL."""
    reprise = _fait("onefootball.com")
    reprise["editeur_origine"] = "glorioso1904.com"
    source = _fait("glorioso1904.com")

    claim = parse(_bloc(source_level=1, faits=[reprise, source], manque_touche_facteur=False))

    assert claim.distinct_publishers == 1
    assert claim.rung == 4, "deux relais du meme fait ne peuvent pas se tromper separement"


def test_deux_medias_sur_la_meme_conference_de_presse_ne_font_qu_un_facteur() -> None:
    """« l'editeur d'origine est le club » — la phrase du gabarit, executee."""
    premier = _fait("bbc.co.uk", 2)
    premier["editeur_origine"] = "motherwellfc.co.uk"
    second = _fait("skysports.com", 2)
    second["editeur_origine"] = "motherwellfc.co.uk"

    claim = parse(_bloc(source_level=2, faits=[premier, second], manque_touche_facteur=False))

    assert claim.rung == 4


def test_sans_origine_declaree_l_editeur_compte_comme_avant() -> None:
    """Le cas ordinaire : les deux se confondent, le champ reste vide."""
    claim = parse(
        _bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
            manque_touche_facteur=False,
        )
    )

    assert claim.rung == 5


def test_une_origine_illisible_est_refusee() -> None:
    """Meme severite que l'editeur : une origine qui n'est pas un domaine
    laisserait compter l'independance sur le relais."""
    reprise = _fait("onefootball.com")
    reprise["editeur_origine"] = "le blog de Glorioso"

    with pytest.raises(ClaimError, match="origine"):
        parse(_bloc(source_level=1, faits=[reprise], manque_touche_facteur=False))


# -- La somme de controle de l'appariement ----------------------------------
#
# **Le compte seul ne suffisait pas** : nombre egal et ordre different donnait
# des crans tous decales d'un rang, en silence. Un cran faux ne se voit pas, la
# ou un cran inconnu se voit.


def _lot_de_deux(settings: Settings) -> tuple[int, str]:
    """Une session de deux matchs, avec le prompt archive qui les numerote."""
    sport = db.query_one("SELECT id FROM sports WHERE key = 'football'", settings=settings)
    for home, away in (("Lyon", "Nice"), ("Reims", "Brest")):
        db.execute(
            "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
            "VALUES (?, ?, ?, ?, 'oddsapi', ?)",
            (sport["id"], home, away, LOIN, db.utcnow()),
            settings=settings,
        )
    db.execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    session_id = int(db.query_one("SELECT MAX(id) AS id FROM sessions", settings=settings)["id"])
    corps = (
        "### M1 · Football · Ligue 1 · Lyon – Nice · 01/01 20:45\n"
        "### M2 · Football · Ligue 1 · Reims – Brest · 01/01 20:45\n"
    )
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, corps, db.utcnow()),
        settings=settings,
    )
    return session_id, corps


TABLEAU_DEUX = """| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Lyon – Nice | 1N2 | Lyon | 1.65 | 🟢 SAFE | 4 |
| 2 | Reims – Brest | 1N2 | Reims | 1.80 | 🔵 FUN | 3 |
"""


def _avec_blocs(*reperes: str) -> str:
    rendu = TABLEAU_DEUX
    for repere in reperes:
        bloc = _bloc(match=repere, source_level=1, faits=[_fait()], manque_touche_facteur=False)
        rendu += f"\n```conf\n{bloc}\n```\n"
    return rendu


def test_le_bloc_est_lu_dans_le_meme_copier_coller_et_apparie_au_prompt(
    migrated: Settings,
) -> None:
    """Un second geste ferait perdre la colonne le jour ou on l'oublie, et c'est
    la seule qui rende le cran calculable. L'appariement, lui, se verifie contre
    les en-tetes du prompt archive."""
    session_id, _ = _lot_de_deux(migrated)

    preview = picks_import.build_preview(session_id, _avec_blocs("M1", "M2"), migrated)

    assert [pick.claim is not None for pick in preview.picks] == [True, True]
    assert [pick.claim.rung for pick in preview.picks] == [4, 4]
    assert not preview.ignored


def test_des_blocs_dans_le_desordre_se_rangent_par_leur_repere(migrated: Settings) -> None:
    """**Ce que l'appariement par repere change, et c'est une amelioration.**

    Deux blocs inverses coutaient auparavant les crans du lot entier : l'ordre
    etait la cle, et un ordre faux ne pouvait pas se distinguer d'un decalage. Le
    repere, lui, nomme son match — chacun se range chez lui, et le refus n'a plus
    lieu d'etre.

    La garde qui comptait reste entiere : c'est la somme de controle sur
    l'affiche, pas l'ordre, qui empeche un cran d'atterrir sur la mauvaise ligne.
    """
    session_id, _ = _lot_de_deux(migrated)

    preview = picks_import.build_preview(session_id, _avec_blocs("M2", "M1"), migrated)

    assert [pick.claim.match for pick in preview.picks] == ["M1", "M2"], (
        "chaque bloc se range sur la ligne que son repère désigne, pas sur sa voisine"
    )
    assert not [note for note in preview.notes if "repères de match" in note]


def test_sans_prompt_archive_rien_n_est_rattache(migrated: Settings) -> None:
    """Ce qui ne peut pas se relire ne doit pas pouvoir s'ecrire : sans en-tetes,
    l'ordre n'est verifiable par rien."""
    session_id = _session(migrated)

    preview = picks_import.build_preview(
        session_id,
        _rendu(source_level=1, faits=[_fait()], manque_touche_facteur=False),
        migrated,
    )

    assert preview.picks[0].claim is None
    assert any("repères de match" in note for note in preview.notes)


def test_un_prompt_d_une_autre_generation_ne_valide_pas_a_moitie(migrated: Settings) -> None:
    """**Un prompt valide l'ensemble ou ne le valide pas.** Retenir le meilleur
    des prompts paire par paire reviendrait a piocher la lecture qui arrange."""
    session_id, _ = _lot_de_deux(migrated)
    # Une generation anterieure ou M1 designait l'autre affiche.
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, "### M1 · Football · Ligue 1 · Reims – Brest · 01/01 20:45\n", db.utcnow()),
        settings=migrated,
    )

    preview = picks_import.build_preview(session_id, _avec_blocs("M1", "M2"), migrated)

    assert [pick.claim is not None for pick in preview.picks] == [True, True], (
        "la generation la plus recente valide bien les deux paires"
    )


# -- La matrice des transitions ---------------------------------------------


def test_un_desaccord_concentre_designe_la_clause_a_reecrire(migrated: Settings) -> None:
    """**Ce que l'ecart mesure a change de nature.** Les deux valeurs sortent du
    meme faisceau : l'ecart ne teste plus le flair du modele, il teste s'il
    applique sa propre table. Un desaccord concentre sur un passage designe une
    clause ambigue, qui se reecrit.

    **L'effectif est volontairement au-dessus du seuil de la page** : en dessous,
    le test passerait par le plancher et ne dirait plus rien de la
    concentration.
    """
    session_id = _session(migrated)
    quatre = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    for _ in range(6):
        _tranchee(migrated, session_id, "4", trois)  # annonce 4, table 3
    for _ in range(2):
        _tranchee(migrated, session_id, "4", quatre)  # accord

    notation = analysis(settings=migrated).notation

    assert notation.comparable == 8
    assert notation.transitions == [(4, 3, 6)]
    assert notation.dominant == (4, 3, 6)
    assert "un 4 annoncé que la table met à 3" in notation.clause_line


def test_un_desaccord_disperse_ne_designe_aucune_clause(migrated: Settings) -> None:
    """Sous la moitie, le desaccord est du bruit de redaction : en nommer une
    clause quand meme ferait reecrire le gabarit sur rien.

    **L'effectif est au-dessus du seuil a dessein.** Ecrit sur deux desaccords,
    ce test passait par le plancher d'effectif et ne verifiait plus l'ex aequo —
    il aurait continue de passer si la regle de concentration disparaissait.
    """
    session_id = _session(migrated)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    lecture = _bloc(source_level="lecture", faits=[])
    for _ in range(4):
        _tranchee(migrated, session_id, "4", trois)  # 4 -> 3
    for _ in range(4):
        _tranchee(migrated, session_id, "3", lecture)  # 3 -> 1

    notation = analysis(settings=migrated).notation

    assert notation.comparable == 8, "au-dessus du seuil, donc c'est bien l'ex aequo"
    assert notation.disagreed == 8
    assert notation.dominant is None
    assert notation.clause_line == ""


# -- La somme de controle : normalisation deterministe, egalite stricte ------


def test_la_typographie_est_absorbee_par_la_normalisation(migrated: Settings) -> None:
    """Casse, accents et tirets ne doivent pas faire tomber un lot entier : le
    tiret long du prompt rendu en tiret court est le cas courant."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2").replace("Lyon – Nice", "lyon - NICE")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.claim is not None for pick in preview.picks] == [True, True]


def test_l_egalite_est_stricte_apres_normalisation(migrated: Settings) -> None:
    """**Une similarite floue ne controlerait plus rien.** Une affiche reduite a
    un seul nom se trouverait dans plusieurs en-tetes, donc ne prouverait pas
    l'appariement. Elle est refusee, et la perte est visible — c'est le bon sens
    du compromis, un cran decale ne se voyant pas."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2").replace("Lyon – Nice", "Lyon")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.claim for pick in preview.picks] == [None, None]
    assert any("repères de match" in note for note in preview.notes)


def test_un_en_tete_de_forme_inattendue_invalide_la_paire(migrated: Settings) -> None:
    """Une somme de controle qui s'accommode de ce qu'elle ne reconnait pas ne
    controle plus rien."""
    session_id, _ = _lot_de_deux(migrated)
    db.execute("DELETE FROM prompts WHERE session_id = ?", (session_id,), settings=migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, "### M1 · Lyon – Nice\n### M2 · Reims – Brest\n", db.utcnow()),
        settings=migrated,
    )

    preview = picks_import.build_preview(session_id, _avec_blocs("M1", "M2"), migrated)

    assert [pick.claim for pick in preview.picks] == [None, None]


# -- Le seuil d'effectif garde aussi la matrice -----------------------------


def test_aucune_clause_n_est_designee_sous_le_seuil(migrated: Settings) -> None:
    """**Le seuil de la page, reutilise et non redefini.** La sortie de ce bloc
    est une consigne — reecrire une clause du gabarit — donc la publier sur trois
    desaccords ferait reecrire un texte sur du bruit."""
    session_id = _session(migrated)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    for _ in range(3):
        _tranchee(migrated, session_id, "4", trois)

    notation = analysis(settings=migrated).notation

    assert notation.disagreed == 3
    assert notation.transitions == [(4, 3, 3)], "le comptage reste fait"
    assert notation.dominant is None, "mais rien ne se conclut sous le seuil"
    assert notation.clause_line == ""


def test_le_seuil_de_la_matrice_est_celui_de_la_page(migrated: Settings) -> None:
    """Reutilise, jamais redefini : sous quel compte une repartition ne veut plus
    rien dire est une propriete des donnees, pas du bloc qui les affiche."""
    report = analysis(settings=migrated)

    assert report.notation.minimum == report.minimum_rows


# -- L'echec doit nommer la paire, sinon il est terminal ---------------------


def test_le_rejet_nomme_la_paire_en_cause(migrated: Settings) -> None:
    """« Ça se rattrape en recollant » n'est un chemin de reprise que si le
    message dit **quoi** corriger : recoller le meme texte echoue a l'identique,
    et il ne reste rien a faire."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2").replace("Reims – Brest", "Reims")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    note = " ".join(preview.notes)
    assert "bloc M2" in note
    # **Ce que le message doit dire a change avec l'appariement**, et la propriete
    # non : il nomme le repere qui bloque et l'affiche qu'il designe, apres
    # normalisation — c'est sous cette forme que la comparaison a eu lieu, et
    # deux caracteres a corriger s'y voient.
    assert "reims brest" in note, "l'affiche attendue, après normalisation"


def test_sans_prompt_le_rejet_le_dit(migrated: Settings) -> None:
    session_id = _session(migrated)

    preview = picks_import.build_preview(
        session_id,
        _rendu(source_level=1, faits=[_fait()], manque_touche_facteur=False),
        migrated,
    )

    assert any("Aucun prompt n'est archivé" in note for note in preview.notes)


def test_une_paire_invalide_fait_tomber_le_lot_entier(migrated: Settings) -> None:
    """**Jamais un retrait paire par paire.** Laisser passer les autres serait le
    « meilleur des prompts paire par paire » qu'on a ecarte, et l'appariement des
    lignes restantes ne serait plus demontre par rien."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2").replace("Reims – Brest", "Reims")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.claim for pick in preview.picks] == [None, None], (
        "la ligne 1 était pourtant appariée : elle tombe avec le lot"
    )


def test_le_gabarit_impose_de_recopier_l_affiche(migrated: Settings) -> None:
    """Le mode d'echec le plus probable de l'egalite stricte est l'abreviation,
    et une phrase le supprime. Meme regle que la cote, recopiee au centime pres."""
    from myassistantbet.services.prompt import build_prompt

    corps = build_prompt(_session(migrated), settings=migrated).body

    assert "telle qu'elle est écrite en\ntête du bloc" in corps
    assert "sans abréger" in corps


# == CHANTIER 2 : le niveau de source cesse d'etre pris au mot ===============
#
# **Mesure : 0 selection en `lecture` sur 149**, quand le budget de recherche
# vaut sept dossiers pour des lots de 57 a 72 matchs. Le defaut doit donc etre
# `lecture`, jamais « ouvert » : un `lecture` de trop se voit et se corrige, un
# niveau de source gonfle qui passe pour verifie ne se voit pas.


def _avec_dossiers(rendu: str, ligne: str) -> str:
    return rendu + "\n" + ligne + "\n"


def test_une_selection_sur_un_dossier_ouvert_garde_son_niveau(migrated: Settings) -> None:
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: [M1]")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.opened for pick in preview.picks] == [True, False]


def test_sans_ligne_de_dossiers_tout_part_en_lecture(migrated: Settings) -> None:
    """**Fail-closed.** Liste absente : rien n'est demontre ouvert."""
    session_id, _ = _lot_de_deux(migrated)

    preview = picks_import.build_preview(session_id, _avec_blocs("M1", "M2"), migrated)

    assert [pick.opened for pick in preview.picks] == [False, False]
    assert any("dossiers_ouverts" in note for note in preview.notes)
    # Et **pourquoi** : le drapeau seul confond une ligne jamais collee avec un
    # match hors de la liste, qui n'appellent pas le meme geste.
    assert [pick.override_cause for pick in preview.picks] == [
        OVERRIDE_LIGNE_ABSENTE,
        OVERRIDE_LIGNE_ABSENTE,
    ]


def test_la_cause_distingue_le_hors_liste_de_la_ligne_absente(migrated: Settings) -> None:
    """Les deux ecrasent, et une seule dit quelque chose du modele.

    C'est le defaut mesure le 14/08/2026 : les 16 selections ecrasees de la base
    se lisaient comme « aucune ne portait sur un dossier ouvert » alors que la
    ligne n'avait jamais ete collee.
    """
    session_id, _ = _lot_de_deux(migrated)

    lue = picks_import.build_preview(
        session_id, _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: [M1]"), migrated
    )
    assert [pick.override_cause for pick in lue.picks] == ["", OVERRIDE_HORS_DOSSIERS]
    assert not is_collection_fault(lue.picks[1].override_cause), "observation sur le modele"

    vide = picks_import.build_preview(
        session_id, _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: []"), migrated
    )
    assert [pick.override_cause for pick in vide.picks] == [OVERRIDE_AUCUN_DOSSIER] * 2
    assert vide.opened.state == OPEN_EMPTY
    assert "aucun dossier déclaré" in vide.readout


def test_la_cause_traverse_le_formulaire_et_arrive_en_base(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le service et sa surface se livrent ensemble.**

    Un test qui appelle `add_pick` directement passerait alors meme que le
    formulaire ne transmet pas le champ — c'est exactement ainsi que le motif de
    saisie tardive est reste sans surface pendant deux jours, et que les blocs
    ```conf ont ete produits neuf lots sans jamais etre colles. Celui-ci poste le
    formulaire et relit la base.
    """
    session_id, _ = _lot_de_deux(isolated_settings)

    # L'apercu doit **emettre** le champ cache : sans lui la cause n'a aucun
    # chemin pour revenir, et l'ecrasement redevient muet.
    apercu = client.post(
        f"/history/{session_id}/picks/preview", data={"table": _avec_blocs("M1", "M2")}
    )
    # A plat : le retour a la ligne entre deux attributs est une largeur de
    # colonne, pas une regle, et un test qui casse dessus n'apprend rien.
    rendu = " ".join(apercu.text.split())
    assert f'name="override_cause_1" value="{OVERRIDE_LIGNE_ABSENTE}"' in rendu

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "tier_1": "safe",
            "opened_1": "0",
            "override_cause_1": OVERRIDE_LIGNE_ABSENTE,
        },
    )

    ligne = db.query_one(
        "SELECT research_overridden, research_override_cause FROM picks",
        settings=isolated_settings,
    )
    assert ligne["research_overridden"] == 1
    assert ligne["research_override_cause"] == OVERRIDE_LIGNE_ABSENTE


def test_une_liste_vide_est_une_declaration_et_non_un_manque(migrated: Settings) -> None:
    """Les deux appellent le meme traitement, pas le meme message."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: []")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.opened for pick in preview.picks] == [False, False]
    assert any("Aucun dossier déclaré ouvert" in note for note in preview.notes)


def test_un_repere_qui_ne_se_resout_pas_fait_tout_tomber(migrated: Settings) -> None:
    """Meme raisonnement que la somme de controle de l'appariement : ce qui ne
    se demontre pas ne s'ecrit pas comme un acquis."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: [M1, M99]")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.opened for pick in preview.picks] == [False, False]
    assert any("ne se résolvent contre aucun prompt" in note for note in preview.notes)


def test_la_ligne_de_dossiers_ne_casse_pas_le_compte_des_blocs(migrated: Settings) -> None:
    """Le gabarit la demande hors de tout bloc, mais un rendu peut la cloturer
    quand meme : comptee comme un bloc en echec, elle couterait les crans du
    lot entier pour une ligne qui n'en est pas un."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2") + '\n```conf\n{"dossiers_ouverts": [M1]}\n```\n'

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.claim is not None for pick in preview.picks] == [True, True]
    assert [pick.opened for pick in preview.picks] == [True, False]


def test_l_ecrasement_enregistre_sa_cause(migrated: Settings) -> None:
    """**Un cran 1 sans sa cause ne mesure rien.**

    Mesure du 14/08/2026 : les 16 selections ecrasees de la base viennent toutes
    d'une ligne `dossiers_ouverts` jamais collee, et se lisaient comme « aucune
    selection ne portait sur un dossier ouvert » — une observation sur le modele.
    Les deux situations donnent le meme `research_overridden = 1` et n'appellent
    pas le meme geste : l'une se repare en recollant, l'autre se constate.
    """
    session_id = _session(migrated)
    for cause in (OVERRIDE_LIGNE_ABSENTE, OVERRIDE_HORS_DOSSIERS):
        add_pick(
            session_id,
            "safe",
            "1N2",
            f"Lyon {cause}",
            confidence="4",
            source_level="1",
            claim=_bloc(source_level=1, faits=[_fait("bbc.co.uk")], manque_touche_facteur=False),
            opened=False,
            override_cause=cause,
            settings=migrated,
        )

    lignes = db.query(
        "SELECT research_overridden, research_override_cause, confidence_computed "
        "FROM picks ORDER BY id",
        settings=migrated,
    )
    # Le drapeau ne les separe pas — c'est tout le probleme — et la cause si.
    assert [ligne["research_overridden"] for ligne in lignes] == [1, 1]
    assert [ligne["confidence_computed"] for ligne in lignes] == [1, 1]
    assert [ligne["research_override_cause"] for ligne in lignes] == [
        OVERRIDE_LIGNE_ABSENTE,
        OVERRIDE_HORS_DOSSIERS,
    ]
    # Et elles ne se comptent pas ensemble : la premiere est une panne de
    # transmission, la seconde une observation sur ce que l'analyse a fait.
    assert is_collection_fault(OVERRIDE_LIGNE_ABSENTE)
    assert not is_collection_fault(OVERRIDE_HORS_DOSSIERS)


def test_la_cause_se_deduit_de_l_etat_de_la_ligne(migrated: Settings) -> None:
    """Chaque etat de la ligne donne sa cause, et `hors_dossiers` est la seule
    qui se decide selection par selection — c'est le seul cas ou la liste a
    vraiment servi."""
    assert Opened(state=OPEN_ABSENT).cause(resolved=False) == OVERRIDE_LIGNE_ABSENTE
    assert Opened(state=OPEN_MALFORMED).cause(resolved=False) == OVERRIDE_LIGNE_ILLISIBLE
    assert Opened(state=OPEN_EMPTY).cause(resolved=False) == OVERRIDE_AUCUN_DOSSIER
    # Renseignee mais rattachee a aucun prompt : le collage a bien porte la
    # ligne, c'est le rapprochement qui a echoue. Defaut de collecte, pas
    # observation — sans quoi un appariement rate se lirait comme un modele qui
    # n'a rien ouvert.
    marks = frozenset({"M1"})
    assert Opened(marks=marks, state=OPEN_READ).cause(resolved=False) == OVERRIDE_REPERES
    assert Opened(marks=marks, state=OPEN_READ).cause(resolved=True) == OVERRIDE_HORS_DOSSIERS

    faute = {Opened(state=etat).cause(resolved=False) for etat in (OPEN_ABSENT, OPEN_MALFORMED)}
    assert all(is_collection_fault(cause) for cause in faute)
    assert not is_collection_fault(OVERRIDE_AUCUN_DOSSIER), "le modele a repondu"


def test_un_dossier_ouvert_sans_fait_porte_sa_propre_cause(migrated: Settings) -> None:
    """La regle est a sens unique : la presence d'un dossier n'accorde rien.

    Cette cause-la est la seule des six qui dise que la recherche **a eu lieu**
    et n'a rien donne. Elle ne passe pas par `research_overridden`, qui ne compte
    que les dossiers non ouverts — et sans elle ce cran 1 se confondrait avec
    ceux qu'aucune recherche n'a jamais approches.
    """
    session_id = _session(migrated)
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        confidence="4",
        source_level="1",
        claim=_bloc(source_level=1, faits=[], manque_touche_facteur=False),
        opened=True,
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT research_overridden, research_override_cause, confidence_computed FROM picks",
        settings=migrated,
    )
    assert ligne["confidence_computed"] == 1
    assert not ligne["research_overridden"], "le dossier etait ouvert"
    assert ligne["research_override_cause"] == OVERRIDE_SANS_FAIT
    assert not is_collection_fault(OVERRIDE_SANS_FAIT)


def test_l_ecrasement_arrive_en_base(migrated: Settings) -> None:
    session_id = _session(migrated)
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        confidence="4",
        source_level="1",
        claim=_bloc(
            source_level=1,
            faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
            manque_touche_facteur=False,
        ),
        opened=False,
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT confidence, confidence_computed, confidence_claimed, research_overridden, "
        "       source_level, source_level_effective, claim_raw_json FROM picks",
        settings=migrated,
    )
    assert ligne["confidence"] == 4, "l'annonce reste ecrite telle quelle"
    assert ligne["confidence_computed"] == 1, "le verdict final"
    assert ligne["confidence_claimed"] == 5, "ce que la declaration aurait donne"
    assert ligne["research_overridden"] == 1
    # **Les deux declarations sont les entrees de la mesure, et rien ne les
    # ecrase.** L'ecraser ferait mesurer a la page sa propre correction : un
    # accord parfait entre ce que l'application a ecrit et ce qu'elle relit.
    assert ligne["source_level"] == "1", "l'annonce de niveau reste ecrite telle quelle"
    assert ligne["source_level_effective"] == "lecture", "l'effectif vit a cote"
    assert "motherwellfc.co.uk" in ligne["claim_raw_json"], (
        "les faits restent en base : c'est la trace de la fabrication"
    )


def test_la_saisie_a_la_main_n_est_jamais_ecrasee(migrated: Settings) -> None:
    """`None` veut dire « on ne sait pas » : l'override juge une declaration de
    modele, pas un geste humain."""
    session_id = _session(migrated)

    add_pick(session_id, "safe", "1N2", "Lyon", source_level="1", settings=migrated)

    ligne = db.query_one("SELECT source_level, research_overridden FROM picks", settings=migrated)
    assert ligne["source_level"] == "1"
    assert ligne["research_overridden"] is None


def _ecrasee(settings: Settings, session_id: int, confiance: str, bloc: str) -> None:
    pick_id = add_pick(
        settings=settings,
        session_id=session_id,
        tier="safe",
        market="1N2",
        selection="Lyon",
        confidence=confiance,
        claim=bloc,
        opened=False,
    )
    set_result(pick_id, "win", settings)


def test_l_override_compte_la_distribution_et_non_le_total(migrated: Settings) -> None:
    """**Deux fautes que le compte seul confondrait.** Un 3 revendique est de
    l'inflation ; un 5 — deux faits dates, deux editeurs, une origine — est de la
    fabrication, et ca ne se traite pas pareil."""
    session_id = _session(migrated)
    cinq = _bloc(
        source_level=1,
        faits=[_fait("motherwellfc.co.uk"), _fait("bbc.co.uk", 2)],
        manque_touche_facteur=False,
    )
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    _ecrasee(migrated, session_id, "5", cinq)
    for _ in range(2):
        _ecrasee(migrated, session_id, "3", trois)

    override = analysis(settings=migrated).override

    assert override.total == 3
    assert override.claimed == [(3, 2), (5, 1)]
    assert override.fabricated == 1, "le 5 revendique des faits sur un dossier non ouvert"
    assert "dont 1 avec des faits déclarés" in override.line


def test_les_ecrasees_sortent_de_la_matrice_des_transitions(migrated: Settings) -> None:
    """**Sinon la matrice ne mesurerait plus que l'override.** Le modele annonce
    3, l'application force 1 : ce desaccord ne dit pas « il applique mal sa
    table », il dit « il revendique une recherche qu'il n'a pas faite ». Deux
    fautes, deux compteurs."""
    session_id = _session(migrated)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    for _ in range(9):
        _ecrasee(migrated, session_id, "4", trois)

    report = analysis(settings=migrated)

    assert report.override.total == 9
    assert report.notation.comparable == 0, "aucune ecrasee ne pese sur la notation"
    assert report.notation.transitions == []
    assert report.notation.clause_line == ""


def test_la_liste_declaree_est_memorisee_a_l_import(
    client: TestClient, isolated_settings: Settings
) -> None:
    """La liste **entiere**, y compris les dossiers sans selection : c'est elle
    qui se compare a l'ordre de passage que l'application avait propose."""
    session_id, _ = _lot_de_deux(isolated_settings)

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "open_dossiers": "M1 M4 M7",
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "tier_1": "safe",
        },
    )

    ligne = db.query_one(
        "SELECT open_dossiers FROM sessions WHERE id = ?", (session_id,), settings=isolated_settings
    )
    assert ligne["open_dossiers"] == "M1 M4 M7"


def test_l_apercu_n_ecrit_toujours_rien(client: TestClient, isolated_settings: Settings) -> None:
    """La liste se memorise a l'import, pas a la lecture : l'apercu est une
    proposition, et une proposition n'ecrit pas."""
    session_id, _ = _lot_de_deux(isolated_settings)
    rendu = _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: [M1]")

    client.post(f"/history/{session_id}/picks/preview", data={"table": rendu})

    ligne = db.query_one(
        "SELECT open_dossiers FROM sessions WHERE id = ?", (session_id,), settings=isolated_settings
    )
    assert ligne["open_dossiers"] is None


def test_la_page_expose_les_overrides_par_session(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Un taux d'override eleve est un signal sur le modele, pas sur les
    matchs.** Il dit combien de fois l'analyse s'est notee comme si elle avait
    cherche, et il doit se voir la ou l'on relit une session."""
    session_id = _session(isolated_settings)
    _ecrasee(
        isolated_settings,
        session_id,
        "4",
        _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False),
    )

    page = client.get("/stats").text

    assert "Lecture forcée" in page
    assert "Dossiers non ouverts" in page


def test_un_defaut_de_collecte_n_accuse_pas_le_modele(migrated: Settings) -> None:
    """`Override` impute une **faute au modele** — « elle s'est notee comme si
    elle avait cherche ». L'imputer sur une ligne `dossiers_ouverts` jamais
    collee serait faux : on ignore alors si le dossier a ete ouvert, la question
    n'a pas ete transmise.

    Mesure du 14/08/2026 : 13 des 16 ecrasees de la base declaraient un niveau de
    source reel, et auraient toutes ete comptees comme de l'inflation.
    """
    session_id = _session(migrated)
    bloc = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    for cause in (OVERRIDE_LIGNE_ABSENTE, OVERRIDE_HORS_DOSSIERS):
        pick_id = add_pick(
            session_id,
            "safe",
            "1N2",
            "Lyon",
            confidence="4",
            claim=bloc,
            opened=False,
            override_cause=cause,
            settings=migrated,
        )
        set_result(pick_id, "win", migrated)

    override = analysis(settings=migrated).override

    assert override.total == 1, "seule celle qui dit quelque chose du modele"
    assert override.researched == 1, "et elle seule compte comme recherche absente"


def test_les_ecrasees_non_transmises_se_comptent_a_part(migrated: Settings) -> None:
    """**Sinon le compte dit l'inverse de ce qui s'est passe.**

    Sur la session du 14/08/2026 il valait 16 sur 16 et se lisait « l'analyse
    s'est notee seize fois comme si elle avait cherche » — alors que la ligne
    `dossiers_ouverts` n'avait jamais ete collee et qu'aucune de ces selections
    ne dit quoi que ce soit du modele. Un geste, pas un jugement.
    """
    session_id = _session(migrated)
    bloc = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    for cause in (OVERRIDE_LIGNE_ABSENTE, OVERRIDE_LIGNE_ABSENTE, OVERRIDE_HORS_DOSSIERS):
        add_pick(
            session_id,
            "safe",
            "1N2",
            "Lyon",
            confidence="4",
            claim=bloc,
            opened=False,
            override_cause=cause,
            settings=migrated,
        )

    ligne = next(
        row for row in analysis(settings=migrated).by_session if row.session_id == session_id
    )

    assert ligne.overridden == 1, "seule celle qui dit quelque chose du modele"
    assert ligne.override_faults == 2, "les deux autres mesurent une transmission"
    # Leur somme reste le total ecrase : deux comptes qui ne se recouvrent pas
    # et ne perdent personne.
    assert ligne.overridden + ligne.override_faults == 3
    assert ligne.override_line == "1 + 2 non transmise(s)"


def test_l_ordre_de_passage_se_lit_dans_le_prompt_archive(migrated: Settings) -> None:
    """La fiche recalculee aujourd'hui ne donnerait plus le meme classement
    qu'au moment de l'analyse : c'est le corps archive qui fait foi."""
    session_id, _ = _lot_de_deux(migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, "1. M1 Lyon – Nice  [tour ouvert]\n2. M2 Reims – Brest  [x]\n", db.utcnow()),
        settings=migrated,
    )

    assert prompt_priorities(migrated)[session_id] == {"M1", "M2"}


def test_un_dossier_hors_ordre_propose_est_compte_sans_etre_juge(
    migrated: Settings,
) -> None:
    """Un dossier ouvert hors priorite est **legitime** — la section F demande
    justement de le dire. C'est l'ecart systematique qui vaudrait d'etre su, et
    il se mesure sans rien decider dessus."""
    session_id, _ = _lot_de_deux(migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (session_id, "1. M1 Lyon – Nice  [tour ouvert]\n", db.utcnow()),
        settings=migrated,
    )
    set_open_dossiers(session_id, {"M1", "M2"}, migrated)

    ligne = next(
        row for row in analysis(settings=migrated).by_session if row.session_id == session_id
    )

    assert ligne.opened == 2
    assert ligne.on_priority == 1
    assert "dont 1 hors ordre proposé" in ligne.priority_line


# -- La declaration reste l'entree de la mesure -----------------------------


def test_un_dossier_ouvert_sans_fait_reste_une_lecture(migrated: Settings) -> None:
    """**La regle est a sens unique : la presence n'accorde rien.**

    Un dossier ouvert dont l'analyse ne tire aucun fait date **est** une lecture
    des blocs — c'est le resultat de la recherche, pas son absence, et il se
    note pareil. Sans cette moitie, ouvrir un dossier suffirait a se noter au
    dessus de la lecture sans avoir rien trouve.
    """
    session_id = _session(migrated)
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        confidence="3",
        source_level="2",
        claim=_bloc(source_level=2, faits=[], manque_touche_facteur=False),
        opened=True,
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT source_level, source_level_effective, confidence, confidence_computed, "
        "       research_overridden FROM picks",
        settings=migrated,
    )
    assert ligne["source_level"] == "2", "l'annonce reste intacte"
    assert ligne["source_level_effective"] == "lecture"
    assert ligne["confidence"] == 3
    assert ligne["confidence_computed"] == 1
    assert not ligne["research_overridden"], "le dossier a bien ete ouvert : rien n'est ecrase"


def test_un_dossier_ouvert_avec_un_fait_garde_son_niveau(migrated: Settings) -> None:
    """L'autre moitie de la regle : la presence n'accorde rien, mais elle
    n'enleve rien non plus a une declaration qui porte un fait date."""
    session_id = _session(migrated)
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        confidence="3",
        source_level="2",
        claim=_bloc(
            source_level=2,
            faits=[_fait("motherwellfc.co.uk")],
            manque_touche_facteur=False,
        ),
        opened=True,
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT source_level, source_level_effective, confidence_computed FROM picks",
        settings=migrated,
    )
    assert ligne["source_level"] == ligne["source_level_effective"] == "2"
    # Un fait dominant sans manque qui touche le facteur : la table donne 4.
    assert ligne["confidence_computed"] == 4


def test_une_saisie_a_la_main_n_est_jamais_ecrasee(migrated: Settings) -> None:
    """`opened` a trois etats, et `None` veut dire « on ne sait pas ».

    L'override juge une declaration de modele, pas un geste humain : un
    formulaire sans la question ne doit pas ramener la ligne en lecture.
    """
    session_id = _session(migrated)
    add_pick(session_id, "safe", "1N2", "Lyon", confidence="3", source_level="2", settings=migrated)

    ligne = db.query_one(
        "SELECT source_level, source_level_effective, research_overridden FROM picks",
        settings=migrated,
    )
    assert ligne["source_level"] == ligne["source_level_effective"] == "2"
    assert ligne["research_overridden"] is None


def test_une_ligne_illisible_ne_se_confond_pas_avec_une_ligne_absente() -> None:
    """**Deux defauts differents que le meme repli confondait.**

    Le modele qui omet la ligne et le lecteur qui echoue a la relire envoient
    tous deux le lot en lecture, mais l'un se reprend dans le gabarit et l'autre
    dans le lecteur — et leur somme se lirait comme un seul taux.
    """
    assert read_opened("dossiers_ouverts: [M1, M4]").state == OPEN_READ
    # **La liste vide porte son propre etat**, et c'etait la moitie du defaut :
    # elle rendait `lue`, donc se lisait comme une liste renseignee alors qu'elle
    # envoie tout le lot en lecture. C'est une declaration du modele — il n'a
    # rien ouvert — et non un collage manquant.
    assert read_opened("dossiers_ouverts: []").state == OPEN_EMPTY
    # La cle est la, sa valeur ne se relit pas : defaut de lecteur.
    assert read_opened("dossiers_ouverts: M1, M4").state == OPEN_MALFORMED
    assert read_opened("Rien de structure ici.").state == OPEN_ABSENT
    # Les quatre etats sont distincts deux a deux : c'est la propriete, et elle
    # ne tient pas si deux d'entre eux se rejoignent.
    etats = {
        read_opened(rendu).state
        for rendu in (
            "dossiers_ouverts: [M1, M4]",
            "dossiers_ouverts: []",
            "dossiers_ouverts: M1, M4",
            "Rien de structure ici.",
        )
    }
    assert len(etats) == 4

    # Les trois qui envoient le lot en lecture le disent differemment : le
    # message nomme le defaut, pas seulement son effet.
    assert not read_opened("dossiers_ouverts: M1").declared
    assert "ne se relit pas" in read_opened("dossiers_ouverts: M1").note
    assert "Aucune ligne" in read_opened("rien").note
    # Et la ligne vide dit qu'elle **a** ete lue : sans quoi on irait chercher un
    # collage rate qui n'existe pas.
    vide = read_opened("dossiers_ouverts: []")
    assert vide.declared, "la ligne etait bien la"
    assert "déclaration du modèle" in vide.note


def test_l_etat_de_la_ligne_est_journalise(migrated: Settings) -> None:
    """Sans lui, les deux defauts se comptent ensemble et rien ne les separe."""
    session_id = _session(migrated)
    set_open_dossiers(session_id, set(), migrated, state=OPEN_MALFORMED)

    ligne = db.query_one(
        "SELECT open_dossiers, open_dossiers_state FROM sessions WHERE id = ?",
        (session_id,),
        settings=migrated,
    )
    assert ligne["open_dossiers"] is None
    assert ligne["open_dossiers_state"] == OPEN_MALFORMED


def test_un_etat_hors_vocabulaire_vaut_non_renseigne(migrated: Settings) -> None:
    """Meme regle que l'angle et le niveau de source : une valeur inattendue
    vaut « on ne sait pas », jamais un refus ni un quatrieme etat en base."""
    session_id = _session(migrated)
    set_open_dossiers(session_id, set(), migrated, state="n'importe quoi")

    ligne = db.query_one(
        "SELECT open_dossiers_state FROM sessions WHERE id = ?", (session_id,), settings=migrated
    )
    assert ligne["open_dossiers_state"] is None


def test_une_recherche_qui_n_a_pas_eu_lieu_se_compte(migrated: Settings) -> None:
    """**Ce n'est pas un cran mal note, c'est une recherche qui n'a pas eu lieu.**

    Un fait cite avec son editeur suppose une page ouverte ; sur un dossier que
    l'analyse declare n'avoir pas ouvert, c'est cette page-la qui n'existe pas.
    Le compte se lit donc sur les faits declares et non sur le cran revendique —
    un cran 2 adosse a un fait invente compte ici et pas dans `fabricated`.
    """
    session_id = _session(migrated)
    # La premiere cite un editeur mais reste un cran 3 — un manque touche son
    # facteur porteur. La seconde ne cite rien. Aucune des deux n'est un cran
    # haut : c'est ce qui separe les deux compteurs.
    for faits in ([_fait("motherwellfc.co.uk")], []):
        pick_id = add_pick(
            session_id,
            "safe",
            "1N2",
            f"Lyon {len(faits)}",
            confidence="2",
            claim=_bloc(source_level=2, faits=faits, manque_touche_facteur=True),
            opened=False,
            independence_note="angles indépendants (fixture)",
            settings=migrated,
        )
        set_result(pick_id, "win", migrated)

    report = analysis(migrated)

    assert report.override.total == 2
    assert report.override.researched == 1, "seule celle qui cite un editeur"
    assert report.override.fabricated == 0, "aucun cran haut : les deux comptes different"
    assert "1 citent un éditeur sans que le dossier ait été ouvert" in report.override.line


# -- Le collage incomplet se voit a l'apercu --------------------------------


def test_un_collage_sans_bloc_le_dit_avant_l_import(migrated: Settings) -> None:
    """**La seule branche muette du module, et c'est celle qui a servi.**

    Un bloc pour trois lignes avertissait ; zero bloc ne disait rien, si bien
    qu'un collage ligne par ligne depuis le tableau de la section C passait sans
    un mot — les blocs etaient produits, jamais transmis. Trois selections sont
    entrees ainsi, et le defaut ne s'est vu qu'une semaine plus tard.
    """
    session_id = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    assert preview.count == 1
    assert preview.claims_attached == 0
    assert not preview.complete
    assert "1 sélection(s) détectée(s)" in preview.readout
    assert "0 bloc(s) de confiance apparié(s)" in preview.readout
    assert "ligne dossiers_ouverts absente" in preview.readout
    # Le message ne dit pas « complete les blocs » comme celui du compte : ce
    # n'est pas le rendu qui en manque, c'est le collage qui les a laisses.
    assert any("recolle la réponse entière" in note for note in preview.notes)


def test_un_collage_complet_ne_porte_aucun_avertissement(migrated: Settings) -> None:
    """L'autre moitie : le releve se lit en vert quand tout est la, sans quoi
    l'avertissement deviendrait un decor qu'on cesse de voir."""
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2") + "\ndossiers_ouverts: [M1, M2]\n"

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert preview.claims_attached == 2
    assert preview.complete
    assert "2 bloc(s) de confiance apparié(s)" in preview.readout
    assert "ligne dossiers_ouverts lue (2 dossier(s))" in preview.readout


def test_un_appariement_refuse_ne_compte_aucun_bloc(migrated: Settings) -> None:
    """Le releve compte les blocs **apparies**, pas les blocs presents.

    Un bloc dont le repere designe une affiche que le tableau ne porte pas fait
    tomber le lot, et le compte doit dire zero — sans quoi il annoncerait des
    crans que l'import n'ecrira pas.
    """
    session_id, _ = _lot_de_deux(migrated)
    rendu = _avec_blocs("M1", "M2").replace("Reims – Brest", "Reims")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert preview.claims_attached == 0
    assert not preview.complete


# -- Ou le bloc se trouve dans un collage -----------------------------------
#
# **Le rendu de Claude et ce qu'on en copie ne sont pas le meme texte.** Le
# module d'import le savait deja pour les tableaux — `_cells` lit les barres
# verticales *et* les tabulations — et ne l'appliquait pas aux blocs : la
# lecture ne se posait que sur la cloture, c'est-a-dire sur la forme que le
# copier-coller detruit. Mesure du 17/08/2026 : `claim_raw_json` NULL sur 235
# selections sur 235, dont les 86 des trois sessions ou le gabarit demandait
# pourtant un bloc par ligne.


def test_un_bloc_sans_cloture_est_lu_comme_un_bloc_cloture() -> None:
    """**La reparation du chantier.** Un bloc de code copie depuis le rendu
    arrive sans ses trois accents graves, exactement comme un tableau y arrive
    tabule plutot qu'a barres verticales."""
    nu = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)

    lecture = read_blocks(f"Voici mes sélections.\n\n{nu}\n\nEt la suite.")

    assert len(lecture.claims) == 1
    assert lecture.claims[0].rung == 4


def test_un_bloc_multiligne_et_indente_passe() -> None:
    """Le gabarit rend le JSON sur plusieurs lignes et l'indente. Un lecteur
    ligne par ligne echouerait precisement sur la forme demandee."""
    rendu = (
        '{"match": "M15", "confiance": 3, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "forfait d\'Osaka, Siniakova héritée d\'un bye",\n'
        '            "date": "2026-08-13", "editeur": "wtatennis.com", "niveau": 1}],\n'
        ' "manque_touche_facteur": true}'
    )

    lecture = read_blocks(rendu)

    assert [claim.match for claim in lecture.claims] == ["M15"]
    assert lecture.claims[0].rung == 3


def test_le_niveau_lecture_passe_comme_un_entier() -> None:
    """`source_level` vaut un entier **ou** la chaine `lecture`. Un schema type
    `int` rejetterait exactement les blocs les plus nombreux."""
    lecture = read_blocks(_bloc(source_level="lecture", faits=[]))

    assert len(lecture.claims) == 1
    assert lecture.claims[0].rung == 1


def test_une_liste_de_faits_vide_est_une_reponse_normale() -> None:
    assert not read_blocks(_bloc(source_level="lecture", faits=[])).rejects


def test_l_editeur_d_origine_est_facultatif() -> None:
    """Sa presence comme son absence doivent passer : le cas ordinaire est que
    l'editeur et l'origine se confondent."""
    sans = read_blocks(_bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False))
    avec_fait = {**_fait(), "editeur_origine": "wtatennis.com"}
    avec = read_blocks(_bloc(source_level=1, faits=[avec_fait], manque_touche_facteur=False))

    assert not sans.rejects and not avec.rejects
    assert avec.claims[0].facts[0].source == "wtatennis.com"


def test_les_accents_et_apostrophes_d_un_enonce_survivent() -> None:
    enonce = "forfait d'Osaka, Siniakova héritée d'un bye — confirmé"
    fait = {**_fait(), "enonce": enonce}

    lecture = read_blocks(_bloc(source_level=1, faits=[fait], manque_touche_facteur=False))

    assert lecture.claims[0].facts[0].statement == enonce


def test_un_repere_a_deux_chiffres_ne_se_confond_pas_avec_son_prefixe() -> None:
    """`M15` et `M1` : un rapprochement par prefixe attribuerait a l'un le bloc
    de l'autre, et le cran serait faux sans se voir."""
    rendu = _bloc(match="M1", source_level="lecture", faits=[])
    rendu += "\n" + _bloc(match="M15", source_level="lecture", faits=[])

    lecture = read_blocks(rendu)

    assert [claim.match for claim in lecture.claims] == ["M1", "M15"]


def test_un_combine_sans_cloture_n_est_pas_lu_comme_un_bloc_de_confiance() -> None:
    """Les deux familles portent `type` : c'est `jambes` qui tranche, et sans ce
    filtre un combine copie sans cloture se rendrait en bloc de confiance
    refuse — donc en cran perdu pour tout le lot."""
    combine = json.dumps({"type": "court", "jambes": ["M1", "M2"], "cote": 4.2})

    lecture = read_blocks(combine)

    assert not lecture.claims and not lecture.rejects


def test_une_accolade_dans_un_enonce_ne_derange_pas_la_lecture() -> None:
    """Le comptage se fait **hors chaines** : sans ce soin, un énoncé accidenté
    ferait avaler la moitié du rendu."""
    fait = {**_fait(), "enonce": "le retour de {le joueur} est acté"}
    rendu = _bloc(source_level=1, faits=[fait], manque_touche_facteur=False)

    lecture = read_blocks(rendu + "\n" + _bloc(match="M2", source_level="lecture", faits=[]))

    assert len(lecture.claims) == 2


def test_un_bloc_cloture_n_est_jamais_compte_deux_fois() -> None:
    bloc = _bloc(source_level="lecture", faits=[])

    assert len(read_blocks(f"```conf\n{bloc}\n```").claims) == 1


# -- Le recalcul applique-t-il vraiment la table ? ---------------------------
#
# **Le risque est qu'il recopie la valeur declaree par une voie detournee.**
# L'ecart declare/recalcule serait alors nul partout, la page annoncerait un
# accord parfait, et personne ne s'en apercevrait — exactement l'etat dans lequel
# les blocs `conf` sont restes du 13 au 17/08.
#
# Ces trois cas sont donc **volontairement mal notes**, et le calcul doit les
# corriger dans les trois sens : vers le bas de deux crans, de trois, et d'un.


@pytest.mark.parametrize(
    ("libelle", "champs", "annonce", "attendu"),
    [
        (
            "deux faits d'un même éditeur ne font qu'un facteur",
            {
                "source_level": 1,
                "faits": [_fait("lequipe.fr"), _fait("lequipe.fr", 2)],
                "manque_touche_facteur": False,
            },
            5,
            4,
        ),
        (
            "aucun fait daté est une lecture, quoi qu'on annonce",
            {"source_level": "lecture", "faits": []},
            4,
            1,
        ),
        (
            "un manque qui touche le facteur porteur plafonne à 3",
            {
                "source_level": 1,
                "faits": [_fait("lequipe.fr")],
                "manque_touche_facteur": True,
            },
            4,
            3,
        ),
    ],
)
def test_un_bloc_mal_note_est_corrige_par_la_table(
    libelle: str, champs: dict[str, object], annonce: int, attendu: int
) -> None:
    claim = parse(_bloc(confiance=annonce, **champs))

    assert claim.declared == annonce, "l'annonce est conservée telle quelle"
    assert claim.rung == attendu, libelle
    assert claim.disagrees, "et l'écart se déclare"


def test_le_cran_calcule_ne_recopie_jamais_l_annonce(migrated: Settings) -> None:
    """**Le parcours complet, jusqu'en base.** Un test sur l'objet seul
    laisserait passer un `add_pick` qui écrirait la valeur annoncée dans la
    colonne calculée — et c'est précisément la panne qu'on ne verrait pas."""
    session_id = _session(migrated)

    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        confidence="5",
        claim=_bloc(
            source_level=1,
            faits=[_fait("lequipe.fr"), _fait("lequipe.fr", 2)],
            manque_touche_facteur=False,
            confiance=5,
        ),
        settings=migrated,
    )

    ligne = db.query_one(
        "SELECT confidence, confidence_computed, distinct_publishers FROM picks",
        settings=migrated,
    )
    assert ligne["confidence"] == 5, "l'annonce reste écrite telle quelle"
    assert ligne["confidence_computed"] == 4, "et le calcul la corrige"
    assert ligne["distinct_publishers"] == 1


def test_le_repli_choisit_le_prompt_du_tableau_et_non_le_premier_qui_compte(
    migrated: Settings,
) -> None:
    """**« Il porte assez de repères » ne prouve rien, et ça a coûté une mesure
    fausse.**

    Une session porte plusieurs prompts — trois pour la session 17, un lot de
    football et deux de tennis — et `M1` y désigne trois matchs différents. Le
    repli qui retenait le premier prompt portant les repères déclarés choisissait
    donc n'importe lequel des trois, en pratique le plus récent.

    Dégât mesuré le 19/08/2026 sur l'import 14 : les affiches comparées étaient
    celles du lot de Cincinnati quand le tableau portait du football, aucune ne
    tombait, et les deux sélections sortaient en `hors_dossiers`. Le lot 8 les a
    lues comme **une observation sur le modèle**. C'était un défaut de collecte.

    Ici : deux prompts numérotent `M1`/`M2`, le second est celui du tableau. Sans
    la garde, le premier gagne et les deux sélections passent en `hors_dossiers`
    alors qu'elles portent sur des dossiers ouverts.
    """
    session_id, _ = _lot_de_deux(migrated)
    # Un prompt plus recent, d'un autre lot, qui porte les memes reperes.
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · Tennis · Cincinnati · Sinner – Alcaraz · 02/01 20:45\n"
            "### M2 · Tennis · Cincinnati · Zverev – Paul · 02/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )

    preview = picks_import.build_preview(
        session_id, TABLEAU_DEUX + "\ndossiers_ouverts: [M1, M2]\n", migrated
    )

    assert [pick.opened for pick in preview.picks] == [True, True], (
        "les repères se résolvent contre le prompt qui a produit ce tableau"
    )
    assert [pick.override_cause for pick in preview.picks] == ["", ""]


def test_un_tableau_qu_aucun_prompt_ne_porte_reste_en_lecture(migrated: Settings) -> None:
    """En cas de doute, rien — et « rien » vaut ici la lecture, cran 1.

    Un repli qui prendrait un prompt approchant poserait des crans calculés sur
    une résolution que personne ne peut refaire. Le défaut est `lecture`, et il
    se voit ; un niveau de source gonflé qui passe pour vérifié ne se voit pas.
    """
    session_id, _ = _lot_de_deux(migrated)
    db.execute("DELETE FROM prompts WHERE session_id = ?", (session_id,), settings=migrated)
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 'session_default.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · Tennis · Cincinnati · Sinner – Alcaraz · 02/01 20:45\n"
            "### M2 · Tennis · Cincinnati · Zverev – Paul · 02/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )

    preview = picks_import.build_preview(
        session_id, TABLEAU_DEUX + "\ndossiers_ouverts: [M1, M2]\n", migrated
    )

    assert [pick.opened for pick in preview.picks] == [False, False]
    assert [pick.override_cause for pick in preview.picks] == [
        OVERRIDE_REPERES,
        OVERRIDE_REPERES,
    ]
    assert is_collection_fault(OVERRIDE_REPERES), "un rattachement raté n'est pas une observation"


# --- Lot 14 : le collage du seul tableau ne passe plus sans confirmation ----


def _lot_qui_reclame(settings: Settings) -> int:
    """Un lot dont le prompt archive **demande** la ligne des dossiers ouverts.

    Sans cette demande, rien ne manque : le releve se tait plutot que d'accuser
    un collage d'apres un gabarit qu'on n'a pas.
    """
    session_id, corps = _lot_de_deux(settings)
    db.execute(
        "UPDATE prompts SET body = ? WHERE session_id = ?",
        (corps + "\ndossiers_ouverts: [M1, M2]\n", session_id),
        settings=settings,
    )
    return session_id


def _dernier_import(settings: Settings) -> str:
    return str(db.query_one("SELECT MAX(id) AS id FROM imports_raw", settings=settings)["id"])


def test_un_collage_du_seul_tableau_est_refuse_sans_confirmation(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le defaut mesure le 20/08/2026.** Sur 24 collages archives, 20 sont un
    collage du seul tableau de la section C ; l'avertissement d'apercu existait
    depuis le 17/08 et il a parle les 20 fois. Les 20 imports ont ete valides
    quand meme, et 89 selections y ont perdu leur cran.
    """
    session_id = _lot_qui_reclame(isolated_settings)
    client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU_DEUX})

    reponse = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "tier_1": "safe",
            "import_id": _dernier_import(isolated_settings),
        },
    )

    assert reponse.status_code == 200
    assert "Import refusé" in reponse.text
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 0


def test_la_confirmation_explicite_laisse_passer(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le refus n'est pas absolu : il reclame un geste, sur un chemin qu'on veut
    rare. Un blocage sans issue ferait contourner l'outil."""
    session_id = _lot_qui_reclame(isolated_settings)
    client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU_DEUX})

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "tier_1": "safe",
            "import_id": _dernier_import(isolated_settings),
            "confirm_partial": "1",
        },
    )

    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 1


def test_un_collage_qui_porte_la_ligne_passe_sans_rien_cocher(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le test du test.** Un garde-fou qui crie sur tout passerait pour un
    garde-fou qui marche : celui-ci verifie le cas sain."""
    session_id = _lot_qui_reclame(isolated_settings)
    complet = _avec_dossiers(_avec_blocs("M1", "M2"), "dossiers_ouverts: [M1, M2]")
    client.post(f"/history/{session_id}/picks/preview", data={"table": complet})

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "1",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "tier_1": "safe",
            "import_id": _dernier_import(isolated_settings),
        },
    )

    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 1


def test_sans_identifiant_de_collage_on_ne_bloque_pas(
    client: TestClient, isolated_settings: Settings
) -> None:
    """La saisie a la main et le rejeu n'ont pas d'identifiant d'import :
    refuser sur ce qu'on n'a pas vu fermerait deux chemins pour en garder un."""
    session_id = _lot_qui_reclame(isolated_settings)

    client.post(
        f"/history/{session_id}/picks/import",
        data={"keep_1": "1", "market_1": "1N2", "selection_1": "Lyon", "tier_1": "safe"},
    )

    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 1


def test_l_apercu_emet_la_case_quand_la_ligne_manque(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le service et sa surface se livrent ensemble.** Un refus serveur sans
    case a cocher serait un blocage sans issue — le defaut exact du motif de
    saisie tardive, reste sans surface pendant deux jours."""
    session_id = _lot_qui_reclame(isolated_settings)

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU_DEUX})
    rendu = " ".join(apercu.text.split())

    assert 'name="confirm_partial"' in rendu
    assert "required" in rendu
