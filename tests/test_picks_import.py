"""Import des picks depuis le tableau de selections rendu par Claude."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient
from markupsafe import escape

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import history as history_service
from myassistantbet.services import picks_import

TABLEAU = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Angle | Ce qui la tue |
|---|-------|--------|-----------|------|--------|--------|-------|---------------|
| 1 | Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.55 | 🟢 SAFE | 4 | Service | Coude |
| 2 | Chan – Tirante | 2jrs 1 set | Oui | 2,35 | 🔵 FUN | 3 | Niveaux proches | Chan |
| 3 | Combiné du jour | Combiné | Hurkacz + Chan | 3.64 | 🟠 ULTRA FUN | 2 | Favoris | Un seul |
"""


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


def _match(settings: Settings, home: str, away: str, hour: str = "15") -> int:
    """Un match **a venir** : un match deja commence ferait decocher toutes les
    lignes de l'apercu, la selection posee apres le coup d'envoi reclamant son
    motif. C'est le cas d'un test dedie, pas celui de la lecture du tableau."""
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        f"VALUES (?, ?, ?, '2099-01-01T{hour}:00:00Z', 'oddsapi', ?)",
        (sport["id"], home, away, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _session(settings: Settings) -> tuple[int, int, int]:
    hurkacz = _match(settings, "Hubert Hurkacz", "Marcos Giron", "15")
    chan = _match(settings, "Duncan Chan", "Thiago Agustin Tirante", "16")
    session_id = 0
    for event_id in (hurkacz, chan):
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id, hurkacz, chan


# -- Lecture du tableau -----------------------------------------------------


def test_le_tableau_est_lu_en_entier(migrated: Settings) -> None:
    session_id, hurkacz, chan = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    assert preview.count == 3
    first, second, third = preview.picks
    assert (first.market, first.selection, first.price) == ("Vainqueur", "Hubert Hurkacz", "1.55")
    assert first.event_id == hurkacz
    assert first.confidence == "4"
    # La virgule decimale du bookmaker passe sans encombre.
    assert second.price == "2.35"
    assert second.event_id == chan
    # Un combine ne designe aucun match : c'est normal, pas une erreur bloquante.
    assert third.event_id is None


def test_les_paliers_sont_reconnus_par_emoji_et_par_libelle(migrated: Settings) -> None:
    """« 🟠 ULTRA FUN » ne doit pas etre lu comme « FUN »."""
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    assert [pick.tier for pick in preview.picks] == ["safe", "fun", "ultra_fun"]


def test_un_palier_sans_emoji_reste_reconnu(migrated: Settings) -> None:
    tiers = history_service.tiers(migrated)

    assert picks_import._resolve_tier("ULTRA FUN", tiers) == "ultra_fun"
    assert picks_import._resolve_tier("giga fun", tiers) == "giga_fun"
    assert picks_import._resolve_tier("", tiers) == ""
    assert picks_import._resolve_tier("palier maison", tiers) == ""


def test_les_colonnes_peuvent_etre_dans_le_desordre(migrated: Settings) -> None:
    """Seul l'entete fait foi : Claude peut reordonner ou ajouter des colonnes."""
    session_id, hurkacz, _ = _session(migrated)
    table = (
        "| Sélection | Palier | Match | Cote | Marché | Commentaire |\n"
        "|---|---|---|---|---|---|\n"
        "| Hubert Hurkacz | 🟢 SAFE | Hurkacz – Giron | 1.55 | Vainqueur | rien |\n"
    )

    preview = picks_import.build_preview(session_id, table, migrated)

    pick = preview.picks[0]
    assert (pick.market, pick.selection, pick.price, pick.tier) == (
        "Vainqueur",
        "Hubert Hurkacz",
        "1.55",
        "safe",
    )
    assert pick.event_id == hurkacz


def test_la_prose_autour_du_tableau_est_ignoree(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)
    table = (
        "### C. Tableau des sélections\n\nVoici mes choix du jour.\n\n"
        "| Match | Marché | Sélection | Cote | Palier |\n|---|---|---|---|---|\n"
        "| Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.55 | 🟢 SAFE |\n\n"
        "### D. Combinés\nRien à signaler.\n"
    )

    preview = picks_import.build_preview(session_id, table, migrated)

    assert preview.count == 1


def test_un_texte_sans_tableau_le_dit(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, "Pas de tableau ici.", migrated)

    assert preview.count == 0
    assert preview.ignored


def test_une_ligne_incomplete_est_conservee_avec_son_motif(migrated: Settings) -> None:
    """Ne rien jeter en silence : l'utilisateur corrige dans le formulaire."""
    session_id, _, _ = _session(migrated)
    table = (
        "| Match | Marché | Sélection | Cote | Palier |\n|---|---|---|---|---|\n"
        "| Hurkacz – Giron | Vainqueur |  | 1.55 | palier maison |\n"
    )

    preview = picks_import.build_preview(session_id, table, migrated)

    pick = preview.picks[0]
    assert pick.ready is False
    assert "sélection absente" in pick.problems
    assert "palier non reconnu (palier maison)" in pick.problems


def test_un_match_inconnu_n_est_pas_devine(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)
    table = (
        "| Match | Marché | Sélection | Cote | Palier |\n|---|---|---|---|---|\n"
        "| Alcaraz – Sinner | Vainqueur | Carlos Alcaraz | 1.55 | 🟢 SAFE |\n"
    )

    preview = picks_import.build_preview(session_id, table, migrated)

    assert preview.picks[0].event_id is None
    assert "match non rapproché" in preview.picks[0].problems


def test_la_confiance_tolere_les_formats(migrated: Settings) -> None:
    assert picks_import._confidence("4") == "4"
    assert picks_import._confidence("4/5") == "4"
    assert picks_import._confidence("⭐⭐⭐") == "3"
    assert picks_import._confidence("") == ""


# -- Routes -----------------------------------------------------------------


def test_l_apercu_n_ecrit_rien(client: TestClient, isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    session_id, _, _ = _session(isolated_settings)

    response = client.post(f"/history/{session_id}/picks/preview", data={"table": TABLEAU})

    assert response.status_code == 200
    assert "Rien n'est enregistré" in response.text
    assert history_service.list_picks(session_id, isolated_settings) == []


def test_seules_les_lignes_cochees_sont_importees(
    client: TestClient, isolated_settings: Settings
) -> None:
    db.run_migrations(isolated_settings)
    session_id, hurkacz, _ = _session(isolated_settings)

    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "safe",
            "market_1": "Vainqueur",
            "selection_1": "Hubert Hurkacz",
            "price_1": "1.55",
            "confidence_1": "4",
            "event_1": str(hurkacz),
            # Ligne 2 presente mais non cochee : elle ne doit pas etre creee.
            "tier_2": "fun",
            "market_2": "2jrs 1 set",
            "selection_2": "Oui",
        },
    )

    assert response.status_code == 200
    picks = history_service.list_picks(session_id, isolated_settings)
    assert len(picks) == 1
    assert picks[0].selection == "Hubert Hurkacz"


