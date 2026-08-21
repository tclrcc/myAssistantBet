"""Le second circuit : les selections produites sans fait date.

**Mesure du 17/08/2026, sur douze sessions** : 🔴 GIGA FUN et 💥 GIGA+ a zero
selection sur 12 sessions sur 12, 🟠 ULTRA FUN a 6 % du volume. Trois niveaux sur
cinq portent tout. Une echelle dont deux niveaux ne se declenchent jamais ne note
plus rien : ces bandes ne sont pas seulement inexploitees, elles sont **non
mesurables**.

L'exigence d'un fait date **n'est pas supprimee** — la retirer perdrait la
comparaison qui donne son sens a la page. Un second circuit s'ajoute a cote,
etiquete et compte a part, et ces tests verifient les deux moities : la
separation stricte des populations, et les deux refus propres a la section.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import ingestion, picks_import
from myassistantbet.services.history import (
    add_pick,
    analysis,
    compare_populations,
    exploratory,
    labelling,
    set_result,
)
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"

#: Un rendu complet : la section C, puis la section C-bis. Les deux tableaux
#: portent le meme en-tete — c'est le titre de section qui les separe, et c'est
#: lui qui remet la lecture de l'en-tete a zero.
RENDU = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |

### C-bis. Sélections exploratoires

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Nice – Adv Nice | 1N2 | Nice | 7.50 | 🔴 GIGA FUN | 1 |
"""


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str, cote: str = "1.45") -> int:
    return save(
        build(
            "football",
            "Match amical",
            nom,
            f"Adv {nom}",
            LOIN,
            "20:45",
            f"{nom} {cote}",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


def _lot(settings: Settings, noms: list[str]) -> tuple[int, list[int]]:
    session_id, events = 0, []
    for nom in noms:
        event_id = _match(settings, nom)
        events.append(event_id)
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, events


# -- La lecture de la section ------------------------------------------------


def test_la_section_c_bis_pose_le_drapeau_et_pas_la_section_c(migrated: Settings) -> None:
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])

    preview = picks_import.build_preview(session_id, RENDU, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]


def test_une_mention_de_c_bis_en_prose_ne_fait_pas_basculer_la_lecture(
    migrated: Settings,
) -> None:
    """**Le defaut du lot 8, et il a coute les deux seuls collages complets.**

    Le motif cherchait « C-bis » n'importe ou dans la ligne. Or la section B en
    parle — « il part en C-bis », « voir C-bis », et le gabarit lui-meme en ecrit
    une : la lecture basculait donc *avant* le tableau de la section C, dont
    toutes les lignes etaient alors refusees comme « exploratoires en palier
    sur ». Le collage complet rendait moins que le collage du seul tableau, ce
    qui est l'inverse exact de ce que le correctif d'interface visait.

    Mesure sur la base servie : 3 selections sur 5 perdues, puis 4 sur 5, et
    **aucun bloc de confiance rattache** — le compte de blocs ne tombait plus
    sur le compte de lignes.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    avec_prose = RENDU.replace(
        "### C. Tableau des sélections",
        "### B. Analyse par match\n\nAucun fait daté ne le porte, il part en C-bis.\n\n"
        "### C. Tableau des sélections",
    )

    preview = picks_import.build_preview(session_id, avec_prose, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]
    assert not [note for note in preview.notes if "palier sûr" in note]


def test_un_titre_de_section_referme_la_section_exploratoire(migrated: Settings) -> None:
    """Les titres `A.` a `F.` **ferment** C-bis autant que son titre l'ouvre.

    Sans eux le drapeau ne redescendait jamais : tout ce qui suit le second
    tableau restait exploratoire, et un rendu qui remettrait une table apres se
    lirait sous les regles du mauvais tableau.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    suite = RENDU + (
        "\n### D. Combinés\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|-------|--------|-----------|------|--------|--------|\n"
        "| 1 | Lyon – Adv Lyon | O/U 2.5 | Over 2.5 | 1.60 | 🟢 SAFE | 4 |\n"
    )

    preview = picks_import.build_preview(session_id, suite, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True, False]


