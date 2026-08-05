"""Saisie groupee : relever un ecran de bookmaker en une soumission."""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.services import board as board_service
from myassistantbet.services import grid
from myassistantbet.services import session as session_service
from myassistantbet.services.manual import ManualError

from .helpers import NOW


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


def _shortlist(settings: Settings, *event_ids: int) -> int:
    session_id = 0
    for event_id in event_ids:
        session_id = board_service.toggle_selection(event_id, True, settings)
    return session_id


def _manual(settings: Settings, event_id: int) -> list[tuple[str, str, float]]:
    rows = db.query(
        "SELECT market_key, outcome_name, price FROM odds WHERE event_id = ? "
        "AND bookmaker = 'manual' ORDER BY outcome_name",
        (event_id,),
        settings=settings,
    )
    return [(row["market_key"], row["outcome_name"], row["price"]) for row in rows]


# -- Lecture des cellules ---------------------------------------------------


@pytest.mark.parametrize("raw,expected", [("2.13", 2.13), ("2,13", 2.13), (" 1.05 ", 1.05)])
def test_cote_lue(raw: str, expected: float) -> None:
    assert grid.parse_price(raw) == expected


@pytest.mark.parametrize("raw", ["", "   ", "abc", "1.00", "0.5", "2.13.4"])
def test_cellule_non_retenue(raw: str) -> None:
    assert grid.parse_price(raw) is None


def test_issues_separees_par_virgules() -> None:
    assert grid.parse_outcome_labels(" Oui , Non ") == ["Oui", "Non"]
    assert grid.parse_outcome_labels("") == []


# -- Nom de marche ----------------------------------------------------------


def test_marche_vide_retombe_sur_outright() -> None:
    assert grid.market_key("  ") == "outright"


def test_un_marche_d_api_ne_peut_pas_etre_usurpe() -> None:
    """Ni par sa cle, ni par son libelle : les deux tromperaient sur la source."""
    for name in ("h2h", "H2H", "Vainqueur", "totals", "1N2"):
        with pytest.raises(ManualError, match="servi par l'API"):
            grid.market_key(name)


def test_un_nom_trop_long_est_refuse() -> None:
    """Le prompt tronquerait en silence : mieux vaut le dire a la saisie."""
    with pytest.raises(ManualError, match="trop long"):
        grid.market_key("2 joueurs gagnent 1 set")

    assert grid.market_key("2jrs 1 set") == "2jrs 1 set"


# -- Ecriture ---------------------------------------------------------------


def test_une_soumission_couvre_tous_les_matchs(migrated: Settings) -> None:
    walton = _match(migrated, "Walton", "Brooksby", "15")
    chan = _match(migrated, "Chan", "Tirante", "16")
    session_id = _shortlist(migrated, walton, chan)

    result = grid.save_grid(
        session_id,
        "2jrs 1 set",
        ["Oui", "Non"],
        {f"{walton}:0": "2.15", f"{walton}:1": "1.52", f"{chan}:0": "2.35", f"{chan}:1": "1.44"},
        settings=migrated,
    )

    assert (result.written, result.events, result.rejected) == (4, 2, [])
    assert _manual(migrated, walton) == [
        ("2jrs 1 set", "Non", 1.52),
        ("2jrs 1 set", "Oui", 2.15),
    ]


def test_sans_libelles_les_participants_font_les_colonnes(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)

    grid.save_grid(
        session_id,
        "Live",
        [],
        {f"{event_id}:0": "2.14", f"{event_id}:1": "1.75"},
        settings=migrated,
    )

    assert _manual(migrated, event_id) == [
        ("Live", "Brooksby", 1.75),
        ("Live", "Walton", 2.14),
    ]


