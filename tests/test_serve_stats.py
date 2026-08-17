"""Statistiques de service : identite des joueurs, et lecture d'une reponse.

**Le piege numero un de cette source est le nom**, et le lot 4 l'a paye deux
fois : l'API ecrit « Mccartney Kessler » quand la base ecrit « McCartney
Kessler », et une comparaison stricte a rendu « 0 point de service » — un faux
negatif de notre rapprochement, pas de la source.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import httpx
import pytest
import respx

from myassistantbet.config import Settings
from myassistantbet.providers.tennisapi import BASE_URL, TennisAPIClient
from myassistantbet.services import serve_stats
from myassistantbet.services.ingestion import MATCH_REF_UNRESOLVED
from myassistantbet.services.serve_stats import ACCENTS, CASSE, EXACT, INTROUVABLE

QUOTA_HEADERS = {"x-ratelimit-requests-remaining": "149000"}


@pytest.fixture
def tennis_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAPIClient:
    return TennisAPIClient(http_client, migrated)


@pytest.fixture
def payload(load_fixture: Callable[[str], Any]) -> Any:
    return load_fixture("tennisapi_matches_played.json")


def _search_par_nom(reponses: dict[str, list[str]]) -> None:
    """Aiguille la recherche sur le nom **decode** de l'URL.

    Un nom accentue part encode (`Anna%20Bond%C3%A1r`), donc un prefixe d'URL
    ecrit en clair ne l'attrape pas. Aiguiller sur la requete decodee dit ce que
    le test veut dire — « quand on demande ce nom-la » — au lieu de decrire un
    encodage.
    """

    def _repondre(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        demande = unquote(request.url.path).rsplit("/", 2)[-2]
        return httpx.Response(200, json=reponses.get(demande, []), headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)


# -- Le choix d'un candidat --------------------------------------------------


def test_le_niveau_exact_departage_deux_homonymes() -> None:
    """**Cas reel, et c'est lui qui impose la progression par niveau.**

    « Alexander Zverev » rend `['Alexander Zverev', 'Alexander Zverev Sr']`.
    Un repli tolerant les aurait pris tous les deux ; c'est le niveau exact qui
    les separe.
    """
    canonical, niveau = serve_stats.pick_candidate(
        "Alexander Zverev", ["Alexander Zverev", "Alexander Zverev Sr"]
    )

    assert (canonical, niveau) == ("Alexander Zverev", EXACT)


def test_le_repli_de_casse_rattrape_le_cas_mesure_au_lot_4() -> None:
    """**La base ecrit « McCartney », l'API « Mccartney ».**

    C'est ce qui avait rendu « 0 point de service » : un faux negatif de notre
    rapprochement, que le lot 4 a explicitement demande de ne pas reproduire.
    """
    canonical, niveau = serve_stats.pick_candidate("McCartney Kessler", ["Mccartney Kessler"])

    assert (canonical, niveau) == ("Mccartney Kessler", CASSE)


def test_le_repli_d_accents_est_un_niveau_a_part() -> None:
    """Il se compte separement : si `accents` devient majoritaire, la
    normalisation en amont est mauvaise, et il faut le savoir."""
    canonical, niveau = serve_stats.pick_candidate("Anna Bondár", ["Anna Bondar"])

    assert (canonical, niveau) == ("Anna Bondar", ACCENTS)


def test_deux_candidats_indiscernables_ne_sont_pas_departages() -> None:
    """**On ne devine pas, et ici c'est plus severe qu'ailleurs.**

    Il n'existe aucune resolution manuelle pour rattraper : attribuer a un
    joueur les statistiques d'un autre serait pire qu'une ligne absente. Meme
    arbitrage que l'Elo tennis.
    """
    canonical, niveau = serve_stats.pick_candidate("anna bondar", ["Anna Bondar", "ANNA BONDAR"])

    assert (canonical, niveau) == ("", INTROUVABLE)


def test_un_nom_tronque_qui_rend_plusieurs_joueurs_est_refuse() -> None:
    """« Kessler » rend trois joueuses. Aucune n'est la bonne par defaut."""
    canonical, _ = serve_stats.pick_candidate(
        "Kessler", ["F Kessler", "J Kessler", "Mccartney Kessler"]
    )

    assert canonical == ""


def test_une_liste_vide_ne_resout_rien() -> None:
    assert serve_stats.pick_candidate("Jean-Personne", []) == ("", INTROUVABLE)