def test_un_debut_de_phrase_francaise_n_est_pas_un_titre_de_section(
    migrated: Settings,
) -> None:
    """**C'est la lettre seule qui impose de lire la ligne brute.** Repliee,
    « C'est » commence par `c` suivi d'un espace, donc exactement comme
    « C. Tableau ». Le separateur distingue un titre d'un debut de phrase."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    avec_prose = RENDU.replace(
        "### C-bis. Sélections exploratoires",
        "C'est le seul endroit où l'exigence tombe.\n\n"
        "D'ailleurs, aucune autre section ne le permet.\n\n"
        "### C-bis. Sélections exploratoires",
    )

    preview = picks_import.build_preview(session_id, avec_prose, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]


def test_les_blocs_de_confiance_s_apparient_a_la_section_c_seule(
    migrated: Settings,
) -> None:
    """**Le gabarit rend les deux lectures legitimes**, et le modele les a prises
    toutes les deux.

    « Un bloc par ligne, dans l'ordre du tableau » est ecrit sous la section C,
    avant que C-bis existe. Mesure sur quatre collages reels : le 18/08 le modele
    a rendu 5 blocs pour 3 lignes de C et 2 de C-bis, le 19/08 5 blocs pour 5
    lignes de C et 2 de C-bis.

    Ce n'est **pas** retenir la lecture qui arrange : ce sont deux ensembles
    definis d'avance, chacun valide ou refuse **en entier** par la somme de
    controle sur l'affiche. Un ensemble mal choisi echoue sur ses paires.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )
    bloc = (
        '{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour date", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
    )
    rendu = RENDU + "\n" + bloc

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]
    assert preview.claims_attached == 1, (
        "un bloc pour la seule ligne de section C : l'appariement doit tomber "
        "sur cette population-la"
    )
    assert preview.picks[0].claim is not None
    assert preview.picks[1].claim is None, "la ligne C-bis n'a pas de bloc, et c'est normal"


def test_un_bloc_qui_ne_correspond_a_aucune_population_est_refuse(
    migrated: Settings,
) -> None:
    """La souplesse porte sur **quelles lignes**, jamais sur la somme de
    controle : un bloc dont l'affiche ne correspond a rien fait tomber le lot
    entier, comme avant."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )
    bloc = (
        '{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [], "manque_touche_facteur": false}\n'
    )
    ailleurs = RENDU.replace("Lyon – Adv Lyon", "Marseille – Adv Marseille")

    preview = picks_import.build_preview(session_id, ailleurs + "\n" + bloc, migrated)

    assert preview.claims_attached == 0
    assert any("ne correspondent à aucun prompt" in note for note in preview.notes)


def test_une_ligne_c_bis_en_palier_sur_est_acceptee(migrated: Settings) -> None:
    """**L'appartenance a C-bis se decide par la confiance, jamais par le prix.**

    Le refus d'origine — « ce tableau est réservé aux paliers hauts » — etait un
    rejet silencieux a l'ecriture : le cadre envoie **toute** confiance 2 en
    C-bis, sans exception, et une confiance 2 sur une cote sure n'avait alors
    aucun endroit ou etre ecrite. Refusee du tableau principal par le cadre,
    refusee de C-bis par l'application. La ligne disparaissait, et la sortie n'en
    portait aucune trace — meme famille que le defaut `EXPLORATORY_HEAD`.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    rendu = RENDU.replace("| 7.50 | 🔴 GIGA FUN | 1 |", "| 1.60 | 🟢 SAFE | 2 |")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]
    assert [pick.tier for pick in preview.picks] == ["safe", "safe"]
    motifs = {reject.block_type for reject in preview.rejects}
    assert ingestion.EXPLORATOIRE not in motifs, "aucun refus sur le palier"