def test_l_import_n_enregistre_aucune_mise(client: TestClient, isolated_settings: Settings) -> None:
    """Aucun montant ne vient d'un tableau genere : la mise reste a l'utilisateur."""
    db.run_migrations(isolated_settings)
    session_id, hurkacz, _ = _session(isolated_settings)

    client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "safe",
            "market_1": "Vainqueur",
            "selection_1": "Hubert Hurkacz",
            "price_1": "1.55",
            "event_1": str(hurkacz),
        },
    )

    rows = db.query("SELECT stake FROM picks", settings=isolated_settings)
    assert [row["stake"] for row in rows] == [None]


def test_une_ligne_refusee_n_empeche_pas_les_autres(
    client: TestClient, isolated_settings: Settings
) -> None:
    db.run_migrations(isolated_settings)
    session_id, hurkacz, _ = _session(isolated_settings)

    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            "keep_1": "on",
            "tier_1": "palier_inexistant",
            "market_1": "Vainqueur",
            "selection_1": "Refusé",
            "keep_2": "on",
            "tier_2": "safe",
            "market_2": "Vainqueur",
            "selection_2": "Hubert Hurkacz",
            "event_2": str(hurkacz),
        },
    )

    assert response.status_code == 200
    picks = history_service.list_picks(session_id, isolated_settings)
    assert [pick.selection for pick in picks] == ["Hubert Hurkacz"]
    assert "Palier inconnu" in response.text