def test_les_cellules_vides_sont_ignorees(migrated: Settings) -> None:
    """Relever une page partiellement remplie ne doit rien effacer ailleurs."""
    walton = _match(migrated, "Walton", "Brooksby", "15")
    chan = _match(migrated, "Chan", "Tirante", "16")
    session_id = _shortlist(migrated, walton, chan)

    result = grid.save_grid(
        session_id, "2jrs 1 set", ["Oui", "Non"], {f"{walton}:0": "2.15"}, settings=migrated
    )

    assert (result.written, result.events) == (1, 1)
    assert _manual(migrated, chan) == []


def test_une_cellule_illisible_est_signalee_avec_son_match(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)

    result = grid.save_grid(
        session_id,
        "2jrs 1 set",
        ["Oui", "Non"],
        {f"{event_id}:0": "2.15", f"{event_id}:1": "1,5,2"},
        settings=migrated,
    )

    assert result.written == 1
    assert result.rejected == ["Walton – Brooksby · Non : « 1,5,2 »"]


def test_ressaisir_met_a_jour_sans_dupliquer(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)
    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {f"{event_id}:0": "2.15"}, settings=migrated)

    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {f"{event_id}:0": "2.25"}, settings=migrated)

    assert _manual(migrated, event_id) == [("2jrs 1 set", "Oui", 2.25)]


def test_une_saisie_n_efface_pas_un_autre_marche(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)
    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {f"{event_id}:0": "2.15"}, settings=migrated)

    grid.save_grid(session_id, "Tie-break", ["Oui"], {f"{event_id}:0": "3.10"}, settings=migrated)

    assert _manual(migrated, event_id) == [
        ("2jrs 1 set", "Oui", 2.15),
        ("Tie-break", "Oui", 3.10),
    ]


def test_le_mode_vidage_ne_touche_que_les_lignes_laissees_vides(migrated: Settings) -> None:
    walton = _match(migrated, "Walton", "Brooksby", "15")
    chan = _match(migrated, "Chan", "Tirante", "16")
    session_id = _shortlist(migrated, walton, chan)
    cells = {f"{walton}:0": "2.15", f"{chan}:0": "2.35"}
    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], cells, settings=migrated)

    result = grid.save_grid(
        session_id, "2jrs 1 set", ["Oui"], {f"{walton}:0": "2.20"}, replace=True, settings=migrated
    )

    assert result.removed == 1
    assert _manual(migrated, walton) == [("2jrs 1 set", "Oui", 2.20)]
    assert _manual(migrated, chan) == []


def test_une_grille_entierement_vide_est_refusee(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)

    with pytest.raises(ManualError, match="Aucune cote saisie"):
        grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {}, settings=migrated)


def test_une_shortlist_vide_est_refusee(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    with pytest.raises(ManualError, match="shortlist est vide"):
        grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {}, settings=migrated)


# -- Rendu ------------------------------------------------------------------


def test_le_prompt_nomme_les_deux_sources(migrated: Settings) -> None:
    """Une saisie qui complete un releve ne doit pas s'attribuer ses cotes."""
    event_id = _match(migrated, "Walton", "Brooksby")
    db.execute(
        "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, price, fetched_at) "
        "VALUES (?, 'betclic_fr', 'h2h', 'Walton', 2.12, '2026-08-04T12:08:00Z')",
        (event_id,),
        settings=migrated,
    )
    session_id = _shortlist(migrated, event_id)
    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {f"{event_id}:0": "2.15"}, settings=migrated)

    block = session_service.render_blocks(session_id, migrated, NOW)[0]

    # L'en-tete nomme la source principale et l'heure de son seul releve ;
    # la frappe se signale sur sa propre ligne, la ou elle est utilisable.
    assert "MARCHES (Betclic, releve" in block
    assert "[saisie manuelle]" not in block.splitlines()[1], "l'en-tete ne porte pas la frappe"
    # Le libelle garde son espace avant la valeur : 11 caracteres puis la colonne.
    assert "  2jrs 1 set  Oui 2.15  [saisie manuelle]" in block
    assert "  Vainqueur   2.12\n" in block + "\n", "la ligne relevee reste nue"