# -- La resolution complete --------------------------------------------------


@respx.mock
async def test_la_resolution_memorise_et_ne_rappelle_pas(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Une fois par joueur, puis cache.** Un nom ne se re-resout pas."""
    route = respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=["Mccartney Kessler"], headers=QUOTA_HEADERS)
    )

    premiere, rejet = await serve_stats.resolve(tennis_client, "McCartney Kessler", "wta", migrated)
    seconde, _ = await serve_stats.resolve(tennis_client, "McCartney Kessler", "wta", migrated)

    assert rejet is None
    assert premiere.canonical == "Mccartney Kessler"
    assert premiere.fallback == CASSE
    assert seconde.canonical == premiere.canonical
    assert len(route.calls) == 1, "la seconde resolution sort du cache"


@respx.mock
async def test_les_accents_declenchent_un_second_appel_et_le_resolvent(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Mesure du 17/08 qui contredit l'ordre de repli du brief.**

    L'endpoint est insensible a la casse en entree — le serveur s'en charge —
    mais **pas** aux accents : `Karolína Muchová` rend une liste vide quand
    `Karolina Muchova` repond. Le repli se fait donc sur l'**entree**, avant
    l'appel, et pas seulement sur les candidats rendus.
    """
    _search_par_nom(
        {"Anna Bondár": [], "Anna Bondar": ["Anna Bondar"]},
    )

    identity, rejet = await serve_stats.resolve(tennis_client, "Anna Bondár", "wta", migrated)

    assert rejet is None
    assert identity.canonical == "Anna Bondar"
    assert identity.fallback == ACCENTS


@respx.mock
async def test_un_nom_sans_accent_ne_declenche_aucun_second_appel(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Le second appel ne coute rien au cas ordinaire, et le test le garde."""
    route = respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

    assert len(route.calls) == 1


@respx.mock
async def test_une_non_resolution_part_en_rejet_et_jamais_en_silence(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Jamais un joueur simplement absent des agregats.**

    Une sortie identique pour « pas trouve » et « rien a chercher » est le
    defaut caracteristique du projet.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    identity, rejet = await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

    assert not identity.resolved
    assert rejet is not None
    assert rejet.reason == MATCH_REF_UNRESOLVED
    assert "Jean Personne" in rejet.detail


@respx.mock
async def test_une_non_resolution_se_memorise_pour_ne_pas_repayer(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**« Cherche, pas trouve » n'est pas « jamais cherche ».**

    Les confondre ferait redemander tous les jours un nom que la source ne
    connait pas — un appel par joueur et par passe, pour un constat qui ne
    bougera pas.
    """
    route = respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=[], headers=QUOTA_HEADERS)
    )

    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)
    appels = len(route.calls)
    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

    assert len(route.calls) == appels
    assert serve_stats.load_identity("Jean Personne", "atp", migrated) is not None


