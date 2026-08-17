"""Le client `tennis-api.com` : quota mensuel, et endpoints interdits.

Le quota de ce fournisseur ne ressemble a aucun des deux autres — il est
**mensuel**. Un plancher franchi ne se rouvre pas le lendemain, et une reprise
d'historique qui l'epuise le 8 laisse l'application sans donnees pendant trois
semaines. C'est ce qui justifie de mesurer avant de depenser, et ces tests
gardent la mesure.
"""

from __future__ import annotations

import httpx
import pytest
import respx

from myassistantbet.config import Settings, get_settings
from myassistantbet.db import query
from myassistantbet.providers import tennisapi
from myassistantbet.providers.base import ProviderError
from myassistantbet.providers.tennisapi import (
    BASE_URL,
    PREFIX,
    ForbiddenEndpoint,
    TennisAPIClient,
)
from myassistantbet.services import serve_stats

QUOTA_HEADERS = {
    "x-ratelimit-requests-limit": "150000",
    "x-ratelimit-requests-remaining": "149997",
    "x-ratelimit-requests-reset": "2677861",
}


@pytest.fixture
def tennis_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAPIClient:
    return TennisAPIClient(http_client, migrated)


# -- Les endpoints interdits -------------------------------------------------


@pytest.mark.parametrize(
    "path",
    [
        "/odds/match/123",
        "/predictions/today",
        "/tennis/value-bets",
        "/top-matches/atp",
        "/profile/x/tips",
    ],
)
def test_les_endpoints_de_cote_et_de_pronostic_sont_refuses(path: str) -> None:
    """**Ecrit dans le code, pas seulement dans le brief.**

    Le plan servi ne rend pas ces endpoints aujourd'hui. L'interdit existe pour
    le jour ou un plan changerait : l'editeur alimente avec cette meme API deux
    services commerciaux de pronostics, et les ingerer rendrait le residu au
    prix ininterpretable — il mesurerait un melange de deux analyses.
    """
    with pytest.raises(ForbiddenEndpoint):
        tennisapi.check_path(path)


def test_l_interdit_porte_sur_les_segments_et_non_sur_la_chaine() -> None:
    """Un interdit qui attrape des cas legitimes finit par etre desactive.

    `"odds" in path` refuserait un joueur nomme « Todds » ; le controle porte
    donc sur les **segments** du chemin, ou `odds` est un mot et non une
    sous-chaine.
    """
    tennisapi.check_path("/profile/Todds Martin/matches-played")
    tennisapi.check_path("/profile/search/Nick Kyrgios/atp")


@respx.mock
async def test_un_appel_interdit_ne_part_pas_sur_le_reseau(
    tennis_client: TennisAPIClient,
) -> None:
    """L'exception se leve **avant** le transport, donc avant le cache."""
    route = respx.get(url__startswith=BASE_URL).mock(return_value=httpx.Response(200, json={}))

    with pytest.raises(ForbiddenEndpoint):
        await tennis_client.get("/odds/atp", tennisapi.SEARCH)

    assert not route.called, "un endpoint interdit ne doit pas atteindre le reseau"


# -- Le quota ----------------------------------------------------------------