def test_une_saisie_seule_reste_sans_horodatage(migrated: Settings) -> None:
    event_id = _match(migrated, "Walton", "Brooksby")
    session_id = _shortlist(migrated, event_id)
    grid.save_grid(session_id, "2jrs 1 set", ["Oui"], {f"{event_id}:0": "2.15"}, settings=migrated)

    block = session_service.render_blocks(session_id, migrated, NOW)[0]

    assert "MARCHES (saisie manuelle)" in block
    assert "releve" not in block


# -- Route ------------------------------------------------------------------


def test_la_grille_s_affiche(client: TestClient, isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    event_id = _match(isolated_settings, "Walton", "Brooksby")
    session_id = _shortlist(isolated_settings, event_id)

    response = client.get(f"/session/{session_id}/odds")

    assert response.status_code == 200
    assert "Walton – Brooksby" in response.text
    assert f'name="price_{event_id}_0"' in response.text


def test_la_route_ecrit_la_grille(client: TestClient, isolated_settings: Settings) -> None:
    db.run_migrations(isolated_settings)
    event_id = _match(isolated_settings, "Walton", "Brooksby")
    session_id = _shortlist(isolated_settings, event_id)

    response = client.post(
        f"/session/{session_id}/odds",
        data={
            "market": "2jrs 1 set",
            "outcomes": "Oui, Non",
            f"price_{event_id}_0": "2.15",
            f"price_{event_id}_1": "1.52",
        },
    )

    assert response.status_code == 200
    assert _manual(isolated_settings, event_id) == [
        ("2jrs 1 set", "Non", 1.52),
        ("2jrs 1 set", "Oui", 2.15),
    ]


def test_la_route_conserve_la_saisie_quand_elle_refuse(
    client: TestClient, isolated_settings: Settings
) -> None:
    db.run_migrations(isolated_settings)
    event_id = _match(isolated_settings, "Walton", "Brooksby")
    session_id = _shortlist(isolated_settings, event_id)

    response = client.post(
        f"/session/{session_id}/odds",
        data={"market": "Vainqueur", "outcomes": "Oui", f"price_{event_id}_0": "2.15"},
    )

    assert response.status_code == 200
    assert "servi par l" in response.text
    assert 'value="Vainqueur"' in response.text
    assert _manual(isolated_settings, event_id) == []


# -- Collage depuis un bookmaker --------------------------------------------

#: Bloc tel qu'il se copie sur la page d'un bookmaker : entete de match,
#: compteur de paris, puis les issues et leurs cotes.
COLLE_TENNIS = """Montréal ATP • 1/64e
S. Baez 17:00 M. Bellucci
+66 paris
Oui
2,13
Non
1,54
Montréal ATP • 1/64e
A. Walton 17:00 J. Brooksby
+68 paris
Oui
2,15
Non
1,52"""


def _rows(settings: Settings, *pairs: tuple[str, str]) -> list[grid.GridRow]:
    session_id = 0
    for index, (home, away) in enumerate(pairs):
        session_id = board_service.toggle_selection(
            _match(settings, home, away, f"{15 + index:02d}"), True, settings
        )
    return grid.build_view(session_id, settings).rows


def test_le_collage_ancre_par_le_nom_pas_par_la_position(migrated: Settings) -> None:
    """L'ordre du bookmaker n'est pas celui de la grille : seul le nom fait foi."""
    rows = _rows(
        migrated, ("Adam Walton", "Jenson Brooksby"), ("Sebastian Baez", "Mattia Bellucci")
    )
    walton, baez = rows[0].event_id, rows[1].event_id

    result = grid.parse_paste(COLLE_TENNIS, rows, 2)

    # Baez est cite en premier dans le collage, second dans la grille.
    assert result.cells == {
        f"{baez}:0": "2.13",
        f"{baez}:1": "1.54",
        f"{walton}:0": "2.15",
        f"{walton}:1": "1.52",
    }
    assert result.missing == []


def test_le_collage_signale_les_matchs_absents(migrated: Settings) -> None:
    rows = _rows(
        migrated,
        ("Adam Walton", "Jenson Brooksby"),
        ("Sebastian Baez", "Mattia Bellucci"),
        ("Hubert Hurkacz", "Marcos Giron"),
    )

    result = grid.parse_paste(COLLE_TENNIS, rows, 2)

    assert result.missing == ["Hubert Hurkacz – Marcos Giron"]
    assert result.filled == 4


def test_le_prenom_abrege_suffit(migrated: Settings) -> None:
    """Un bookmaker abrege le prenom mais jamais le nom de famille."""
    rows = _rows(migrated, ("Sebastian Baez", "Mattia Bellucci"))

    assert grid.anchor("S. Baez 17:00 M. Bellucci", rows) is rows[0]


def test_le_bruit_de_la_page_n_ancre_rien(migrated: Settings) -> None:
    """« +66 paris » ne doit pas designer le Paris Saint-Germain."""
    rows = _rows(migrated, ("Paris Saint-Germain", "Lyon"))

    assert grid.anchor("+66 paris", rows) is None
    assert grid.anchor("Montréal ATP • 1/64e", rows) is None


def test_deux_matchs_trop_proches_ne_sont_pas_devines(migrated: Settings) -> None:
    """Deux homonymes : ne rien attribuer plutot que de tirer au sort."""
    rows = _rows(migrated, ("Alexander Zverev", "Casper Ruud"), ("Mischa Zverev", "Jan Lennard"))

    assert grid.anchor("Zverev 17:00 quelqu'un", rows) is None


def test_les_cotes_sans_match_identifiable_sont_comptees(migrated: Settings) -> None:
    rows = _rows(migrated, ("Adam Walton", "Jenson Brooksby"))

    result = grid.parse_paste("Oui\n2,13\nNon\n1,54", rows, 2)

    assert result.cells == {}
    assert result.orphan_prices == 2


def test_les_cotes_en_trop_ne_debordent_pas_sur_le_match_suivant(migrated: Settings) -> None:
    rows = _rows(migrated, ("Adam Walton", "Jenson Brooksby"))
    event_id = rows[0].event_id

    result = grid.parse_paste("A. Walton 17:00 J. Brooksby\n2,15\n1,52\n9,99", rows, 2)

    assert result.cells == {f"{event_id}:0": "2.15", f"{event_id}:1": "1.52"}
    assert result.orphan_prices == 1


def test_horaires_et_compteurs_ne_sont_pas_lus_comme_des_cotes(migrated: Settings) -> None:
    rows = _rows(migrated, ("Adam Walton", "Jenson Brooksby"))
    event_id = rows[0].event_id

    result = grid.parse_paste("A. Walton 17:00 J. Brooksby\n+68 paris\n1/64e\n18:10\n2,15", rows, 2)

    assert result.cells == {f"{event_id}:0": "2.15"}


def test_le_collage_n_ecrit_rien_en_base(client: TestClient, isolated_settings: Settings) -> None:
    """Le collage pre-remplit et rend la main : c'est l'utilisateur qui valide."""
    db.run_migrations(isolated_settings)
    event_id = _match(isolated_settings, "Adam Walton", "Jenson Brooksby")
    session_id = _shortlist(isolated_settings, event_id)

    response = client.post(
        f"/session/{session_id}/odds/paste",
        data={
            "market": "2jrs 1 set",
            "outcomes": "Oui, Non",
            "pasted": "A. Walton 17:00 J. Brooksby\nOui\n2,15\nNon\n1,52",
        },
    )

    assert response.status_code == 200
    assert 'value="2.15"' in response.text
    assert "Rien n'est enregistré" in response.text
    assert _manual(isolated_settings, event_id) == []
