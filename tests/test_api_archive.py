"""L'archive des reponses brutes, et le rejeu qui en depend.

**C'est la lecon directe de Sackmann.** Une source gratuite a disparu du jour au
lendemain — 404 sur `raw` comme sur l'API du depot — et avec elle les colonnes
sur lesquelles reposait tout le calcul de service. Rien n'en avait ete garde
localement, donc rien n'a pu etre sauve. `tennis-api.com` est payante,
proprietaire et unique : cette table est le seul endroit ou l'historique deja
constitue survivra a une fin d'abonnement.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from myassistantbet.config import Settings
from myassistantbet.db import query
from myassistantbet.providers import tennisapi
from myassistantbet.providers.base import ProviderError, archive_params
from myassistantbet.providers.tennisapi import BASE_URL, TennisAPIClient
from myassistantbet.replay_api import replay_response
from myassistantbet.services import api_archive

QUOTA_HEADERS = {
    "x-ratelimit-requests-limit": "150000",
    "x-ratelimit-requests-remaining": "149997",
}


@pytest.fixture
def tennis_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAPIClient:
    return TennisAPIClient(http_client, migrated)


@pytest.fixture
def payload(load_fixture: Callable[[str], Any]) -> Any:
    return load_fixture("tennisapi_matches_played.json")


# -- L'ecriture precede la lecture -------------------------------------------


@respx.mock
async def test_la_reponse_est_archivee_telle_quelle(
    tennis_client: TennisAPIClient, migrated: Settings, payload: Any
) -> None:
    """**Integrale**, et non reduite aux champs qu'on lit aujourd'hui.

    Ce qui est jete ici ne se recupere plus, et le schema d'un fournisseur
    bouge. C'est exactement ce que `dossier._summarize` a le droit de faire — sa
    source est vivante et se redemande — et que ceci n'a pas.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )

    await tennis_client.matches_played("Alexander Zverev")

    rows = query("SELECT * FROM api_responses", settings=migrated)
    assert len(rows) == 1
    assert json.loads(rows[0]["raw_json"]) == payload
    assert rows[0]["http_status"] == 200
    assert rows[0]["quota_remaining"] == 149997
    assert rows[0]["endpoint"] == tennisapi.MATCHES_PLAYED


@respx.mock
async def test_une_reponse_vide_est_archivee_comme_les_autres(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Le defaut caracteristique du projet, dans la source elle-meme.**

    `"success": true` sur un `result` vide : le vide y a la meme sortie que la
    donnee. N'archiver que ce qui se lit reproduirait ici le silence qu'on
    supprime partout ailleurs.
    """
    vide = {"success": True, "result": []}
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=vide, headers=QUOTA_HEADERS)
    )

    await tennis_client.event("A", "B", "2026-08-11")

    rows = query("SELECT raw_json FROM api_responses", settings=migrated)
    assert len(rows) == 1, "une reponse vide reste une reponse, et elle se garde"


@respx.mock
async def test_une_reponse_en_erreur_est_archivee(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """C'est **precisement** le cas ou le parsing echoue qu'on veut rejouer."""
    respx.get(url__startswith=BASE_URL).mock(return_value=httpx.Response(404, text="not found"))

    with pytest.raises(ProviderError):
        await tennis_client.matches_played("Personne")

    rows = query("SELECT http_status, raw_json FROM api_responses", settings=migrated)
    assert len(rows) == 1
    assert rows[0]["http_status"] == 404


@respx.mock
async def test_l_archive_ne_fait_jamais_tomber_la_collecte(
    tennis_client: TennisAPIClient, migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Un temoin ne doit pas devenir un point de panne.**

    Une archive qui leverait transformerait le mecanisme de secours en cause
    d'echec, ce qui est l'inverse de son role.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    def _casse(*_: object, **__: object) -> None:
        raise RuntimeError("disque plein")

    monkeypatch.setattr("myassistantbet.providers.tennisapi.archive_response", _casse)

    with pytest.raises(RuntimeError):
        await tennis_client.search_player("Alexander Zverev", "atp")


def test_les_secrets_ne_sont_jamais_ecrits_dans_les_parametres() -> None:
    """Une archive est un troisieme endroit ou un secret ne doit pas atterrir,
    apres le cache disque et les logs."""
    rendu = archive_params({"pageSize": 100, "apiKey": "secret", "x-rapidapi-key": "secret"})

    assert "secret" not in rendu
    assert json.loads(rendu) == {"pageSize": 100}


def test_les_parametres_sont_tries(migrated: Settings) -> None:
    """Deux appels identiques ne doivent pas produire deux archives que rien ne
    rapproche."""
    assert archive_params({"page": 2, "pageSize": 100}) == archive_params(
        {"pageSize": 100, "page": 2}
    )


@respx.mock
async def test_deux_relevés_du_meme_endpoint_font_deux_archives(
    tennis_client: TennisAPIClient, migrated: Settings, payload: Any
) -> None:
    """**Aucune deduplication sur l'empreinte**, contrairement a `imports_raw`.

    La raison est inverse : deux collages du meme texte n'apportent rien, quand
    deux relevés du meme endpoint a deux dates disent **que la source n'a pas
    bouge** — c'est exactement la question du lot 4, et l'empreinte y repond.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )

    await tennis_client.matches_played("Alexander Zverev")
    await tennis_client.matches_played("Alexander Zverev")

    rows = query("SELECT sha256 FROM api_responses", settings=migrated)
    assert len(rows) == 2
    assert rows[0]["sha256"] == rows[1]["sha256"], "meme contenu, deux relevés"


