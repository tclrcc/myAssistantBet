"""Dater les changements de cadre, pour rendre leurs effets attribuables.

Trois lots ont modifie ce qui est produit et ce qui est mesure en une seule
journee. Sans ces dates, un mouvement du residu au prix vient du gabarit, de
l'ingestion, ou de rien — et rien ne permet de trancher.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.services import board as board_service
from myassistantbet.services import changelog
from myassistantbet.services.history import add_pick, set_result
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt, save_prompt, template_fingerprint


def _match(settings: Settings, home: str = "Lyon", away: str = "Nice") -> int:
    return save(
        build(
            "football",
            "Amical",
            home,
            away,
            "2099-01-01",
            "20:45",
            f"{home} 2.00\n{away} 2.00",
            "",
            "",
            settings=settings,
        ),
        settings,
    )


# -- Le journal --------------------------------------------------------------


def test_les_trois_lots_precedents_sont_dates(migrated: Settings) -> None:
    """Le seed est **rétroactif et sûr** : il se lit dans l'historique des commits.

    C'est la différence avec `price_source` (030) ou le cran calculé (042), qui
    auraient demandé de reconstituer une information jamais écrite. Dater ce qui
    est daté n'est pas inventer.
    """
    carnet = changelog.journal(migrated)

    assert not carnet.empty
    libelles = {entry.label for entry in carnet.entries}
    assert any("lot 1" in nom for nom in libelles)
    assert any("lot 2" in nom for nom in libelles)
    assert any("lot 3" in nom for nom in libelles)
    # Toutes les portées du seed appartiennent au vocabulaire : une valeur
    # inconnue produirait une ligne qu'aucune coupe ne verrait jamais.
    assert {entry.scope for entry in carnet.entries} <= set(changelog.SCOPES)


def test_deux_lots_du_meme_jour_ne_donnent_qu_un_point_de_coupe(migrated: Settings) -> None:
    """**Un fait sur le rythme de livraison, pas un défaut du journal.**

    Les lots 1 et 2 sont tous deux du 17/08 : ils ne se sépareront jamais par la
    date. Inventer une seconde date pour les distinguer ferait croire à un
    découpage qui ne découpe rien.
    """
    carnet = changelog.journal(migrated)

    du_17 = carnet.at("2026-08-17")
    assert len({entry.label for entry in du_17}) >= 3, "lots 1, 2 et 3 le même jour"
    assert carnet.days.count("2026-08-17") == 1


def test_les_dates_sont_decroissantes(migrated: Settings) -> None:
    """On relit un journal du plus récent, comme la feuille de session."""
    days = changelog.journal(migrated).days
    assert days == sorted(days, reverse=True)


def test_une_portee_hors_vocabulaire_est_refusee(migrated: Settings) -> None:
    """Rangée ailleurs, elle produirait une ligne qu'aucune coupe ne verrait.

    C'est la forme habituelle du défaut ici : rien ne casse, la ligne existe, et
    elle est invisible à l'endroit précis où elle devait servir.
    """
    with pytest.raises(ValueError, match="Portée inconnue"):
        changelog.add("2026-09-01", "essai", scope="prompt", settings=migrated)


def test_un_changement_sans_date_ou_sans_libelle_est_refuse(migrated: Settings) -> None:
    """Une ligne sans date ne coupe rien, une ligne sans libellé ne nomme rien."""
    with pytest.raises(ValueError):
        changelog.add("", "essai", settings=migrated)
    with pytest.raises(ValueError):
        changelog.add("2026-09-01", "  ", settings=migrated)


# -- L'empreinte du gabarit --------------------------------------------------


def test_l_empreinte_bouge_quand_un_gabarit_bouge(tmp_path: Path) -> None:
    """Elle répond à « le gabarit a-t-il changé », et à rien d'autre."""
    un = tmp_path / "a.md.j2"
    un.write_text("bonjour", encoding="utf-8")

    avant = changelog.fingerprint([un])
    un.write_text("bonjour,", encoding="utf-8")

    assert changelog.fingerprint([un]) != avant


def test_l_empreinte_couvre_le_nom_et_pas_seulement_le_contenu(tmp_path: Path) -> None:
    """**Deux gabarits dont on échangerait le contenu ne sont pas le même cadre.**

    Sans le nom dans l'empreinte, l'échange rendrait la même somme et le
    changement passerait inaperçu — le défaut caractéristique du projet appliqué
    à sa propre datation.
    """
    un, deux = tmp_path / "a.md.j2", tmp_path / "b.md.j2"
    un.write_text("premier", encoding="utf-8")
    deux.write_text("second", encoding="utf-8")
    avant = changelog.fingerprint([un, deux])

    un.write_text("second", encoding="utf-8")
    deux.write_text("premier", encoding="utf-8")

    assert changelog.fingerprint([un, deux]) != avant


