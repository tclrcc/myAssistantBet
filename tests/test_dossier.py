"""Dossier d'equipe : socle de memorisation par equipe, peremption, plancher.

Ce qui est verifie ici et qui n'allait pas de soi : une donnee qui vaut pour une
equipe ne doit pas se payer une fois par match, un entraineur parti ne doit
jamais etre nomme comme s'il etait en poste, et un plancher d'appels franchi doit
se dire au lieu de ressembler a une panne de rapprochement.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.apifootball import BASE_URL, PROVIDER, APIFootballClient
from myassistantbet.providers.base import record_api_usage
from myassistantbet.services import dossier
from myassistantbet.services.context import KIND_TEAMS
from myassistantbet.services.context import store as store_context

RATE_HEADERS = {"x-ratelimit-requests-remaining": "4300", "x-ratelimit-requests-limit": "7500"}

HOME = "BK Hacken"
AWAY = "Djurgardens IF"
COMMENCE = "2026-08-03T15:30:00Z"
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


@pytest.fixture
def api_client(http_client: httpx.AsyncClient, migrated: Settings) -> APIFootballClient:
    return APIFootballClient(http_client, migrated)


def _seed_event(settings: Settings, *, rapproche: bool = True) -> None:
    """Un match rattache a l'Allsvenskan, dont le rapprochement a deja eu lieu."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (1, ?, ?, 'evt-1', ?, ?, ?, 'api', ?)",
        (
            competition["sport_id"],
            competition["id"],
            HOME,
            AWAY,
            COMMENCE,
            db.utcnow(),
        ),
        settings=settings,
    )
    if rapproche:
        store_context(
            1, KIND_TEAMS, {"home": 376, "away": 377, "league": 113, "season": 2026}, settings
        )


def _mock_coachs(load_fixture: Any) -> dict[str, respx.Route]:
    def _mock(fichier: str, team: str) -> respx.Route:
        return respx.get(f"{BASE_URL}/coachs", params__contains={"team": team}).mock(
            return_value=httpx.Response(200, json=load_fixture(fichier), headers=RATE_HEADERS)
        )

    return {
        "home": _mock("apifootball_coachs_home.json", "376"),
        "away": _mock("apifootball_coachs_away.json", "377"),
    }


def _lines(settings: Settings) -> dict[str, str]:
    return dict(dossier.dossier_lines(1, HOME, AWAY, COMMENCE, settings))


# -- Recuperation et memorisation --------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_le_dossier_porte_l_entraineur_et_son_anciennete(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """L'anciennete est le signal, pas le nom : une equipe qui a change
    d'entraineur il y a six semaines ne se lit pas comme celle qui garde le sien
    depuis trois ans. La date situe, la duree se lit d'un coup d'oeil."""
    _seed_event(migrated)
    _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Entraineur"] == (
        "BK Hacken P. Gustafsson (depuis 06/2023, 3 ans) | "
        "Djurgardens IF M. Lindqvist (depuis 06/2026, 1 mois)"
    )


@respx.mock
@pytest.mark.anyio
async def test_un_entraineur_parti_n_est_jamais_nomme_en_poste(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le fournisseur rend plusieurs entraineurs pour une meme equipe : le
    predecesseur y figure avec sa date de fin. Le poste en cours est celui dont
    l'etape de carriere dans cette equipe n'est pas refermee — prendre le premier
    de la liste nommerait un entraineur parti, affirme comme un fait."""
    _seed_event(migrated)
    _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    ligne = _lines(migrated)["Entraineur"]
    assert "M. Lindqvist" in ligne
    assert "T. Kalmar" not in ligne, "son etape a Djurgarden est refermee"


