"""Classements Elo tennis : lecture du rapport, rapprochement des noms, rendu.

Deux risques propres a cette source, chacun avec son test : attribuer a un
joueur le rating d'un autre — il n'existe aucune resolution manuelle pour le
rattraper — et laisser filer une conversion en probabilite, que la page source
propose pourtant noir sur blanc.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime, timedelta

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet.config import Settings
from myassistantbet.db import connect
from myassistantbet.main import app
from myassistantbet.providers.base import ProviderError
from myassistantbet.providers.tennisabstract import (
    BASE_URL,
    REPORTS,
    TennisAbstractClient,
    parse_elo_report,
)
from myassistantbet.services import board as board_service
from myassistantbet.services import competitions as competitions_service
from myassistantbet.services import elo
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import build_prompt
from myassistantbet.services.session import renderable_events

from .helpers import NOW

MOMENT = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def client(isolated_settings: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@pytest.fixture
def elo_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAbstractClient:
    return TennisAbstractClient(http_client, migrated)


def _mock_reports(load_text_fixture: Callable[[str], str], *, wta: bool = True) -> None:
    respx.get(f"{BASE_URL}{REPORTS['atp']}").mock(
        return_value=httpx.Response(200, text=load_text_fixture("tennisabstract_atp_elo.html"))
    )
    if wta:
        respx.get(f"{BASE_URL}{REPORTS['wta']}").mock(
            return_value=httpx.Response(200, text=load_text_fixture("tennisabstract_wta_elo.html"))
        )


# -- Lecture du rapport -----------------------------------------------------


def test_lecture_du_rapport(load_text_fixture: Callable[[str], str]) -> None:
    players = parse_elo_report(load_text_fixture("tennisabstract_atp_elo.html"))

    assert len(players) == 5, "le tableau d'introduction ne doit pas produire de joueur"
    sinner = players[0]
    assert sinner["player"] == "Jannik Sinner", "l'espace insecable est ramene a un espace"
    assert sinner["elo"] == 2331.9
    assert sinner["hard_elo"] == 2269.3
    assert sinner["clay_elo"] == 2221.8
    assert sinner["grass_elo"] == 2135.5
    assert sinner["peak_month"] == "2026-05"
    assert sinner["tour_rank"] == 1


def test_les_colonnes_sont_lues_par_libelle(load_text_fixture: Callable[[str], str]) -> None:
    """La colonne du classement officiel s'appelle « WTA Rank » chez les femmes."""
    players = parse_elo_report(load_text_fixture("tennisabstract_wta_elo.html"))

    assert players[0]["player"] == "Aryna Sabalenka"
    assert players[0]["tour_rank"] == 1


def test_un_tableau_sans_entete_est_refuse() -> None:
    with pytest.raises(ProviderError, match="en-tete"):
        parse_elo_report("<html><body><p>maintenance</p></body></html>")


def test_des_colonnes_inattendues_sont_refusees() -> None:
    """Mieux vaut une erreur qu'un tableau lu de travers."""
    html = "<table><thead><tr><th>Nom</th><th>Points</th></tr></thead><tbody></tbody></table>"

    with pytest.raises(ProviderError, match="colonnes inattendues"):
        parse_elo_report(html)