def test_l_empreinte_ne_depend_pas_de_l_ordre(tmp_path: Path) -> None:
    """`glob` ne garantit aucun ordre : sans tri, l'empreinte varierait seule."""
    un, deux = tmp_path / "a.md.j2", tmp_path / "b.md.j2"
    un.write_text("premier", encoding="utf-8")
    deux.write_text("second", encoding="utf-8")

    assert changelog.fingerprint([un, deux]) == changelog.fingerprint([deux, un])


def test_le_cadre_est_fige_au_premier_prompt(migrated: Settings) -> None:
    """`COALESCE`, comme `scale_version`, et pour la même raison.

    Changer de gabarit en cours de session ne doit pas réétiqueter ce qui a déjà
    été rendu sous l'ancien.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    save_prompt(session_id, build_prompt(session_id, settings=migrated), settings=migrated)

    ligne = db.query(
        "SELECT gabarit_version, gabarit_sha FROM sessions WHERE id = ?",
        (session_id,),
        settings=migrated,
    )[0]
    assert ligne["gabarit_version"] == changelog.FRAME_VERSION
    assert ligne["gabarit_sha"] == template_fingerprint()

    # Un second prompt sous un cadre annoncé différent ne réécrit rien.
    db.execute(
        "UPDATE sessions SET gabarit_version = 'lot-0' WHERE id = ?",
        (session_id,),
        settings=migrated,
    )
    save_prompt(session_id, build_prompt(session_id, settings=migrated), settings=migrated)
    fige = db.query(
        "SELECT gabarit_version FROM sessions WHERE id = ?", (session_id,), settings=migrated
    )[0]
    assert fige["gabarit_version"] == "lot-0"


def test_rien_n_est_retro_rempli(migrated: Settings) -> None:
    """Le gabarit d'hier n'existe nulle part : seul son rendu est archivé.

    Une empreinte reconstituée depuis le fichier courant dirait que les quinze
    sessions passées ont été rendues sous le gabarit d'aujourd'hui — faux, et
    lisible comme une mesure.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)

    ligne = db.query(
        "SELECT gabarit_version, gabarit_sha FROM sessions WHERE id = ?",
        (session_id,),
        settings=migrated,
    )[0]
    assert ligne["gabarit_version"] is None and ligne["gabarit_sha"] is None


# -- Le découpage ------------------------------------------------------------


def _selection(settings: Settings, session_id: int, event_id: int, jour: str, gagne: bool) -> None:
    pick_id = add_pick(
        session_id,
        tier="fun",
        market="O/U 2.5",
        selection=f"Over {jour}-{gagne}",
        event_id=str(event_id),
        price="2.00",
        independence_note="angles indépendants (fixture)",
        settings=settings,
    )
    set_result(pick_id, "win" if gagne else "loss", settings)
    db.execute("UPDATE picks SET created_at = ? WHERE id = ?", (jour, pick_id), settings=settings)