#: Ce que l'on obtient reellement en copiant un tableau depuis l'interface de
#: Claude : les barres verticales ont ete consommees par le rendu, il ne reste
#: que des tabulations.
TABLEAU_COLLE = (
    "C. Tableau des sélections\n"
    "#\tMatch\tMarché\tSélection\tCote\tPalier\tConf/5\tAngle\tCe qui la tue\n"
    "M1\tHurkacz – Giron\t2 jrs 1 set\tOui (3 sets)\t2.10\t🔵 FUN\t4\tRentrée\tIl sert bien\n"
    "M2\tChan – Tirante\tVainqueur\tTirante\t1.32\t🟢 SAFE\t3\tÉcart de niveau\tForfait récent\n"
)


def test_le_tableau_copie_depuis_l_interface_est_lu(migrated: Settings) -> None:
    """Le format tabule est celui du geste reel : il doit passer comme le Markdown."""
    session_id, hurkacz, chan = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU_COLLE, migrated)

    assert preview.count == 2
    assert [pick.event_id for pick in preview.picks] == [hurkacz, chan]
    assert [pick.tier for pick in preview.picks] == ["fun", "safe"]
    assert [pick.price for pick in preview.picks] == ["2.10", "1.32"]
    assert [pick.confidence for pick in preview.picks] == ["4", "3"]


def test_la_prose_tabulee_n_est_pas_prise_pour_un_tableau(migrated: Settings) -> None:
    """Deux tabulations au minimum : une phrase indentee n'est pas une ligne."""
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(
        session_id, "Analyse\tdu jour\nrien à signaler ici\n", migrated
    )

    assert preview.count == 0
    assert preview.ignored


def test_les_deux_formats_donnent_le_meme_resultat(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)
    markdown = (
        "| Match | Marché | Sélection | Cote | Palier |\n|---|---|---|---|---|\n"
        "| Hurkacz – Giron | 2 jrs 1 set | Oui | 2.10 | 🔵 FUN |\n"
    )
    tabule = (
        "Match\tMarché\tSélection\tCote\tPalier\nHurkacz – Giron\t2 jrs 1 set\tOui\t2.10\t🔵 FUN\n"
    )

    depuis_md = picks_import.build_preview(session_id, markdown, migrated).picks[0]
    depuis_tab = picks_import.build_preview(session_id, tabule, migrated).picks[0]

    assert (depuis_md.market, depuis_md.selection, depuis_md.price, depuis_md.tier) == (
        depuis_tab.market,
        depuis_tab.selection,
        depuis_tab.price,
        depuis_tab.tier,
    )


# -- Doublons ---------------------------------------------------------------

UNE_LIGNE = (
    "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
    "|---|---|---|---|---|---|---|\n"
    "| 1 | Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.55 | 🟢 SAFE | 4 |\n"
)


def test_une_selection_deja_dans_la_session_est_decochee(migrated: Settings) -> None:
    """Coller deux fois le meme rendu ne doit pas doubler l'historique."""
    session_id, hurkacz, _ = _session(migrated)
    history_service.add_pick(
        session_id, "safe", "Vainqueur", "Hubert Hurkacz", event_id=str(hurkacz), settings=migrated
    )

    preview = picks_import.build_preview(session_id, UNE_LIGNE, migrated)

    pick = preview.picks[0]
    assert pick.duplicate
    assert pick.ready, "elle reste enregistrable — c'est peut-etre voulu"
    assert not pick.keep, "mais elle n'est pas cochee d'office"
    assert "déjà présente" in pick.problems
    assert preview.duplicate_count == 1


def test_un_tableau_qui_se_repete_est_detecte(migrated: Settings) -> None:
    """Un rendu recopie deux fois se repete a l'interieur du meme collage."""
    session_id, _, _ = _session(migrated)
    repete = UNE_LIGNE + UNE_LIGNE.splitlines()[-1] + "\n"

    preview = picks_import.build_preview(session_id, repete, migrated)

    assert [pick.duplicate for pick in preview.picks] == [False, True]


def test_la_casse_et_les_accents_ne_creent_pas_de_faux_doublon(migrated: Settings) -> None:
    session_id, hurkacz, _ = _session(migrated)
    history_service.add_pick(
        session_id, "safe", "Hand. jeux", "Plíšková +5", event_id=str(hurkacz), settings=migrated
    )
    tableau = (
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Hurkacz – Giron | hand.  JEUX | PLISKOVA  +5 | 1.98 | 🔵 FUN | 3 |\n"
    )

    assert picks_import.build_preview(session_id, tableau, migrated).picks[0].duplicate


