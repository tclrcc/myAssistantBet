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

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import picks_import
from myassistantbet.services.confidence import (
    Claim,
    ClaimError,
    Fact,
    parse,
    publisher_of,
    read_blocks,
)
from myassistantbet.services.history import add_pick, analysis, set_result

LOIN = "2099-01-01T20:45:00Z"


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


def test_un_nombre_de_blocs_different_ne_rattache_rien(migrated: Settings) -> None:
    """**Aligner sept blocs sur huit lignes decalerait les faits d'une selection
    a l'autre** : le cran serait faux et personne ne le verrait. Le desaccord se
    dit plutot que de se rattraper au jugé."""
    session_id = _session(migrated)
    deux_blocs = _rendu(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    deux_blocs += "```conf\n" + _bloc(source_level="lecture", faits=[]) + "\n```\n"

    preview = picks_import.build_preview(session_id, deux_blocs, migrated)

    assert preview.picks[0].claim is None
    assert any("bloc(s) de confiance" in note for note in preview.ignored)


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
        "       facts_json, source_level FROM picks",
        settings=migrated,
    )
    assert ligne["confidence"] == 4, "le cran annonce reste ecrit tel quel"
    assert ligne["confidence_computed"] == 5
    assert ligne["distinct_publishers"] == 2
    assert ligne["gap_touches_factor"] == 0
    assert "motherwellfc.co.uk" in ligne["facts_json"]
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


def test_des_blocs_dans_le_desordre_ne_rattachent_rien(migrated: Settings) -> None:
    """**Le defaut que le compte ne voyait pas.** Deux blocs pour deux lignes,
    mais inverses : sans somme de controle, chaque cran atterrissait sur l'autre
    selection sans qu'aucun compteur ne bouge."""
    session_id, _ = _lot_de_deux(migrated)

    preview = picks_import.build_preview(session_id, _avec_blocs("M2", "M1"), migrated)

    assert [pick.claim for pick in preview.picks] == [None, None]
    assert any("repères de match" in note for note in preview.ignored)


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
    assert any("repères de match" in note for note in preview.ignored)


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
    clause ambigue, qui se reecrit."""
    session_id = _session(migrated)
    quatre = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=False)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    for _ in range(3):
        _tranchee(migrated, session_id, "4", trois)  # annonce 4, table 3
    _tranchee(migrated, session_id, "4", quatre)  # accord

    notation = analysis(settings=migrated).notation

    assert notation.transitions == [(4, 3, 3)]
    assert notation.dominant == (4, 3, 3)
    assert "un 4 annoncé que la table met à 3" in notation.clause_line


def test_un_desaccord_disperse_ne_designe_aucune_clause(migrated: Settings) -> None:
    """Sous la moitie, le desaccord est du bruit de redaction : en nommer une
    clause quand meme ferait reecrire le gabarit sur rien."""
    session_id = _session(migrated)
    trois = _bloc(source_level=1, faits=[_fait()], manque_touche_facteur=True)
    lecture = _bloc(source_level="lecture", faits=[])
    _tranchee(migrated, session_id, "4", trois)  # 4 -> 3
    _tranchee(migrated, session_id, "3", lecture)  # 3 -> 1

    notation = analysis(settings=migrated).notation

    assert notation.disagreed == 2
    assert notation.dominant is None
    assert notation.clause_line == ""
