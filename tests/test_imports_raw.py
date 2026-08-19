"""Le collage brut, garde avant toute tentative de lecture.

**C'est la ligne la plus rentable du projet, et elle n'avait pas ete faite.** Le
chantier precedent a etabli que `picks.claim_raw_json` etait NULL sur 235
selections sur 235, que le texte colle n'etait conserve nulle part, et que le
rattrapage des 86 selections des sessions 11, 13 et 14 etait donc impossible.

La journalisation des rejets ne l'aurait pas evite : elle attrape ce qui
**leve**, pas ce qui passe et se trompe. La panne d'origine ne levait rien — la
lecture ne trouvait aucun bloc, faute de cloture, et se taisait. Une table de
rejets serait restee vide.

Ces tests verifient les trois proprietes qui rendent le **prochain** bug
rattrapable : le texte est garde meme quand la lecture echoue entierement, les
bornes redonnent le fragment d'origine, et le rejeu ne double rien.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from html import unescape

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.replay import main as replay_main
from myassistantbet.replay import replay
from myassistantbet.services import board as board_service
from myassistantbet.services import imports_raw, picks_import
from myassistantbet.services.manual import build, save

LOIN = "2099-01-01"

TABLEAU = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE | 4 |
| 2 | Nice – Adv Nice | 1N2 | Nice | 1.60 | 🟢 SAFE | 4 |
"""


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, nom: str) -> int:
    return save(
        build(
            "football",
            "Match amical",
            nom,
            f"Adv {nom}",
            LOIN,
            "20:45",
            f"{nom} 1.45",
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


# -- Le texte est gardé, quoi qu'il arrive à la lecture ----------------------


def test_un_collage_illisible_laisse_quand_meme_sa_ligne(migrated: Settings) -> None:
    """**Le critère d'acceptation du chantier.** Un collage dont le parsing
    échoue entièrement n'atteint jamais le formulaire d'import : si on attendait
    la validation pour le garder, il ne serait gardé nulle part. C'est
    précisément ce cas-là qu'on veut pouvoir rejouer."""
    session_id, _ = _lot(migrated, ["Lyon"])
    illisible = "Voici mon analyse, en prose, sans le moindre tableau."

    preview = picks_import.build_preview(session_id, illisible, migrated)

    assert preview.ignored, "le parsing échoue entièrement"
    assert preview.import_id is not None
    collage = imports_raw.get(preview.import_id, migrated)
    assert collage is not None
    assert collage.raw_text == illisible, "le texte tel quel, sans normalisation"
    assert collage.char_count == len(illisible)


def test_le_texte_est_garde_tel_quel_sans_normalisation(migrated: Settings) -> None:
    """Un `strip()` ou une conversion de fins de ligne rendrait les bornes
    fausses — et c'est justement le balisage abîmé qui intéresse au rejeu."""
    session_id, _ = _lot(migrated, ["Lyon"])
    brut = "\r\n  \tdes espaces insécables et des \r\n fins de ligne mêlées  \n"

    preview = picks_import.build_preview(session_id, brut, migrated)

    assert imports_raw.get(preview.import_id or 0, migrated).raw_text == brut  # type: ignore[union-attr]


def test_deux_collages_du_meme_texte_ne_font_qu_une_ligne(migrated: Settings) -> None:
    """**Contrairement aux rejets**, où deux tentatives identiques sont deux
    tentatives. Ici le texte est le même, et ce qu'on garde est de quoi rejouer —
    pas un compteur d'essais. L'aperçu puis l'import postent le même texte."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])

    premier = picks_import.build_preview(session_id, TABLEAU, migrated).import_id
    second = picks_import.build_preview(session_id, TABLEAU, migrated).import_id

    assert premier == second
    assert len(imports_raw.list_for_session(session_id, migrated)) == 1


def test_une_session_inconnue_ne_fait_pas_echouer_l_apercu(migrated: Settings) -> None:
    """**Garder le collage est un filet, jamais une condition.** Faire échouer
    un aperçu parce qu'on n'a pas pu garder son texte serait un remède pire que
    le mal qu'il prévient."""
    preview = picks_import.build_preview(9999, TABLEAU, migrated)

    assert preview.import_id is None
    assert preview.count == 2, "la lecture se fait quand même"


def test_l_apercu_n_ecrit_toujours_aucune_selection(client: TestClient, migrated: Settings) -> None:
    """Le contrat de longue date porte sur les **sélections**, pas sur le texte
    reçu, et la distinction est tout l'objet du chantier."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])

    client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})

    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"] == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM imports_raw", settings=migrated)["n"] == 1


# -- Les bornes redonnent le fragment ----------------------------------------


def test_les_bornes_d_une_selection_redonnent_son_fragment(migrated: Settings) -> None:
    """**Le second critère d'acceptation.** Sans ces bornes, corriger un lecteur
    obligerait à re-parser tout un collage et à rapprocher les résultats à la
    main."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    for pick in preview.picks:
        assert pick.start is not None and pick.end is not None
        assert TABLEAU[pick.start : pick.end] == (
            f"| {pick.index} | {pick.match_text} | {pick.market} | {pick.selection} "
            f"| {'1.45' if pick.index == 1 else '1.60'} | 🟢 SAFE | 4 |"
        )


def test_les_bornes_d_un_bloc_de_confiance_sont_distinctes_de_celles_de_sa_ligne(
    migrated: Settings,
) -> None:
    """Les deux arrivent d'endroits différents du texte : une seule paire de
    bornes ne pourrait pas porter les deux, et en choisir une ferait mentir
    l'autre."""
    session_id, _ = _lot(migrated, ["Lyon"])
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
    bloc = '{"match": "M1", "confiance": 1, "source_level": "lecture", "faits": []}'
    rendu = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
        f"\n```conf\n{bloc}\n```\n"
    )

    pick = picks_import.build_preview(session_id, rendu, migrated).picks[0]

    assert pick.claim is not None
    assert rendu[pick.claim.start : pick.claim.end] == bloc
    assert pick.start != pick.claim.start


_HIDDEN = re.compile(r'name="([a-z_0-9]+)"\s+value="([^"]*)"')


def _hidden(html: str) -> dict[str, str]:
    """Les champs cachés du formulaire, tels que le navigateur les renverrait.

    Passer par le rendu réel plutôt que par l'objet : c'est le **transport** qui
    est testé, et un champ oublié côté gabarit ne se verrait pas autrement.
    """
    return {name: unescape(value) for name, value in _HIDDEN.findall(html)}


def test_les_bornes_arrivent_en_base(client: TestClient, migrated: Settings) -> None:
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    apercu = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})
    champs = _hidden(apercu.text)
    import_id, offsets = champs["import_id"], champs["offsets_1"]

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "rejects": "[]",
            "import_id": import_id,
            "keep_1": "1",
            "offsets_1": offsets,
            "event_1": str(events[0]),
            "tier_1": "safe",
            "market_1": "1N2",
            "selection_1": "Lyon",
            "price_1": "1.45",
        },
    )

    ligne = db.query_one("SELECT import_id, offset_start, offset_end FROM picks", settings=migrated)
    collage = imports_raw.get(int(ligne["import_id"]), migrated)
    assert collage is not None
    fragment = collage.fragment(ligne["offset_start"], ligne["offset_end"])
    assert "1N2" in fragment and "Lyon" in fragment


