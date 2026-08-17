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


def test_la_migration_ne_deplace_aucun_indicateur(isolated_settings: Settings) -> None:
    """**Règle de travail du lot** : les indicateurs historiques de la page sont
    identiques avant et après chaque migration, hors changement explicitement
    demandé — et ça se vérifie par un test, pas à l'œil.

    Le lot est monté sous le schéma d'avant (051), mesuré, puis la migration 052
    est appliquée et le lot remesuré. Une colonne ajoutée ne doit rien déplacer.
    """
    from myassistantbet.services.history import analysis

    from .helpers import migre_jusqu_a

    migre_jusqu_a(isolated_settings, 51)
    session_id, events = _lot(isolated_settings, ["Lyon", "Nice", "Reims"])
    # **Écrites en SQL, et c'est le point** : `add_pick` est le code *courant* et
    # remplit les colonnes de 052. Ce qu'on veut mesurer est le sort des lignes
    # **historiques**, écrites sous l'ancien schéma — celles de la base servie.
    for event_id, resultat in zip(events, ("win", "loss", "win"), strict=True):
        db.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, result, created_at) "
            "VALUES (?, ?, 'safe', '1N2', ?, 1.45, 4, 0, ?, ?)",
            (session_id, event_id, f"choix {event_id}", resultat, "2026-08-01T10:00:00Z"),
            settings=isolated_settings,
        )

    avant = analysis(isolated_settings)
    empreinte_avant = (
        avant.recorded,
        avant.settled,
        avant.without_antecedence,
        avant.consistent,
        [(row.key, row.won, row.settled) for row in avant.by_tier],
    )

    db.run_migrations(isolated_settings)
    apres = analysis(isolated_settings)

    assert (
        apres.recorded,
        apres.settled,
        apres.without_antecedence,
        apres.consistent,
        [(row.key, row.won, row.settled) for row in apres.by_tier],
    ) == empreinte_avant