def test_une_ligne_c_bis_sur_un_match_deja_pris_est_refusee(migrated: Settings) -> None:
    """« Une seule sélection par match, tous tableaux confondus » est une
    contrainte qui ne tombe pas."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    rendu = RENDU.replace("| 1 | Nice – Adv Nice", "| 1 | Lyon – Adv Lyon")

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False]
    motifs = {(reject.block_type, reject.reason) for reject in preview.rejects}
    assert (ingestion.EXPLORATOIRE, ingestion.DUPLICATE) in motifs


def test_trois_lignes_c_bis_produisent_trois_selections_exploratoires(
    client: TestClient, migrated: Settings
) -> None:
    """**Le critere d'acceptation du chantier.**"""
    session_id, events = _lot(migrated, ["Lyon", "Nice", "Reims", "Brest"])
    rendu = (
        "### C. Tableau des sélections\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |\n"
        "\n### C-bis. Sélections exploratoires\n\n"
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Nice – Adv Nice | 1N2 | Nice | 3.20 | 🟠 ULTRA FUN | 1 |\n"
        "| 2 | Reims – Adv Reims | 1N2 | Reims | 7.50 | 🔴 GIGA FUN | 1 |\n"
        "| 3 | Brest – Adv Brest | 1N2 | Brest | 22.00 | 💥 GIGA+ | 1 |\n"
    )

    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": rendu})
    assert apercu.status_code == 200
    donnees: dict[str, str] = {"rejects": "[]"}
    for index, (event_id, tier, price) in enumerate(
        zip(
            events,
            ("safe", "ultra_fun", "giga_fun", "giga_plus"),
            ("1.45", "3.20", "7.50", "22.00"),
            strict=True,
        ),
        start=1,
    ):
        donnees |= {
            f"keep_{index}": "1",
            f"event_{index}": str(event_id),
            f"tier_{index}": tier,
            f"market_{index}": "1N2",
            f"selection_{index}": f"choix {index}",
            f"price_{index}": price,
        }
        if index > 1:
            donnees[f"exploratory_{index}"] = "1"

    client.post(f"/history/{session_id}/picks/import", data=donnees)

    lignes = db.query("SELECT tier, exploratoire FROM picks ORDER BY id", settings=migrated)
    assert [int(row["exploratoire"]) for row in lignes] == [0, 1, 1, 1]


# -- La separation des populations -------------------------------------------


def test_une_selection_exploratoire_n_entre_dans_aucun_regroupement_existant(
    migrated: Settings,
) -> None:
    """**Melanger les deux detruirait la comparaison que cette section existe
    pour rendre possible.** C'est la propriete centrale du chantier."""
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    principal = add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="1.45",
        confidence="4",
        settings=migrated,
    )
    second = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        event_id=str(events[1]),
        price="7.50",
        confidence="1",
        exploratory=True,
        settings=migrated,
    )
    set_result(principal, "win", migrated)
    set_result(second, "loss", migrated)

    principale = analysis(migrated)

    assert principale.settled == 1, "la population principale ignore l'exploratoire"
    assert principale.recorded == 1, "y compris le témoin, sinon l'addition ne ferme plus"
    assert principale.consistent
    par_axe = {block.key: sum(row.count for row in block.rows) for block in labelling(migrated)}
    assert par_axe["tier"] == 1, "l'étiquetage décrit la même population"


def test_le_bloc_exploratoire_ne_mesure_que_sa_propre_population(
    migrated: Settings,
) -> None:
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    add_pick(
        session_id,
        "safe",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="1.45",
        settings=migrated,
    )
    second = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        event_id=str(events[1]),
        price="7.50",
        exploratory=True,
        settings=migrated,
    )
    set_result(second, "loss", migrated)

    lot = exploratory(migrated)

    assert (lot.settled, lot.won) == (1, 0)
    assert [row.key for row in lot.by_tier] == ["giga_fun"]


def test_la_comparaison_ne_se_rend_pas_sous_vingt_selections_de_chaque_cote() -> None:
    """**Elle opposerait deux nombres dont aucun ne veut rien dire.** C'est la
    faute exacte que la page a mis huit lots a cesser de commettre."""
    from myassistantbet.services.history import RateRow

    def _row(key: str, won: int, lost: int) -> RateRow:
        row = RateRow(key=key, label=key)
        row.won, row.lost = won, lost
        return row

    courte = compare_populations([_row("giga_fun", 5, 5)], [_row("giga_fun", 5, 5)])
    longue = compare_populations([_row("giga_fun", 15, 10)], [_row("giga_fun", 5, 20)])

    assert courte == []
    assert [cle for cle, _, _, _ in longue] == ["giga_fun"]