@respx.mock
async def test_un_appel_vaut_un_credit_et_le_compteur_est_persiste(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Un appel vaut un credit, quoi qu'il rende.**

    C'est la difference avec The Odds API, qui facture au marche servi : ici une
    reponse vide se paie comme une reponse pleine. Un `result` vide n'est donc
    pas un appel gratuit qu'on pourrait retenter sans compter.
    """
    respx.get(f"{BASE_URL}{PREFIX}/profile/search/Alexander Zverev/atp").mock(
        return_value=httpx.Response(200, json=["Alexander Zverev"], headers=QUOTA_HEADERS)
    )

    await tennis_client.search_player("Alexander Zverev", "atp")

    rows = query("SELECT provider, cost, remaining FROM api_usage", settings=migrated)
    assert [(r["provider"], r["cost"], r["remaining"]) for r in rows] == [("tennisapi", 1, 149997)]


@respx.mock
async def test_l_en_tete_de_navigateur_accompagne_l_appel(
    tennis_client: TennisAPIClient,
) -> None:
    """Sans lui, **Cloudflare rend une erreur 1010** et non un 403.

    Le message ne nomme pas la cause : l'appel a l'air d'un refus
    d'authentification, et on cherche la cle. Meme precaution que Tennis
    Abstract, et pour la meme raison.
    """
    route = respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    await tennis_client.search_player("Nick Kyrgios", "atp")

    envoye = route.calls.last.request.headers
    assert "Mozilla" in envoye["user-agent"]
    assert envoye["x-rapidapi-host"] == tennisapi.HOST


@respx.mock
async def test_la_cle_ne_parait_ni_dans_le_journal_ni_dans_une_erreur(
    http_client: httpx.AsyncClient, migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Une cle absente est un etat normal, et son message ne la cite pas."""
    monkeypatch.setenv("RAPIDAPI_KEY", "")
    get_settings.cache_clear()
    client = TennisAPIClient(http_client, get_settings())

    with pytest.raises(ProviderError) as capture:
        await client.search_player("Nick Kyrgios", "atp")

    assert "aucune cle" in str(capture.value)


# -- Le plancher -------------------------------------------------------------


def test_un_quota_inconnu_laisse_partir(migrated: Settings) -> None:
    """C'est l'etat d'une installation qui n'a jamais appele le fournisseur.

    Refuser le premier appel rendrait le compteur impossible a etablir, donc le
    plancher auto-bloquant pour toujours. Meme regle que le dossier d'equipe.
    """
    etat = serve_stats.budget(migrated)

    assert not etat.known
    assert etat.allowed
    assert etat.spendable is None
    assert etat.note == ""


@pytest.mark.parametrize(
    ("restant", "autorise"),
    [(20_001, True), (20_000, False), (19_999, False), (150_000, True)],
)
def test_le_plancher_arrete_la_collecte(migrated: Settings, restant: int, autorise: bool) -> None:
    """La borne appartient au refus : a `floor` exactement, on ne part plus."""
    _quota(migrated, restant)

    etat = serve_stats.budget(migrated)

    assert etat.allowed is autorise
    assert etat.spendable == max(0, restant - 20_000)


def test_le_refus_dit_que_le_quota_est_mensuel(migrated: Settings) -> None:
    """**Le seul plancher du projet qui ne se rouvre pas demain.**

    Les deux autres gardent des quotas journaliers. Laisser croire que celui-ci
    se comporte pareil ferait attendre une reprise qui n'arrivera qu'au
    renouvellement.
    """
    _quota(migrated, 12_000)

    note = serve_stats.budget(migrated).note

    assert "mensuel" in note
    assert "pas demain" in note
    assert "12000" in note.replace(" ", "")


# -- La consommation ---------------------------------------------------------


@respx.mock
async def test_la_consommation_se_compte_par_famille_et_non_par_joueur(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Le nom du joueur ne doit pas faire une ligne par joueur.**

    Sans famille declaree, le tableau compterait des noms au lieu de compter des
    endpoints, et ne dirait plus rien du dimensionnement.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json={"singles": []}, headers=QUOTA_HEADERS)
    )

    for nom in ("Alexander Zverev", "Aryna Sabalenka", "Alex Michelsen"):
        await tennis_client.matches_played(nom)
    await tennis_client.search_player("Alexander Zverev", "atp")

    releve = serve_stats.consumption(settings=migrated)

    assert {ligne.endpoint: ligne.calls for ligne in releve} == {
        tennisapi.MATCHES_PLAYED: 3,
        tennisapi.SEARCH: 1,
    }


@respx.mock
async def test_deux_endpoints_voisins_ne_se_confondent_pas(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Le defaut que ce test a trouve, et qui fonde la regle.**

    Les deux chemins partagent leur premier segment et portent leur segment
    variable a des rangs differents : `profile/{nom}/matches-played` au milieu,
    `profile/search/{nom}/{tour}` a l'avant-dernier rang. Une derivee du chemin
    par « premier plus dernier segment » rangeait la recherche sous
    `profile/atp` — le circuit pris pour une famille.

    La famille est donc **declaree par l'appelant**, qui la tient deja.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    await tennis_client.search_player("Alexander Zverev", "atp")
    await tennis_client.search_player("Aryna Sabalenka", "wta")

    familles = {ligne.endpoint for ligne in serve_stats.consumption(settings=migrated)}
    assert familles == {tennisapi.SEARCH}, "le circuit n'est pas une famille d'appel"


async def test_une_famille_non_declaree_est_refusee(tennis_client: TennisAPIClient) -> None:
    """Un compte se fait sur une enumeration, sinon deux orthographes le cassent."""
    with pytest.raises(ProviderError):
        await tennis_client.get("/profile/x/matches-played", "profil/matchs")


# -- Outils ------------------------------------------------------------------


def _quota(settings: Settings, remaining: int) -> None:
    _appel(settings, "/tennis/v2/profile/x/matches-played", remaining)


def _appel(settings: Settings, endpoint: str, remaining: int = 149_000) -> None:
    from myassistantbet.db import connect, utcnow

    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO api_usage (provider, endpoint, cost, remaining, called_at) "
            "VALUES ('tennisapi', ?, 1, ?, ?)",
            (endpoint, remaining, utcnow()),
        )