def test_le_decoupage_partage_la_population_autour_d_une_date(migrated: Settings) -> None:
    """Les deux moitiés portent chacune leur effectif et leur résidu.

    À la cote 2.00, chaque sélection paie 0,50 victoire : quatre victoires sur
    quatre valent +2,00 d'écart, zéro sur quatre valent −2,00. Les nombres sont
    choisis pour se vérifier de tête.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    for index in range(4):
        _selection(migrated, session_id, event_id, f"2026-08-1{index}T12:00:00Z", gagne=False)
    for index in range(4):
        _selection(migrated, session_id, event_id, f"2026-08-2{index}T12:00:00Z", gagne=True)

    coupe = changelog.split("2026-08-17", migrated)

    assert coupe.before.settled == 4 and coupe.before.won == 0
    assert coupe.after.settled == 4 and coupe.after.won == 4
    assert coupe.before.gap == -2.0 and coupe.after.gap == 2.0
    # L'écart se lit **par sélection** : deux moitiés d'effectifs différents ne
    # se comparent pas par leur écart brut.
    assert coupe.before.per_selection == -0.5 and coupe.after.per_selection == 0.5
    assert coupe.shift == 1.0
    assert coupe.readable


def test_le_decoupage_ignore_les_deux_autres_populations(migrated: Settings) -> None:
    """**La règle du projet, et elle ne tombe pas ici.**

    Mêler l'exploratoire ou la tardive détruirait les comparaisons que ces
    populations existent pour rendre possibles — fait daté contre lecture, prix
    d'avant-match contre prix écrit en connaissant le début du match.
    """
    event_id = _match(migrated)
    autre = _match(migrated, "Lens", "Reims")
    session_id = board_service.toggle_selection(event_id, True, migrated)
    board_service.toggle_selection(autre, True, migrated)
    _selection(migrated, session_id, event_id, "2026-08-20T12:00:00Z", gagne=True)

    exploratoire = add_pick(
        session_id,
        tier="giga_fun",
        market="1N2",
        selection="Lens",
        event_id=str(autre),
        price="9.00",
        exploratory=True,
        settings=migrated,
    )
    set_result(exploratoire, "win", migrated)
    db.execute(
        "UPDATE picks SET created_at = '2026-08-20T12:00:00Z' WHERE id = ?",
        (exploratoire,),
        settings=migrated,
    )

    coupe = changelog.split("2026-08-17", migrated)

    assert coupe.after.settled == 1, "seule la population principale entre dans la coupe"


def test_un_decoupage_a_sens_unique_ne_compare_rien(migrated: Settings) -> None:
    """Rendre l'écart de la moitié pleine ferait passer une population pour une
    différence."""
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _selection(migrated, session_id, event_id, "2026-08-20T12:00:00Z", gagne=True)

    coupe = changelog.split("2026-08-17", migrated)

    assert not coupe.readable
    assert coupe.shift is None
    assert "rien à comparer" in coupe.line


def test_la_coupe_porte_sur_la_date_de_decision(migrated: Settings) -> None:
    """Et non sur celle du match.

    Un changement de gabarit agit sur ce qui est **écrit** ce jour-là, quelle
    que soit la date du coup d'envoi. Même règle que l'étalement de
    `feedback()`, qui compte lui aussi des journées d'analyse.
    """
    event_id = _match(migrated)
    session_id = board_service.toggle_selection(event_id, True, migrated)
    # Le match est au 01/01/2099 ; la décision au 10/08/2026, donc avant la coupe.
    _selection(migrated, session_id, event_id, "2026-08-10T12:00:00Z", gagne=True)

    coupe = changelog.split("2026-08-17", migrated)

    assert coupe.before.settled == 1 and coupe.after.settled == 0


def test_le_decoupage_ne_rend_aucun_seuil(migrated: Settings) -> None:
    """**Un outil de lecture, jamais un test.**

    Un `p` posé sur deux moitiés d'une base de cette taille se lirait comme un
    verdict, et la page a déjà mis huit lots à retirer les résidus non testés
    qui décoraient son détail chiffré.
    """
    coupe = changelog.split("2026-08-17", migrated)

    champs = set(vars(coupe)) | {nom for nom in dir(coupe) if not nom.startswith("_")}
    assert not {nom for nom in champs if "p_value" in nom or nom == "significant"}


# -- Non-régression ----------------------------------------------------------


def test_la_migration_ne_deplace_aucune_population(isolated_settings: Settings) -> None:
    """**Règle de travail n°6, vérifiée sous la forme que le lot 2 a retenue.**

    Le lot est écrit **en SQL sous le schéma d'avant**, comme la base servie l'a
    été : `add_pick` est le code *courant*, et l'employer testerait la fixture au
    lieu de la migration.

    L'assertion ne compare pas deux appels d'`analysis()` — le lecteur est
    toujours le code courant, et il ne tourne pas sur un schéma antérieur. Elle
    compare les indicateurs à ce que les **lignes** impliquent, lues en SQL.

    Vérifié en plus sur une copie de la base servie : `recorded` 230,
    principale 178, tardive 52, exploratoire 0, `consistent` vrai — identiques
    au relevé du lot 2, la migration n'ajoutant que deux colonnes nulles et une
    table sans lien avec `picks`.
    """
    from myassistantbet.services.history import analysis, populations

    from .helpers import migre_jusqu_a

    migre_jusqu_a(isolated_settings, 53)
    event_id = _match(isolated_settings)
    session_id = board_service.toggle_selection(event_id, True, isolated_settings)
    for resultat in ("win", "loss", "win"):
        db.execute(
            "INSERT INTO picks (session_id, event_id, tier, market, selection, price, "
            "                   confidence, played, result, created_at) "
            "VALUES (?, ?, 'safe', '1N2', ?, 2.00, 4, 0, ?, '2026-08-01T10:00:00Z')",
            (session_id, event_id, f"choix {resultat}-{event_id}", resultat),
            settings=isolated_settings,
        )
    avant = {
        "recorded": db.query(
            "SELECT COUNT(*) AS n FROM picks WHERE result IN ('win','loss')",
            settings=isolated_settings,
        )[0]["n"]
    }

    db.run_migrations(isolated_settings)
    report, compte = analysis(isolated_settings), populations(isolated_settings)

    assert report.recorded == avant["recorded"] == 3
    assert compte.consistent
    assert (compte.total, compte.main, compte.exploratory, compte.late) == (3, 3, 0, 0)
    # Les deux colonnes ajoutées valent NULL sur l'existant : rien n'est
    # rétro-rempli, et une session d'hier ne s'attribue pas le cadre de demain.
    assert all(
        ligne["gabarit_version"] is None and ligne["gabarit_sha"] is None
        for ligne in db.query("SELECT * FROM sessions", settings=isolated_settings)
    )