def test_la_meme_cote_sur_deux_affiches_n_est_pas_un_doublon(migrated: Settings) -> None:
    """Le match fait partie de l'identite d'une selection."""
    session_id, _, chan = _session(migrated)
    history_service.add_pick(
        session_id, "safe", "Vainqueur", "Hubert Hurkacz", event_id=str(chan), settings=migrated
    )

    assert not picks_import.build_preview(session_id, UNE_LIGNE, migrated).picks[0].duplicate


def test_une_ligne_neuve_reste_cochee(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)

    pick = picks_import.build_preview(session_id, UNE_LIGNE, migrated).picks[0]

    assert pick.keep and not pick.duplicate


# -- Le « pourquoi » : type d'angle et niveau de source ---------------------

TABLEAU_AVEC_POURQUOI = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Type | Source | Angle |
|---|-------|--------|-----------|------|--------|--------|------|--------|-------|
| 1 | Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.55 | 🟢 SAFE | 4 | issue | 2 | Service |
| 2 | Chan – Tirante | Jeux O/U | Over 21.5 | 2.35 | 🔵 FUN | 3 | manière (rythme) | lecture | TB |
"""


def test_le_pourquoi_est_lu_dans_le_tableau(migrated: Settings) -> None:
    """Les deux colonnes qui disent **sur quoi** la selection reposait.

    Toutes les autres dimensions sont des etiquettes de forme : un palier est
    une bande de cote, un marche un libelle. Celles-ci portent la seule question
    dont la reponse changerait la methode.
    """
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU_AVEC_POURQUOI, migrated)

    assert [(pick.angle, pick.source) for pick in preview.picks] == [
        ("issue", "2"),
        # « manière (rythme) » : le mot est cherche **dans** la cellule, le rendu
        # ajoutant volontiers une precision utile a la lecture.
        ("maniere", "lecture"),
    ]


def test_la_colonne_angle_de_prose_n_est_pas_le_type(migrated: Settings) -> None:
    """« Angle » nomme la description en une ligne, « Type » le mot a deux
    valeurs. Les confondre ferait entrer une phrase entiere dans le champ."""
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    assert all(pick.angle == "" for pick in preview.picks)
    assert all(pick.source == "" for pick in preview.picks)


def test_un_tableau_sans_le_pourquoi_reste_importable(migrated: Settings) -> None:
    """Cent selections en base n'en portent aucun : les exiger casserait
    l'import de tout rendu anterieur, et le manque se dit deja en statistiques."""
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    assert all(pick.ready for pick in preview.picks if pick.market and pick.selection)