# -- Le rejeu ----------------------------------------------------------------


def test_le_rejeu_simule_par_defaut(migrated: Settings) -> None:
    """**Écrire d'office ferait d'un outil de diagnostic un outil de risque**, sur
    des données dont le projet entier dit qu'elles ne se reconstituent pas."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    import_id = picks_import.build_preview(session_id, TABLEAU, migrated).import_id

    report = replay(int(import_id or 0), settings=migrated)

    assert report.dry_run
    assert len(report.fresh) == 2
    assert report.written == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"] == 0
    assert "SIMULATION" in report.lines[0]


def test_le_rejeu_ecrit_sur_option_puis_ne_double_rien(migrated: Settings) -> None:
    """**C'est l'outil qui aurait sauvé les 86 sélections.** Le second passage ne
    double rien : une ligne dont la signature existe déjà est marquée en
    doublon, et le rejeu ne garde que ce qui manque."""
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    import_id = int(picks_import.build_preview(session_id, TABLEAU, migrated).import_id or 0)

    premier = replay(import_id, write=True, settings=migrated)
    second = replay(import_id, write=True, settings=migrated)

    assert premier.written == 2
    assert second.written == 0
    assert len(second.known) == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"] == 2


def test_le_rejeu_rattrape_ce_qu_un_lecteur_corrige_sait_lire(migrated: Settings) -> None:
    """**La panne d'origine, rejouée.** Un bloc de confiance sans clôture était
    perdu sans un mot ; le lecteur corrigé le lit, et le rejeu du collage
    conservé fait entrer le cran qui manquait."""
    session_id, _ = _lot(migrated, ["Lyon"])
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
    rendu = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
        '\n{"match": "M1", "confiance": 1, "source_level": "lecture", "faits": []}\n'
    )
    import_id = int(picks_import.build_preview(session_id, rendu, migrated).import_id or 0)

    report = replay(import_id, write=True, settings=migrated)

    assert report.written == 1
    ligne = db.query_one("SELECT claim_raw_json, confidence_computed FROM picks", settings=migrated)
    assert ligne["claim_raw_json"] is not None, "le bloc sans clôture est lu"
    assert ligne["confidence_computed"] == 1


def test_un_match_sorti_de_la_fenetre_ne_se_rejoue_pas_en_doublon(
    migrated: Settings,
) -> None:
    """**La garde de doublon de l'aperçu se périme, et il a fallu la mesurer.**

    `parse_table` marque `duplicate` sur une signature qui inclut l'identifiant
    de match — lequel se résout par la shortlist et le voisinage, donc **cesse de
    se résoudre** dès que le match sort de la fenêtre. Relevé du 19/08/2026 sur
    les dix-neuf collages archivés : douze sélections déclarées « neuves »,
    **douze avec `event_id = None`, et douze déjà en base**. Un `--ecrire` naïf
    aurait inséré douze doublons orphelins.
    """
    session_id, events = _lot(migrated, ["Lyon", "Nice"])
    import_id = int(picks_import.build_preview(session_id, TABLEAU, migrated).import_id or 0)
    replay(import_id, write=True, settings=migrated)
    # Le match quitte le board **et** le voisinage : c'est ce que le temps fait
    # tout seul, et rien d'autre n'est simulé ici.
    for event_id in events:
        db.execute("DELETE FROM session_events WHERE event_id = ?", (event_id,), settings=migrated)
        db.execute(
            "UPDATE events SET commence_time = '2020-01-01T12:00:00Z' WHERE id = ?",
            (event_id,),
            settings=migrated,
        )

    second = replay(import_id, write=True, settings=migrated)

    assert second.written == 0, "une ligne non résolue n'est pas une ligne nouvelle"
    assert len(second.known) == 2
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"] == 2


def test_le_rattachement_pose_un_bloc_sans_creer_de_selection(migrated: Settings) -> None:
    """**Le geste que `replay` ne peut pas faire.** Les trois collages complets de
    la base ne rendent aucune sélection neuve — leurs lignes sont entrées par les
    re-collages du seul tableau qui les ont suivis. Ce qui leur manque n'est pas
    leur existence, c'est leur bloc de confiance, donc le cran calculé.
    """
    from myassistantbet.replay import attach

    session_id, _ = _lot(migrated, ["Lyon"])
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
    tableau = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
    )
    complet = (
        tableau + '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour daté", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
        "\ndossiers_ouverts: [M1]\n"
    )
    # La sélection entre **sans son bloc**, comme un re-collage du seul tableau.
    court = int(picks_import.build_preview(session_id, tableau, migrated).import_id or 0)
    replay(court, write=True, settings=migrated)
    avant = db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"]
    long = int(picks_import.build_preview(session_id, complet, migrated).import_id or 0)

    rapport = attach(long, write=True, settings=migrated)

    assert rapport.attached == 1 and rapport.already == 0
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=migrated)["n"] == avant, (
        "le rattachement ne crée aucune sélection"
    )
    ligne = db.query_one("SELECT claim_raw_json, confidence_computed FROM picks", settings=migrated)
    assert ligne["claim_raw_json"] is not None
    assert ligne["confidence_computed"] == 4
    session = db.query_one(
        "SELECT open_dossiers, open_dossiers_state FROM sessions WHERE id = ?",
        (session_id,),
        settings=migrated,
    )
    assert (session["open_dossiers"], session["open_dossiers_state"]) == ("M1", "renseignee")


def test_le_rattachement_n_ecrase_jamais_un_bloc_deja_pose(migrated: Settings) -> None:
    """Le premier relevé fait foi : ce chemin répare un collage dont les blocs se
    sont perdus, il ne corrige pas une déclaration."""
    from myassistantbet.replay import attach

    session_id, _ = _lot(migrated, ["Lyon"])
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
    complet = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
        '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [{"enonce": "retour daté", "date": "2026-08-12",\n'
        '            "editeur": "lequipe.fr", "niveau": 1}],\n'
        ' "manque_touche_facteur": false}\n'
    )
    import_id = int(picks_import.build_preview(session_id, complet, migrated).import_id or 0)
    replay(import_id, write=True, settings=migrated)

    rapport = attach(import_id, write=True, settings=migrated)

    assert (rapport.attached, rapport.already) == (0, 1)


def test_un_bloc_qui_ne_designe_pas_une_selection_unique_est_dit(migrated: Settings) -> None:
    """**Poser un cran sur la mauvaise ligne serait le défaut que la somme de
    contrôle existe pour empêcher.** Zéro ou plusieurs correspondances : on le
    dit, on ne devine pas."""
    from myassistantbet.replay import attach

    session_id, _ = _lot(migrated, ["Lyon"])
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
    complet = (
        "| # | Match | Marché | Sélection | Cote | Palier |\n"
        "|---|---|---|---|---|---|\n"
        "| 1 | Lyon – Adv Lyon | 1N2 | Lyon | 1.45 | 🟢 SAFE |\n"
        '\n{"match": "M1", "confiance": 4, "type": "issue", "source_level": 1,\n'
        ' "faits": [], "manque_touche_facteur": false}\n'
    )
    import_id = int(picks_import.build_preview(session_id, complet, migrated).import_id or 0)

    rapport = attach(import_id, write=True, settings=migrated)

    assert rapport.attached == 0
    assert rapport.unmatched and "aucune sélection" in rapport.unmatched[0]


def test_le_rejeu_d_un_import_inconnu_le_dit(migrated: Settings) -> None:
    assert replay_main(["9999"]) == 1


def test_la_commande_liste_les_collages(
    migrated: Settings, capsys: pytest.CaptureFixture[str]
) -> None:
    session_id, _ = _lot(migrated, ["Lyon", "Nice"])
    picks_import.build_preview(session_id, TABLEAU, migrated)

    assert replay_main(["--lister", str(session_id)]) == 0
    assert "formulaire" in capsys.readouterr().out


# -- Non-régression des populations ------------------------------------------


def test_les_migrations_ne_deplacent_aucune_ligne_historique(
    isolated_settings: Settings,
) -> None:
    """**Règle de travail du lot** : les indicateurs historiques sont identiques
    avant et après chaque migration, hors changement explicitement demandé — et
    ça se vérifie par un test, pas à l'œil.

    Le lot est écrit **en SQL sous le schéma d'avant**, comme la base servie l'a
    été : `add_pick` est le code *courant*, et l'employer testerait la fixture au
    lieu de la migration.

    L'assertion ne compare pas deux appels d'`analysis()` : le lecteur est
    toujours le code courant, et il ne tourne pas sur un schéma antérieur. Elle
    compare les indicateurs à ce que les **lignes** impliquent, lues en SQL —
    c'est la seule forme qui ne se déplace pas avec le code.
    """
    from myassistantbet.services.history import analysis, populations

    from .helpers import migre_jusqu_a

    migre_jusqu_a(isolated_settings, 51)
    session_id, events = _lot(isolated_settings, ["Lyon", "Nice", "Reims"])
    # Deux à venir, une écrite après le coup d'envoi : c'est exactement la forme
    # de la base servie, 178 antérieures pour 52 tardives.
    for event_id, resultat, debut in zip(
        events,
        ("win", "loss", "win"),
        ("2099-01-01T20:45:00Z", "2099-01-01T20:45:00Z", "2000-01-01T00:00:00Z"),
        strict=True,
    ):
        db.execute(
            "UPDATE events SET commence_time = ? WHERE id = ?",
            (debut, event_id),
            settings=isolated_settings,
        )
        db.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, result, created_at) "
            "VALUES (?, ?, 'safe', '1N2', ?, 1.45, 4, 0, ?, ?)",
            (session_id, event_id, f"choix {event_id}", resultat, "2026-08-01T10:00:00Z"),
            settings=isolated_settings,
        )

    db.run_migrations(isolated_settings)
    report, compte = analysis(isolated_settings), populations(isolated_settings)

    # **La somme des trois populations vaut le total** : aucune ligne ne s'est
    # perdue entre elles, et c'est le seul témoin qui ne peut pas baisser.
    assert compte.consistent
    assert (compte.total, compte.main, compte.exploratory, compte.late) == (3, 2, 0, 1)
    # Et le rétro-remplissage suit **exactement** la dérivation d'origine.
    lignes = db.query(
        "SELECT k.tardive, k.created_at, e.commence_time FROM picks k "
        "JOIN events e ON e.id = k.event_id",
        settings=isolated_settings,
    )
    for ligne in lignes:
        attendu = str(ligne["created_at"]) >= str(ligne["commence_time"])
        assert bool(ligne["tardive"]) is attendu
    assert report.recorded == 3, "le témoin compte toutes les tranchées non exploratoires"
    assert report.consistent