@respx.mock
async def test_le_niveau_de_repli_se_compte(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**C'est la forme qui sert**, et pas une ligne de journal par resolution.

    Si `accents` devient majoritaire, la normalisation en amont est mauvaise.
    Un compte le dit ; un mois de logs que personne ne relit, jamais.
    """
    _search_par_nom(
        {
            "Alexander Zverev": ["Alexander Zverev"],
            "McCartney Kessler": ["Mccartney Kessler"],
        }
    )

    await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)
    await serve_stats.resolve(tennis_client, "McCartney Kessler", "wta", migrated)

    assert serve_stats.fallback_tally(migrated) == {EXACT: 1, CASSE: 1}


@respx.mock
async def test_la_resolution_garde_la_reponse_dont_elle_sort(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Sans elle, on saurait qu'un nom a ete resolu et jamais sur quoi."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json=["Alexander Zverev"], headers=QUOTA_HEADERS)
    )

    identity, _ = await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)

    assert identity.response_id
    from myassistantbet.services import api_archive

    assert api_archive.load(identity.response_id, migrated) is not None


def test_l_identifiant_numerique_se_note_quand_il_arrive(migrated: Settings) -> None:
    """**C'est le vrai identifiant, et il n'arrive qu'apres.**

    La recherche ne rend que des noms ; l'identifiant apparait dans la premiere
    reponse `matches-played`. Regle de revue du projet : quand un identifiant
    existe, c'est lui.
    """
    serve_stats.store_identity(
        serve_stats.Identity(
            local_name="Alexander Zverev", tour="atp", canonical="Alexander Zverev", fallback=EXACT
        ),
        migrated,
    )

    serve_stats.note_provider_id("Alexander Zverev", "atp", 24008, migrated)

    identity = serve_stats.load_identity("Alexander Zverev", "atp", migrated)
    assert identity is not None
    assert identity.provider_id == 24008


# -- La lecture d'une reponse ------------------------------------------------


def test_le_joueur_se_trouve_des_deux_cotes(payload: Any) -> None:
    """**`player1` n'est pas celui qu'on a demande.**

    Sur la fixture reelle, Zverev est `player2` sur deux matchs sur huit. Lire
    a une position fixe donnerait les statistiques de son adversaire une fois
    sur quatre, **en silence** — le genre d'erreur qui ne casse rien et se lit
    comme un profil.
    """
    lignes, ecartes = serve_stats.parse_matches_played(payload, "Alexander Zverev")

    assert len(lignes) == 8
    assert ecartes == 0
    assert "Alexander Zverev" not in {ligne.opponent for ligne in lignes}


def test_le_rapprochement_ignore_la_casse(payload: Any) -> None:
    """Meme repli que partout : `labels.sort_key` sur les deux cotes."""
    lignes, _ = serve_stats.parse_matches_played(payload, "alexander ZVEREV")

    assert len(lignes) == 8


def test_un_joueur_absent_de_la_reponse_ne_rend_aucune_ligne(payload: Any) -> None:
    """Et les matchs sont **comptes** comme ecartes, jamais tus."""
    lignes, ecartes = serve_stats.parse_matches_played(payload, "Quelqu'un d'Autre")

    assert lignes == ()
    assert ecartes == 8


def test_l_invariant_de_points_totaux_tient_sur_les_donnees_reelles(payload: Any) -> None:
    """**Le seul controle qui rattache les colonnes adverses aux notres.**

    `totalPointsWon` doit valoir points de service gagnes + points de retour
    gagnes. S'il se rompt, la reponse melange deux matchs ou deux camps, et les
    taux de retour seraient faux **sans que rien ne le montre**.
    """
    lignes, _ = serve_stats.parse_matches_played(payload, "Alexander Zverev")

    assert all(ligne.consistent for ligne in lignes)


def test_une_ligne_qui_rompt_l_invariant_est_ecartee_et_non_corrigee() -> None:
    """Une ligne dont on sait qu'elle melange deux camps n'a pas de version
    reparable."""
    faux = {
        "singles": [
            {
                "date": "2026-08-16T00:00:00.000Z",
                "player1": {
                    "name": "A",
                    "stats": {
                        "firstServe": 39,
                        "firstServeOf": 74,
                        "winningOnFirstServe": 33,
                        "winningOnSecondServe": 20,
                        "totalPointsWon": 999,
                    },
                },
                "player2": {
                    "name": "B",
                    "stats": {"firstServeOf": 111, "winningOnFirstServe": 42},
                },
            }
        ]
    }

    lignes, ecartes = serve_stats.parse_matches_played(faux, "A")

    assert lignes == ()
    assert ecartes == 1


def test_les_points_de_retour_sortent_des_colonnes_adverses(payload: Any) -> None:
    """**C'est la reponse a la question que le brief demandait de mesurer.**

    `matches-played` sert les colonnes des **deux** camps : les taux de retour
    se reconstruisent depuis la meme reponse, sans croiser deux appels.
    """
    lignes, _ = serve_stats.parse_matches_played(payload, "Alexander Zverev")
    premiere = lignes[0]

    assert premiere.return_points == premiere.opp_first_serve_of > 0
    assert 0 < premiere.return_points_won < premiere.return_points


def test_les_doubles_fautes_se_rapportent_aux_secondes_balles(payload: Any) -> None:
    """Un joueur qui rentre 75 % de premieres a mecaniquement moins d'occasions
    d'en commettre : les rapporter aux points de service melangerait deux
    grandeurs."""
    lignes, _ = serve_stats.parse_matches_played(payload, "Alexander Zverev")
    premiere = lignes[0]

    assert premiere.second_serves == premiere.first_serve_of - premiere.first_serve


def test_la_surface_vient_de_la_meme_reponse(payload: Any) -> None:
    """Aucun appel de plus pour la fenetre par surface."""
    lignes, _ = serve_stats.parse_matches_played(payload, "Alexander Zverev")

    assert {ligne.surface for ligne in lignes} == {"Hard", "Grass"}