def test_le_pourquoi_arrive_en_base_par_l_import(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, hurkacz, chan = _session(isolated_settings)
    preview = picks_import.build_preview(session_id, TABLEAU_AVEC_POURQUOI, isolated_settings)

    payload: dict[str, str] = {}
    for pick in preview.picks:
        payload |= {
            f"keep_{pick.index}": "1",
            f"event_{pick.index}": str(pick.event_id or ""),
            f"market_{pick.index}": pick.market,
            f"selection_{pick.index}": pick.selection,
            f"price_{pick.index}": pick.price,
            f"tier_{pick.index}": pick.tier,
            f"confidence_{pick.index}": pick.confidence,
            f"angle_{pick.index}": pick.angle,
            f"source_{pick.index}": pick.source,
        }
    client.post(f"/history/{session_id}/picks/import", data=payload)

    lignes = db.query(
        "SELECT angle, source_level FROM picks ORDER BY id", settings=isolated_settings
    )
    assert [(row["angle"], row["source_level"]) for row in lignes] == [
        ("issue", "2"),
        ("maniere", "lecture"),
    ]


def test_une_valeur_hors_vocabulaire_vaut_non_renseigne(migrated: Settings) -> None:
    """Refuser un import de vingt lignes pour un mot inattendu couterait plus
    que la ligne manquante. Le seul effet est une ligne de moins en statistiques."""
    session_id, hurkacz, _ = _session(migrated)

    pick_id = history_service.add_pick(
        session_id,
        "safe",
        "Vainqueur",
        "Hurkacz",
        event_id=str(hurkacz),
        angle="scénario",
        source_level="Twitter",
        settings=migrated,
    )

    row = db.query_one(
        "SELECT angle, source_level FROM picks WHERE id = ?",
        (pick_id,),
        settings=migrated,
    )
    assert (row["angle"], row["source_level"]) == (None, None)


# -- Une seconde selection sur le meme match, a l'import --------------------

TABLEAU_DEUX_FOIS = """### C. Tableau des sélections

| # | Match | Marché | Sélection | Cote | Palier | Conf/5 | Angle |
|---|-------|--------|-----------|------|--------|--------|-------|
| 1 | Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.55 | 🟢 SAFE | 4 | Service |
| 2 | Hurkacz – Giron | Jeux O/U | Over 21.5 | 2.10 | 🔵 FUN | 3 | Rythme |
"""


def test_la_seconde_ligne_d_un_meme_match_est_signalee(migrated: Settings) -> None:
    """Le prompt encadre le cas depuis toujours ; l'import ne le voyait pas.

    La ligne reste proposee — c'est peut-etre voulu — mais **decochee** tant que
    sa justification manque : `add_pick` la refuserait, et une ligne qui echoue
    a l'import se remarque moins qu'une case qu'on doit cocher.
    """
    session_id, _, _ = _session(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU_DEUX_FOIS, migrated)

    assert [pick.same_event for pick in preview.picks] == [False, True]
    assert preview.picks[0].keep and not preview.picks[1].keep
    assert "2e sélection sur ce match" in " ".join(preview.picks[1].problems)
    assert preview.same_event_count == 1


def test_un_match_deja_pris_en_base_compte_aussi(migrated: Settings) -> None:
    """Coller un second tableau ne remet pas le compteur a zero."""
    session_id, hurkacz, _ = _session(migrated)
    history_service.add_pick(
        session_id, "safe", "Vainqueur", "Hurkacz", event_id=str(hurkacz), settings=migrated
    )

    preview = picks_import.build_preview(session_id, TABLEAU, migrated)

    premiere = next(pick for pick in preview.picks if pick.event_id == hurkacz)
    assert premiere.same_event


def test_la_justification_saisie_recoche_la_ligne(migrated: Settings) -> None:
    session_id, _, _ = _session(migrated)
    preview = picks_import.build_preview(session_id, TABLEAU_DEUX_FOIS, migrated)

    preview.picks[1].independence = "l'un porte l'issue, l'autre le rythme"

    assert preview.picks[1].keep
    assert "2e sélection sur ce match" not in " ".join(preview.picks[1].problems)


def test_la_justification_arrive_en_base_par_l_import(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id, _, _ = _session(isolated_settings)
    preview = picks_import.build_preview(session_id, TABLEAU_DEUX_FOIS, isolated_settings)

    payload: dict[str, str] = {}
    for pick in preview.picks:
        payload |= {
            f"keep_{pick.index}": "1",
            f"event_{pick.index}": str(pick.event_id or ""),
            f"market_{pick.index}": pick.market,
            f"selection_{pick.index}": pick.selection,
            f"tier_{pick.index}": pick.tier,
        }
    payload["independence_2"] = "issue contre rythme"
    client.post(f"/history/{session_id}/picks/import", data=payload)

    notes = [
        row["independence_note"]
        for row in db.query(
            "SELECT independence_note FROM picks ORDER BY id", settings=isolated_settings
        )
    ]
    assert notes == [None, "issue contre rythme"]


def test_sans_justification_l_import_refuse_la_ligne(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Elle est **decochee** par defaut ; forcee, elle echoue et le dit."""
    session_id, _, _ = _session(isolated_settings)
    preview = picks_import.build_preview(session_id, TABLEAU_DEUX_FOIS, isolated_settings)

    payload: dict[str, str] = {}
    for pick in preview.picks:
        payload |= {
            f"keep_{pick.index}": "1",
            f"event_{pick.index}": str(pick.event_id or ""),
            f"market_{pick.index}": pick.market,
            f"selection_{pick.index}": pick.selection,
            f"tier_{pick.index}": pick.tier,
        }
    page = client.post(f"/history/{session_id}/picks/import", data=payload).text

    assert "angle réellement indépendant" in page
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 1, (
        "seule la première ligne entre"
    )


def test_l_import_lit_la_mention_de_cote_de_reference(migrated: Settings) -> None:
    """Le prompt impose « (ref.) » dans la colonne Cote des la premiere ligne du
    preambule, et la liste des prix a relever sous le tableau C la reprend. Elle
    etait ecrite, lue, puis jetee — alors que c'est elle qui dit qu'un palier
    repose sur un prix qu'on n'obtiendra pas.

    Une cote sans mention vient du bookmaker principal : c'est la regle du bloc,
    pas une supposition."""
    table = (
        "| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |\n"
        "|---|---|---|---|---|---|---|\n"
        "| 1 | Moutet – Bergs | Hand. jeux | Moutet -2.5 | 1.92 (ref.) | 🔵 FUN | 3 |\n"
        "| 2 | Moutet – Bergs | Vainqueur | Moutet | 1.85 | 🔵 FUN | 3 |\n"
    )

    preview = picks_import.build_preview(0, table, migrated)

    assert [pick.price_source for pick in preview.picks] == ["reference", "betclic"]
    assert [pick.price for pick in preview.picks] == ["1.92", "1.85"]


# -- Un match deja commence, voire fini -------------------------------------
#
# **La garde reclamait un motif qu'aucune surface n'offrait.** `add_pick`
# l'accepte depuis la migration 034 et `ParsedPick` le porte, mais ni le tableau
# d'import ni la saisie a la main ne proposaient les deux valeurs : le refus
# etait donc absolu, precisement sur le chemin qu'il devait laisser ouvert. Un
# lot de six lignes rendait « Rien d'importe » sans qu'aucun geste puisse le
# debloquer.

TABLEAU_COMMENCE = """| # | Match | Marché | Sélection | Cote | Palier | Conf/5 |
|---|-------|--------|-----------|------|--------|--------|
| 1 | Hurkacz – Giron | Vainqueur | Hubert Hurkacz | 1.65 (ref.) | 🟢 SAFE | 3 |
"""


def _match_fini(settings: Settings, home: str, away: str) -> int:
    """Un match dont le coup d'envoi est passe depuis longtemps.

    Il **reste dans la shortlist** — un match qui a commence quitte le board
    mais pas la session — donc `anchor` le retrouve et l'apercu le rapproche.
    """
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        "VALUES (?, ?, ?, '2020-01-01T15:00:00Z', 'oddsapi', ?)",
        (sport["id"], home, away, db.utcnow()),
        settings=settings,
    )
    return int(db.query_one("SELECT MAX(id) AS id FROM events", settings=settings)["id"])


def _session_finie(settings: Settings) -> int:
    event_id = _match_fini(settings, "Hubert Hurkacz", "Marcos Giron")
    return board_service.toggle_selection(event_id, True, settings)


def test_une_ligne_sur_un_match_commence_est_decochee_avec_son_motif(
    migrated: Settings,
) -> None:
    """Elle reste **proposee** : la decision est peut-etre anterieure, seule la
    saisie est tardive. Decochee, parce qu'une ligne qui echoue au milieu de
    vingt se remarque moins qu'une case qu'on doit cocher."""
    session_id = _session_finie(migrated)

    preview = picks_import.build_preview(session_id, TABLEAU_COMMENCE, migrated)

    assert preview.picks[0].started
    assert not preview.picks[0].keep
    assert "match déjà commencé" in " ".join(preview.picks[0].problems)


def test_le_motif_saisi_recoche_la_ligne(migrated: Settings) -> None:
    session_id = _session_finie(migrated)
    preview = picks_import.build_preview(session_id, TABLEAU_COMMENCE, migrated)

    preview.picks[0].late_reason = "differee"

    assert preview.picks[0].keep
    assert "match déjà commencé" not in " ".join(preview.picks[0].problems)


def test_l_apercu_offre_le_menu_des_deux_motifs(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Le contrôle manquant**, et le seul qui débloque le lot. Deux valeurs et
    pas de texte libre : leur mélange est ce qui a rendu inexploitables les 37
    sélections tardives de la base."""
    session_id = _session_finie(isolated_settings)

    page = client.post(
        f"/history/{session_id}/picks/preview", data={"table": TABLEAU_COMMENCE}
    ).text

    assert 'name="late_1"' in page
    # Par `escape` et non sur le texte brut : les libelles portent une
    # apostrophe (« coup d'envoi ») que Jinja rend en `&#39;`. Comparer les deux
    # textes bruts ferait echouer le test pour la mauvaise raison, et inviterait
    # a l'affaiblir plutot qu'a lire ce qu'il dit.
    for label in history_service.LATE_REASONS.values():
        assert str(escape(label)) in page


def test_le_motif_arrive_en_base_par_l_import(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Le bout qui manquait : la route ne transmettait pas le champ."""
    session_id = _session_finie(isolated_settings)
    preview = picks_import.build_preview(session_id, TABLEAU_COMMENCE, isolated_settings)
    pick = preview.picks[0]

    response = client.post(
        f"/history/{session_id}/picks/import",
        data={
            f"keep_{pick.index}": "1",
            f"event_{pick.index}": str(pick.event_id or ""),
            f"market_{pick.index}": pick.market,
            f"selection_{pick.index}": pick.selection,
            f"price_{pick.index}": pick.price,
            f"price_source_{pick.index}": pick.price_source,
            f"tier_{pick.index}": pick.tier,
            f"late_{pick.index}": "live",
        },
    )

    assert "Rien d'importé" not in response.text
    ligne = db.query_one(
        "SELECT late_reason, price, price_source FROM picks", settings=isolated_settings
    )
    assert ligne["late_reason"] == "live"
    # La cote de reference traverse l'import avec sa mention : c'est elle qui dit
    # que le palier repose sur un prix qu'on n'obtiendra pas.
    assert ligne["price"] == 1.65
    assert ligne["price_source"] == "reference"


def test_sans_motif_l_import_refuse_toujours_la_ligne(
    client: TestClient, isolated_settings: Settings
) -> None:
    """La garde reste une garde : le menu l'ouvre, il ne la leve pas."""
    session_id = _session_finie(isolated_settings)
    preview = picks_import.build_preview(session_id, TABLEAU_COMMENCE, isolated_settings)
    pick = preview.picks[0]

    page = client.post(
        f"/history/{session_id}/picks/import",
        data={
            f"keep_{pick.index}": "1",
            f"event_{pick.index}": str(pick.event_id or ""),
            f"market_{pick.index}": pick.market,
            f"selection_{pick.index}": pick.selection,
            f"tier_{pick.index}": pick.tier,
        },
    ).text

    assert "déjà commencé" in page
    assert db.query_one("SELECT COUNT(*) AS n FROM picks", settings=isolated_settings)["n"] == 0


def test_la_saisie_a_la_main_offre_le_meme_menu(
    client: TestClient, isolated_settings: Settings
) -> None:
    """Les deux chemins d'ecriture portent le meme vocabulaire : un motif
    disponible a l'import et absent du formulaire ferait chercher la difference
    dans la donnee plutot que dans la surface."""
    session_id = _session_finie(isolated_settings)

    page = client.get(f"/history/{session_id}").text

    assert 'name="late_reason"' in page
    for label in history_service.LATE_REASONS.values():
        assert str(escape(label)) in page


def test_le_motif_arrive_en_base_par_la_saisie_a_la_main(
    client: TestClient, isolated_settings: Settings
) -> None:
    session_id = _session_finie(isolated_settings)
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=isolated_settings)["id"]

    client.post(
        f"/history/{session_id}/picks",
        data={
            "tier": "safe",
            "market": "Vainqueur",
            "selection": "Hurkacz",
            "event_id": str(event_id),
            "late_reason": "differee",
        },
    )

    ligne = db.query_one("SELECT late_reason FROM picks", settings=isolated_settings)
    assert ligne["late_reason"] == "differee"


def test_le_motif_est_relu_sur_la_feuille_de_session(
    client: TestClient, isolated_settings: Settings
) -> None:
    """**Une donnee que rien ne lit finit par se retirer** — le sort exact de
    l'effectif collecte des mois sans lecteur. Celle-ci decide de la lecture du
    prix : une selection `live` porte une cote qui n'a jamais ete un prix
    d'avant-match, et aucune autre ligne de la feuille ne le dit."""
    session_id = _session_finie(isolated_settings)
    event_id = db.query_one("SELECT MAX(id) AS id FROM events", settings=isolated_settings)["id"]
    history_service.add_pick(
        session_id,
        "safe",
        "Vainqueur",
        "Hurkacz",
        event_id=str(event_id),
        late_reason="live",
        settings=isolated_settings,
    )

    page = client.get(f"/history/{session_id}").text

    assert str(escape(history_service.LATE_REASONS["live"])) in page
