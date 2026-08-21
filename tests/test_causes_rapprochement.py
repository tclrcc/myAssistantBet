"""Les trois formes d'une rencontre non resolue.

**La taxonomie declaree ne decrivait pas le regime reel.** Mesure du 21/08/2026
sur les 378 tentatives journalisees : `served` 340, `unresolved` **35**,
`unmapped` 3 — et **zero** `not_covered` comme `unreachable`. Deux causes
declarees ne se produisent jamais, et la seule qui se produise etait reduite a un
mot alors qu'elle recouvre trois situations qui n'appellent pas la meme decision.

Ce sont ces 35 instances — 9 % des tentatives — qui meritaient d'etre separees.
"""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.apifootball import APIFootballClient
from myassistantbet.services.context import (
    CAUSE_BLOCK_NOTES,
    CAUSE_FIXTURE_ABSENT,
    CAUSE_LABELS,
    CAUSE_PROVIDER_EMPTY,
    CAUSE_TEAM_UNMATCHED,
    CAUSE_UI_NOTES,
    COLLECTION_FAULTS,
    UNRESOLVED_FORMS,
    fetch_context,
)

from .helpers import RATE_HEADERS, mock_context_routes

pytestmark = pytest.mark.anyio

EVENT = {
    "id": 1,
    "home": "BK Hacken",
    "away": "Djurgardens IF",
    "commence_time": "2026-08-03T15:30:00Z",
    "apifootball_league_id": 113,
}


def _seed_event(settings: Settings) -> None:
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = 113", settings=settings
    )
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (1, ?, ?, 'evt-1', ?, ?, ?, 'api', ?)",
        (
            competition["sport_id"],
            competition["id"],
            EVENT["home"],
            EVENT["away"],
            EVENT["commence_time"],
            db.utcnow(),
        ),
        settings=settings,
    )


def _journee(*rencontres: tuple[int, str, int, str]) -> dict[str, Any]:
    """Une journee servie par le fournisseur, sous la forme qu'il rend."""
    return {
        "errors": [],
        "response": [
            {
                "fixture": {"id": 900 + index, "date": EVENT["commence_time"], "venue": {}},
                "teams": {
                    "home": {"id": home_id, "name": home},
                    "away": {"id": away_id, "name": away},
                },
                "league": {"id": 113, "season": 2026},
            }
            for index, (home_id, home, away_id, away) in enumerate(rencontres)
        ],
    }


@respx.mock
async def test_un_fournisseur_muet_ne_se_repare_pas_par_un_alias(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Aucune rencontre servie ce jour-la.** Ni nos noms ni notre date ne sont
    en cause : il n'y a rien a apparier, et c'est le seul des trois cas qui se
    retente utilement plus tard."""
    _seed_event(migrated)
    routes = mock_context_routes(load_fixture)
    routes["fixtures_date"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.cause == CAUSE_PROVIDER_EMPTY


@respx.mock
async def test_une_equipe_non_appariee_se_repare_par_un_alias(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le fournisseur sert la journee, nos noms n'y tombent pas.**

    La rencontre existe probablement : c'est notre lecture qui bloque, et un
    alias la debloque pour tous les matchs a venir de cette equipe.
    """
    _seed_event(migrated)
    routes = mock_context_routes(load_fixture)
    routes["fixtures_date"].mock(
        return_value=httpx.Response(
            200,
            json=_journee((10, "Racing Club de Nulle Part", 11, "Association Inconnue")),
            headers=RATE_HEADERS,
        )
    )

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.cause == CAUSE_TEAM_UNMATCHED


@respx.mock
async def test_deux_equipes_reconnues_sans_rencontre_envoient_verifier_la_date(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Le cas que le mot unique cachait le mieux.**

    Les deux equipes sont reconnues et aucune rencontre ne les oppose ce jour-la.
    Un alias n'y peut rien : c'est la date qu'il faut verifier — report non
    repercute, ou match deplace.
    """
    _seed_event(migrated)
    routes = mock_context_routes(load_fixture)
    routes["fixtures_date"].mock(
        return_value=httpx.Response(
            200,
            json=_journee(
                (10, EVENT["home"], 11, "Association Inconnue"),
                (12, "Autre Club", 13, EVENT["away"]),
            ),
            headers=RATE_HEADERS,
        )
    )

    report = await fetch_context(api_client, dict(EVENT), migrated)

    assert report.cause == CAUSE_FIXTURE_ABSENT


def test_les_trois_formes_se_reparent_et_se_lisent_chacune_a_sa_facon() -> None:
    """Un libelle sans definition est le defaut que ce projet evite partout.

    Les trois disent la meme chose a l'analyse — rien n'a pu etre lu — mais pas
    **ce qu'une recherche y gagnerait**, et c'est cette moitie-la qui decide du
    budget.
    """
    assert UNRESOLVED_FORMS <= COLLECTION_FAULTS, "les trois se reparent, elles ne se cherchent pas"
    for cause in UNRESOLVED_FORMS:
        assert CAUSE_LABELS[cause], f"{cause} n'a pas de nom"
        assert CAUSE_BLOCK_NOTES[cause], f"{cause} ne dit rien a l'analyse"
        assert CAUSE_UI_NOTES[cause], f"{cause} ne nomme aucun geste"
        assert CAUSE_UI_NOTES[cause] != CAUSE_BLOCK_NOTES[cause]

    notes = {CAUSE_BLOCK_NOTES[cause] for cause in UNRESOLVED_FORMS}
    assert len(notes) == len(UNRESOLVED_FORMS), (
        "trois causes qui se lisent pareil ne valaient pas d'etre separees"
    )