# -- Le rejeu ----------------------------------------------------------------


@respx.mock
async def test_le_rejeu_relit_sans_rappeler_le_fournisseur(
    tennis_client: TennisAPIClient, migrated: Settings, payload: Any
) -> None:
    """**Une reponse archivee est un fait date.**

    La redemander produirait une autre reponse, donc mesurerait autre chose.
    C'est aussi ce qui rend le rejeu gratuit en quota.
    """
    route = respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await tennis_client.matches_played("Alexander Zverev")
    appels = len(route.calls)

    archive = api_archive.recent(settings=migrated)[0]
    report = replay_response(archive.id, migrated)

    assert len(route.calls) == appels, "le rejeu ne rappelle jamais la source"
    assert report.subject == "Alexander Zverev"
    assert len(report.lines) == 8
    assert report.service_points > 0


def test_le_rejeu_d_un_identifiant_inconnu_le_dit(migrated: Settings) -> None:
    with pytest.raises(LookupError):
        replay_response(4242, migrated)


@respx.mock
async def test_une_famille_sans_lecteur_le_dit_au_lieu_de_se_taire(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Une sortie identique pour « rien a lire » et « rien lu » serait le defaut
    que ce projet passe son temps a supprimer."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=["Alexander Zverev"], headers=QUOTA_HEADERS)
    )
    await tennis_client.search_player("Alexander Zverev", "atp")

    archive = api_archive.recent(settings=migrated)[0]
    report = replay_response(archive.id, migrated)

    assert "aucun lecteur" in report.note
    assert report.lines == ()


@respx.mock
async def test_le_sujet_se_lit_dans_le_chemin_archive(
    tennis_client: TennisAPIClient, migrated: Settings, payload: Any
) -> None:
    """**Le chemin porte deja le joueur** : on ne redemande pas ce qu'on tient.

    C'est la meme regle que la famille d'appel — l'identifiant existe, il ne se
    devine pas.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=payload, headers=QUOTA_HEADERS)
    )
    await tennis_client.matches_played("Alexander Zverev")

    archive = api_archive.load(api_archive.recent(settings=migrated)[0].id, migrated)
    assert archive is not None
    assert archive.path.endswith("/matches-played")
    assert "Alexander Zverev" in archive.path


def test_un_corps_illisible_se_rend_tel_quel(migrated: Settings) -> None:
    """C'est justement une reponse en erreur qu'on veut pouvoir regarder."""
    from myassistantbet.providers.base import archive_response

    archive_response("tennisapi", tennisapi.SEARCH, "/x", "pas du json", settings=migrated)

    archive = api_archive.recent(settings=migrated)[0]
    assert archive.data == "pas du json"