def test_la_page_avertit_que_le_taux_faible_est_attendu(
    client: TestClient, migrated: Settings
) -> None:
    """Sans cette phrase, le bloc se lirait comme un constat d'echec de la
    methode, alors qu'il mesure exactement ce qu'il annonce.

    **La phrase a change de fond, pas de forme.** Elle disait « produites sans
    fait date, par construction » ; mesure du 21/08/2026 sur les 32 lignes en
    base, **26 declarent un niveau de source numerique** et 5 des 7 blocs
    lisibles portent des faits. Ce qui definit la population est la **regle** —
    l'exigence levee — jamais son contenu, et la description d'origine etait
    fausse la ou elle etait verifiable.
    """
    session_id, events = _lot(migrated, ["Lyon"])
    pick_id = add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Lyon",
        event_id=str(events[0]),
        price="7.50",
        exploratory=True,
        settings=migrated,
    )
    set_result(pick_id, "loss", migrated)

    page = client.get("/stats")

    plat = " ".join(page.text.split())
    assert "sans <em>exigence</em> de fait daté" in plat, "la regle, jamais le contenu"
    assert "C'est la règle qui définit la population, pas son contenu" in plat
    assert "pas pour être bonne" in plat
    assert "produites sans fait daté, par construction" not in plat


def test_les_lignes_c_bis_portent_leur_propre_bloc_conf(migrated: Settings) -> None:
    """**Complétion de la spécification du lot 1**, décidée au lot 9.

    « Même tableau, mêmes colonnes, mêmes règles » ne tranchait pas ce point.
    Sans bloc du côté exploratoire, ces sélections n'ont aucun cran déclaré, et
    la comparaison « fait daté contre lecture » qui justifie l'existence de la
    section C-bis devient impossible d'un côté. Les deux populations doivent
    être symétriques.

    Le bloc d'une ligne C-bis est le plus souvent `source_level: "lecture"` avec
    `faits: []` — c'est attendu, et le gabarit le dit.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )
    rendu = RENDU + (
        '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour daté", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
        '\n{"match": "M2", "confiance": 1, "type": "issue", "source_level": "lecture",\n'
        ' "faits": [], "manque_touche_facteur": true}\n'
    )

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True]
    assert preview.claims_attached == 2, "les deux tableaux portent un bloc"
    assert preview.picks[1].claim is not None
    assert preview.picks[1].claim.source_level == "lecture"
    assert preview.picks[1].claim.facts == ()


def test_le_drapeau_exploratoire_ne_vient_jamais_du_bloc(migrated: Settings) -> None:
    """**Il se dérive de la sélection appariée, jamais d'un champ déclaré.**

    C'est déterministe — la ligne vient d'un tableau ou de l'autre — et ce que
    l'application peut trancher ne se délègue pas au modèle. Un bloc qui
    prétendrait le contraire ne doit rien changer.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )
    # Le bloc de la ligne C-bis se declare « exploratoire: false », celui de la
    # section C « exploratoire: true » : deux mensonges, aucun effet.
    rendu = RENDU + (
        '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "exploratoire": true, "faits": [], "manque_touche_facteur": false}\n'
        '\n{"match": "M2", "confiance": 1, "type": "issue", "source_level": "lecture",\n'
        ' "exploratoire": false, "faits": [], "manque_touche_facteur": true}\n'
    )

    preview = picks_import.build_preview(session_id, rendu, migrated)

    assert [pick.exploratory for pick in preview.picks] == [False, True], (
        "le drapeau suit le tableau d'origine, pas ce que le bloc raconte"
    )