@respx.mock
@pytest.mark.anyio
async def test_l_anciennete_se_compte_dans_l_equipe_du_match_pas_dans_la_precedente(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La carriere porte aussi les clubs precedents : compter depuis le premier
    poste donnerait « depuis 02/2024 » pour une arrivee de juin 2026."""
    _seed_event(migrated)
    _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "depuis 06/2026" in _lines(migrated)["Entraineur"]
    assert "2024" not in _lines(migrated)["Entraineur"]


@respx.mock
@pytest.mark.anyio
async def test_un_releve_frais_n_est_pas_repaye(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """C'est toute la raison d'etre du stockage par equipe : l'entraineur d'une
    equipe est le meme dans les deux affiches ou elle apparait cette semaine.
    Memorise par match, il se paierait autant de fois qu'elle joue."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    rapport = await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["home"].call_count == 1, "le second passage relit la base"
    assert routes["away"].call_count == 1
    assert rapport.kinds == [], "rien n'a ete recupere"
    assert dossier.KIND_COACH in rapport.cached, "et le dire evite de croire a un echec"


@respx.mock
@pytest.mark.anyio
async def test_un_releve_perime_est_rafraichi(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un entraineur limoge doit entrer dans le bloc, et pas dans un mois : la
    peremption est ce qui distingue un cache d'un oubli."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    plus_tard = NOW + timedelta(hours=dossier.TTL_HOURS[dossier.KIND_COACH] + 1)
    await dossier.refresh_event(api_client, 1, migrated, now=plus_tard)

    assert routes["home"].call_count == 2


@respx.mock
@pytest.mark.anyio
async def test_une_date_de_releve_illisible_vaut_perimee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Mieux vaut un appel de trop qu'une donnee dont on ne sait plus quand elle
    a ete prise."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)
    dossier.store(376, dossier.KIND_COACH, [], settings=migrated)
    db.execute(
        "UPDATE team_context SET fetched_at = 'jamais' WHERE team_id = 376", settings=migrated
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["home"].call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_le_stockage_est_idempotent(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Cle naturelle `(equipe, type, perimetre)` : relancer ne duplique rien."""
    _seed_event(migrated)
    _mock_coachs(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    plus_tard = NOW + timedelta(hours=dossier.TTL_HOURS[dossier.KIND_COACH] + 1)
    await dossier.refresh_event(api_client, 1, migrated, now=plus_tard)

    lignes = db.query("SELECT team_id FROM team_context WHERE team_id = 376", settings=migrated)
    assert len(lignes) == 1


# -- Ce qu'on ne devine pas ---------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_sans_rapprochement_aucun_appel_et_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un evenement dont le rapprochement est reste incertain n'a aucun
    identifiant d'equipe. Interroger au hasard attribuerait l'entraineur d'un
    autre club, ce qui serait pire qu'une ligne absente."""
    _seed_event(migrated, rapproche=False)
    routes = _mock_coachs(load_fixture)

    rapport = await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["home"].call_count == 0
    assert rapport.ok
    assert _lines(migrated) == {}


@respx.mock
@pytest.mark.anyio
async def test_une_equipe_sans_entraineur_servi_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Base vierge de ce cote : aucune ligne, jamais « inconnu » — qui se lirait
    comme un fait sur l'equipe. L'autre equipe garde la sienne."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)
    routes["away"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    ligne = _lines(migrated)["Entraineur"]
    assert "BK Hacken P. Gustafsson" in ligne
    assert AWAY not in ligne


@respx.mock
@pytest.mark.anyio
async def test_une_prise_de_fonction_posterieure_au_match_ne_rend_aucune_duree(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une anciennete negative presentee comme une duree serait une absurdite
    affichee. La date reste, elle est verifiable."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)
    futur = load_fixture("apifootball_coachs_home.json")
    futur["response"][0]["career"][0]["start"] = "2027-01-05"
    routes["home"].mock(return_value=httpx.Response(200, json=futur, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "BK Hacken P. Gustafsson (depuis 01/2027)" in _lines(migrated)["Entraineur"]


# -- Plancher d'appels --------------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_le_plancher_d_appels_suspend_le_dossier_et_le_dit(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le quota API-Football n'etait surveille nulle part : il ne servait qu'au
    contexte, quelques dizaines d'appels par soiree. Un plancher franchi n'est
    pas une panne, mais le taire ferait chercher une erreur de rapprochement la
    ou il n'y a qu'un quota bas."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)
    record_api_usage(PROVIDER, "/fixtures", 1, migrated.apifootball_call_floor, migrated)

    rapport = await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["home"].call_count == 0
    assert rapport.blocked_reason is not None
    assert "plancher" in rapport.blocked_reason
    assert not rapport.ok


@respx.mock
@pytest.mark.anyio
async def test_un_quota_inconnu_laisse_partir(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Etat d'une installation qui n'a jamais appele le fournisseur : le premier
    appel renseignera le compteur, et bloquer par principe empecherait de
    demarrer."""
    _seed_event(migrated)
    routes = _mock_coachs(load_fixture)

    rapport = await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["home"].call_count == 1
    assert rapport.blocked_reason is None


@respx.mock
@pytest.mark.anyio
async def test_le_plancher_ne_bloque_que_le_dossier(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le contexte d'un match reste la fonction premiere de l'outil : l'arreter
    faute de quota pour un bonus serait le mauvais arbitrage. Un seul appel de
    contexte doit encore pouvoir partir sous le plancher."""
    _seed_event(migrated)
    injuries = respx.get(f"{BASE_URL}/injuries").mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )
    record_api_usage(PROVIDER, "/fixtures", 1, 10, migrated)

    await api_client.injuries(4242)

    assert injuries.call_count == 1


# -- Peremption, en isolation -------------------------------------------------


def test_un_releve_jamais_pris_n_est_pas_frais() -> None:
    assert not dossier.is_fresh(dossier.KIND_COACH, None, NOW)


def test_la_fraicheur_se_mesure_a_la_duree_du_type() -> None:
    """Chaque type a la sienne : elle se regle sur la vitesse a laquelle la
    donnee change, bornee par ce qu'elle coute."""
    pris = (NOW - timedelta(hours=25)).strftime("%Y-%m-%dT%H:%M:%SZ")

    assert dossier.is_fresh(dossier.KIND_COACH, pris, NOW), "sept jours pour un entraineur"
    assert not dossier.is_fresh(dossier.KIND_COACH, pris, NOW, ttl_hours=12)


@respx.mock
@pytest.mark.anyio
async def test_relire_le_dossier_ne_declenche_aucun_appel(migrated: Settings) -> None:
    """Meme regle en deux temps que le contexte : `refresh_event` appelle et
    persiste, `dossier_lines` relit. Regenerer un prompt dix fois ne coute rien —
    aucune route n'est simulee ici, donc le moindre appel ferait echouer le test."""
    _seed_event(migrated)
    dossier.store(
        376,
        dossier.KIND_COACH,
        [
            {
                "name": "P. Gustafsson",
                "career": [{"team": {"id": 376}, "start": "2023-06-15", "end": None}],
            }
        ],
        settings=migrated,
    )

    for _ in range(3):
        assert "P. Gustafsson" in _lines(migrated)["Entraineur"]