@respx.mock
async def test_appel_du_rapport(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    _mock_reports(load_text_fixture)

    players = await elo_client.elo_ratings("atp")

    assert len(players) == 5
    request = respx.calls.last.request
    assert "Mozilla" in request.headers["user-agent"], "sans navigateur declare, le site repond 403"


@respx.mock
async def test_aucun_credit_n_est_comptabilise(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    """La source est gratuite : `api_usage` ne compte que des credits consommes."""
    _mock_reports(load_text_fixture)

    await elo_client.elo_ratings("atp")

    with connect(migrated) as conn:
        assert conn.execute("SELECT COUNT(*) AS n FROM api_usage").fetchone()["n"] == 0


async def test_circuit_inconnu(elo_client: TennisAbstractClient) -> None:
    with pytest.raises(ProviderError, match="circuit inconnu"):
        await elo_client.elo_ratings("itf")


# -- Rafraichissement -------------------------------------------------------


@respx.mock
async def test_rafraichissement(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    _mock_reports(load_text_fixture)

    report = await elo.refresh(elo_client, migrated, now=MOMENT)

    assert report.ok
    assert report.counts == {"atp": 5, "wta": 2}
    assert elo.has_data("atp", migrated)


@respx.mock
async def test_un_classement_frais_n_est_pas_redemande(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    _mock_reports(load_text_fixture)
    await elo.refresh(elo_client, migrated, now=MOMENT)
    appels = len(respx.calls)

    second = await elo.refresh(elo_client, migrated, now=MOMENT + timedelta(hours=1))

    assert len(respx.calls) == appels, "les ratings sont hebdomadaires, inutile d'insister"
    assert second.skipped


@respx.mock
async def test_un_classement_perime_est_redemande(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    _mock_reports(load_text_fixture)
    await elo.refresh(elo_client, migrated, now=MOMENT)

    later = MOMENT + timedelta(hours=elo.MAX_AGE_HOURS + 1)
    assert elo.is_stale("atp", migrated, later)
    report = await elo.refresh(elo_client, migrated, now=later)
    assert report.counts["atp"] == 5


@respx.mock
async def test_un_circuit_en_panne_n_empeche_pas_l_autre(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    _mock_reports(load_text_fixture, wta=False)
    respx.get(f"{BASE_URL}{REPORTS['wta']}").mock(return_value=httpx.Response(503))

    report = await elo.refresh(elo_client, migrated, now=MOMENT)

    assert report.counts == {"atp": 5}
    assert report.errors and "wta" in report.errors[0]
    assert not report.ok


@respx.mock
async def test_le_remplacement_est_complet(
    elo_client: TennisAbstractClient, load_text_fixture: Callable[[str], str], migrated: Settings
) -> None:
    """Un joueur sorti du classement disparait, il ne vieillit pas en base."""
    elo.store("atp", [{"player": "Vieux Joueur", "elo": 1500.0}], migrated)
    _mock_reports(load_text_fixture)

    await elo.refresh(elo_client, migrated, force=True, now=MOMENT)

    assert elo.lookup("Vieux Joueur", "atp", migrated) is None


# -- Rapprochement des noms -------------------------------------------------


def _garnir(settings: Settings) -> None:
    elo.store(
        "atp",
        [
            {
                "player": "Alexei Popyrin",
                "elo": 1893.4,
                "elo_rank": 28,
                "hard_elo": 1901.2,
                "hard_rank": 25,
                "peak_elo": 1950.7,
                "peak_month": "2024-08",
                "tour_rank": 31,
            },
            {
                "player": "Raphael Collignon",
                "elo": 1712.9,
                "elo_rank": 78,
                "hard_elo": 1698.4,
                "hard_rank": 82,
                "peak_elo": 1745.0,
                "peak_month": "2026-06",
                "tour_rank": 84,
            },
        ],
        settings,
    )


def test_correspondance_exacte(migrated: Settings) -> None:
    _garnir(migrated)

    assert elo.lookup("Alexei Popyrin", "atp", migrated)["elo"] == 1893.4


def test_les_accents_ne_bloquent_pas(migrated: Settings) -> None:
    _garnir(migrated)

    assert elo.lookup("Raphaël Collignon", "atp", migrated)["elo_rank"] == 78


def test_un_nom_trop_eloigne_ne_donne_rien(migrated: Settings) -> None:
    _garnir(migrated)

    assert elo.lookup("Novak Djokovic", "atp", migrated) is None


def test_deux_candidats_trop_proches_ne_sont_pas_departages(migrated: Settings) -> None:
    """En cas de doute on ne devine pas : aucune resolution manuelle ici."""
    elo.store(
        "atp",
        [
            {"player": "Alexander Zverev", "elo": 2114.5},
            {"player": "Alexandre Zverev", "elo": 1500.0},
        ],
        migrated,
    )

    assert elo.lookup("Alexandr Zverev", "atp", migrated) is None


def test_sans_circuit_les_deux_listes_sont_fouillees(migrated: Settings) -> None:
    """Un evenement manuel n'a aucune cle qui dise le circuit."""
    _garnir(migrated)
    elo.store("wta", [{"player": "Victoria Mboko", "elo": 1948.6}], migrated)

    assert elo.lookup("Victoria Mboko", None, migrated)["elo"] == 1948.6
    assert elo.lookup("Alexei Popyrin", None, migrated)["elo"] == 1893.4


def test_circuit_deduit_de_la_cle() -> None:
    assert elo.tour_for("tennis_atp_canadian_open") == "atp"
    assert elo.tour_for("tennis_wta_canadian_open") == "wta"
    assert elo.tour_for("soccer_france_ligue_one") is None
    assert elo.tour_for(None) is None


# -- Rendu ------------------------------------------------------------------


def test_ligne_elo_avec_surface(migrated: Settings) -> None:
    _garnir(migrated)

    lines = elo.lines(
        "Raphael Collignon", "Alexei Popyrin", "tennis_atp_canadian_open", "hard", migrated
    )

    label, value = lines[0]
    assert label == "Elo"
    assert "Raphael Collignon 1713 (#78) · dur 1698 (#82)" in value
    assert "Alexei Popyrin 1893 (#28) · dur 1901 (#25)" in value
    assert "pic 1951 2024-08" in value, "l'Elo est rendu a l'entier"
    assert "classement 31e" in value
    assert value.count("\n") == 1, "un joueur par ligne, aligne sous le libelle"


def test_sans_surface_seul_l_elo_general_est_rendu(migrated: Settings) -> None:
    _garnir(migrated)

    _, value = elo.lines(
        "Raphael Collignon", "Alexei Popyrin", "tennis_atp_canadian_open", None, migrated
    )[0]

    assert "dur" not in value, "deviner la surface d'un tournoi serait une invention"
    assert "1893 (#28)" in value


def test_un_joueur_absent_du_classement_est_dit(migrated: Settings) -> None:
    _garnir(migrated)

    _, value = elo.lines(
        "Alexei Popyrin", "Parfait Inconnu", "tennis_atp_canadian_open", "hard", migrated
    )[0]

    assert "Parfait Inconnu — non trouvé au classement Elo" in value


def test_aucune_ligne_sans_classement_recupere(migrated: Settings) -> None:
    """Sur une base vierge, deux « non trouvé » feraient chercher un faux probleme."""
    assert elo.lines("Untel", "Machin", "tennis_atp_canadian_open", "hard", migrated) == []


def test_aucune_ligne_si_les_deux_joueurs_sont_inconnus(migrated: Settings) -> None:
    _garnir(migrated)

    assert elo.lines("Untel", "Machin", "tennis_atp_canadian_open", "hard", migrated) == []


# -- Integration au bloc et au prompt ---------------------------------------


def _match_de_tennis(settings: Settings, surface: str = "hard") -> int:
    event_id = save(
        build(
            "tennis",
            "ATP Canadian Open",
            "Raphael Collignon",
            "Alexei Popyrin",
            "2026-08-04",
            "19:20",
            "Raphael Collignon 1.59\nAlexei Popyrin 2.40",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    session_id = board_service.toggle_selection(event_id, True, settings)
    competition = next(
        row
        for row in competitions_service.list_all(settings)
        if row["label"] == "ATP Canadian Open"
    )
    competitions_service.set_surface(int(competition["id"]), surface, settings)
    return session_id


def test_le_bloc_de_tennis_porte_l_elo(migrated: Settings) -> None:
    session_id = _match_de_tennis(migrated)
    _garnir(migrated)

    event = renderable_events(session_id, migrated, NOW)[0]

    labels = [label for label, _ in event.context_lines]
    assert "Elo" in labels


def test_le_football_n_est_pas_touche(migrated: Settings) -> None:
    event_id = save(
        build(
            "football",
            "Ligue 1",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    _garnir(migrated)

    event = renderable_events(session_id, migrated, NOW)[0]

    labels = [label for label, _ in event.context_lines]
    assert "Elo" not in labels and "Surface" not in labels
    # Le bloc n'a **que** sa ligne de densite : sans contexte recupere, elle dit
    # que rien n'a ete collecte plutot que de laisser lire un match sans histoire.
    assert labels == ["Densite"]


def test_le_prompt_interdit_la_conversion_en_probabilite(migrated: Settings) -> None:
    """Le garde-fou compte autant que la donnee (SPEC.md section 9)."""
    session_id = _match_de_tennis(migrated)
    _garnir(migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Elo" in body
    assert "jamais** : la convertir en probabilité" in body
    assert "espérance" in body


# -- Ecrans -----------------------------------------------------------------


def test_surface_enregistree_via_htmx(client: TestClient, isolated_settings: Settings) -> None:
    _match_de_tennis(isolated_settings, surface="")
    competition = next(
        row
        for row in competitions_service.list_all(isolated_settings)
        if row["label"] == "ATP Canadian Open"
    )

    response = client.post(f"/competitions/{competition['id']}/surface", data={"surface": "clay"})

    assert response.status_code == 200
    updated = next(
        row
        for row in competitions_service.list_all(isolated_settings)
        if row["id"] == competition["id"]
    )
    assert updated["surface"] == "clay"


def test_une_surface_inconnue_vaut_non_renseignee(
    client: TestClient, isolated_settings: Settings
) -> None:
    _match_de_tennis(isolated_settings)
    competition = next(
        row
        for row in competitions_service.list_all(isolated_settings)
        if row["label"] == "ATP Canadian Open"
    )

    client.post(f"/competitions/{competition['id']}/surface", data={"surface": "moquette"})

    updated = next(
        row
        for row in competitions_service.list_all(isolated_settings)
        if row["id"] == competition["id"]
    )
    assert updated["surface"] is None


@respx.mock
def test_rafraichissement_via_htmx(
    client: TestClient, isolated_settings: Settings, load_text_fixture: Callable[[str], str]
) -> None:
    _mock_reports(load_text_fixture)

    response = client.post("/competitions/elo/refresh")

    assert response.status_code == 200
    assert "Aucun crédit consommé" in response.text
    assert elo.has_data("atp", isolated_settings)