def test_l_ecart_des_deux_crans_se_mesure_aussi_de_ce_cote(migrated: Settings) -> None:
    """**L'autre moitié du chantier du lot 9.**

    La phrase ajoutée au gabarit fait porter un bloc `conf` aux lignes de C-bis ;
    sans lecteur en face, ce cran ne se compare à rien et finirait par se
    retirer — le sort exact de l'effectif collecté des mois sans lecteur.

    Le bloc se compte comme celui de la population principale : **sur toutes les
    sélections portant les deux crans, tranchées ou non**, parce qu'il compare
    deux déclarations connues à l'import et non une issue. C'est ici la règle
    plutôt que l'exception — la première volée de blocs de C-bis est entière en
    attente.
    """
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    db.execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', ?, 0, ?)",
        (
            session_id,
            "### M1 · football · Amical · Lyon – Adv Lyon · 01/01 20:45\n"
            "### M2 · football · Amical · Nice – Adv Nice · 01/01 20:45\n",
            db.utcnow(),
        ),
        settings=migrated,
    )
    # La ligne C-bis annonce 1 et porte de quoi valoir 2 : un fait daté d'un
    # éditeur, sans manque qui touche le facteur.
    rendu = RENDU + (
        '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour daté", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
        '\n{"match": "M2", "confiance": 1, "type": "issue", "source_level": 3,\n'
        ' "faits": [{"enonce": "note de forum", "date": "2026-08-12",\n'
        '            "editeur": "forum.example", "niveau": 3}],\n'
        ' "manque_touche_facteur": false}\n'
        "\ndossiers_ouverts: [M1, M2]\n"
    )
    preview = picks_import.build_preview(session_id, rendu, migrated)
    for pick in preview.picks:
        add_pick(
            session_id,
            pick.tier,
            pick.market,
            pick.selection,
            price=pick.price,
            confidence=pick.confidence,
            source_level=pick.source,
            claim=pick.claim.raw if pick.claim else "",
            opened=pick.opened,
            exploratory=pick.exploratory,
            event_id=pick.event_id,
            settings=migrated,
        )

    principale = analysis(settings=migrated).notation
    exploratoire = exploratory(settings=migrated).notation

    assert (principale.comparable, exploratoire.comparable) == (1, 1), (
        "chaque population compte son propre écart"
    )
    assert exploratoire.transitions and exploratoire.transitions[0][:2] == (1, 2), (
        "le désaccord de C-bis est son passage à lui, pas celui de la section C"
    )
    assert principale.transitions != exploratoire.transitions, (
        "les fondre désignerait une clause moyenne qu'aucune des deux ne réclame"
    )


def test_le_desaccord_de_c_bis_est_rendu_avec_son_effectif(
    client: TestClient, migrated: Settings
) -> None:
    """Un écart sans son dénominateur se lit comme un fait — même règle que
    partout sur cette page."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        price="7.50",
        confidence="1",
        source_level="3",
        claim=(
            '{"match": "M2", "confiance": 1, "type": "issue", "source_level": 3,'
            ' "faits": [{"enonce": "note", "date": "2026-08-12",'
            ' "editeur": "forum.example", "niveau": 3}],'
            ' "manque_touche_facteur": false}'
        ),
        opened=True,
        exploratory=True,
        settings=migrated,
    )

    page = client.get("/stats")

    assert "Cran annoncé contre cran calculé, sur cette population" in page.text
    assert "1 sur 1 en désaccord" in page.text


def test_une_population_sans_tranchee_ne_fait_pas_tomber_la_page(
    client: TestClient, migrated: Settings
) -> None:
    """**Un résidu sans effectif n'est pas un résidu, et `gap` rend `None` pour
    le dire.** Le rendre sans garde faisait tomber `/stats` en 500 — pas une
    ligne absente, la page entière.

    C'est l'état exact dans lequel C-bis entre depuis que ses lignes portent un
    bloc : la première volée est toute en attente. Le défaut dormait derrière
    une population qui avait toujours eu au moins une sélection tranchée.

    Le même piège existait sur la population tardive, et la garde des bandes de
    retard — trois lignes plus bas dans le même gabarit — l'avait déjà résolu de
    son côté sans que les deux autres la reprennent.
    """
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    add_pick(
        session_id,
        "giga_fun",
        "1N2",
        "Nice",
        price="7.50",
        confidence="1",
        exploratory=True,
        settings=migrated,
    )
    # La tardive porte le meme piege, et il faut un vrai coup d'envoi passe pour
    # l'atteindre : le drapeau se derive de l'heure, pas du motif.
    db.execute(
        "UPDATE events SET commence_time = '2020-01-01T20:00:00Z' WHERE id = ?",
        (events[0],),
        settings=migrated,
    )
    add_pick(
        session_id,
        "fun",
        "1N2",
        "Lyon",
        price="2.00",
        confidence="3",
        event_id=str(events[0]),
        late_reason="differee",
        settings=migrated,
    )

    page = client.get("/stats")

    assert page.status_code == 200
    assert page.text.count("le résidu au prix ne se calcule pas encore") == 2
