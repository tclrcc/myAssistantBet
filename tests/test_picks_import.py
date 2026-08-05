"""Import des picks depuis le tableau de selections rendu par Claude."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

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
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    db.execute(
        "INSERT INTO events (sport_id, home, away, commence_time, source, created_at) "
        f"VALUES (?, ?, ?, '2026-08-04T{hour}:00:00Z', 'oddsapi', ?)",
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
