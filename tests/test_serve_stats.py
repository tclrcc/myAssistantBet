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
from myassistantbet.db import query
from myassistantbet.providers.tennisapi import BASE_URL, TennisAPIClient
from myassistantbet.services import api_archive, serve_stats
from myassistantbet.services.ingestion import MATCH_REF_UNRESOLVED, SOURCE_VIDE
from myassistantbet.services.serve_stats import ACCENTS, CASSE, EXACT, NOM

QUOTA_HEADERS = {"x-ratelimit-requests-remaining": "149000"}


@pytest.fixture
def tennis_client(http_client: httpx.AsyncClient, migrated: Settings) -> TennisAPIClient:
    return TennisAPIClient(http_client, migrated)


@pytest.fixture
def payload(load_fixture: Callable[[str], Any]) -> Any:
    return load_fixture("tennisapi_matches_played.json")


def _un_match(joueur: str, adversaire: str = "Quelqu'un") -> dict[str, Any]:
    """Un match minimal mais **coherent** : l'invariant des points totaux ferme.

    Ecrire des nombres au hasard ferait echouer le controle et rendrait le
    profil vide, donc chaque test de resolution echouerait pour une raison qui
    n'est pas la sienne.
    """
    return {
        "date": "2026-08-16T00:00:00.000Z",
        "tournament": {"name": "Test", "court": {"name": "Hard"}},
        "player1": {
            "id": 1,
            "name": joueur,
            "stats": {
                "firstServe": 40,
                "firstServeOf": 70,
                "aces": 5,
                "doubleFaults": 2,
                "winningOnFirstServe": 30,
                "winningOnFirstServeOf": 40,
                "winningOnSecondServe": 15,
                "winningOnSecondServeOf": 30,
                "breakPointsConverted": 2,
                "breakPointsConvertedOf": 6,
                "totalPointsWon": 65,
            },
        },
        "player2": {
            "id": 2,
            "name": adversaire,
            "stats": {
                "firstServe": 30,
                "firstServeOf": 60,
                "aces": 3,
                "doubleFaults": 4,
                "winningOnFirstServe": 20,
                "winningOnFirstServeOf": 30,
                "winningOnSecondServe": 20,
                "winningOnSecondServeOf": 30,
                "breakPointsConverted": 1,
                "breakPointsConvertedOf": 4,
                "totalPointsWon": 65,
            },
        },
    }


def _source(
    recherches: dict[str, list[str]], profils: dict[str, list[dict[str, Any]]] | None = None
) -> None:
    """Simule les deux endpoints, aiguilles sur le nom **decode** de l'URL.

    Un nom accentue part encode (`Anna%20Bond%C3%A1r`), donc un prefixe d'URL
    ecrit en clair ne l'attrape pas. Aiguiller sur la requete decodee dit ce que
    le test veut dire — « quand on demande ce nom-la » — au lieu de decrire un
    encodage.

    `profils` porte les matchs par graphie canonique. **Un nom absent rend un
    profil vide**, ce qui est le cas reel a reproduire : `Leylah Fernandez`
    existe chez le fournisseur et ne sert aucun match.
    """
    profils = profils or {}

    def _repondre(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        chemin = unquote(request.url.path)
        if "/matches-played" in chemin:
            nom = chemin.rsplit("/", 2)[-2]
            return httpx.Response(
                200,
                json={"singles": profils.get(nom, []), "singlesCount": len(profils.get(nom, []))},
                headers=QUOTA_HEADERS,
            )
        nom = chemin.rsplit("/", 2)[-2]
        return httpx.Response(200, json=recherches.get(nom, []), headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)


# -- Le choix d'un candidat --------------------------------------------------


def test_le_niveau_exact_departage_deux_homonymes() -> None:
    """**Cas reel, et c'est lui qui impose la progression par niveau.**

    « Alexander Zverev » rend `['Alexander Zverev', 'Alexander Zverev Sr']`.
    Un repli tolerant les aurait pris tous les deux ; c'est le niveau exact qui
    les place en tete.
    """
    ordre = serve_stats.rank_candidates(
        "Alexander Zverev", ["Alexander Zverev", "Alexander Zverev Sr"]
    )

    assert ordre[0] == ("Alexander Zverev", EXACT)


def test_le_repli_de_casse_rattrape_le_cas_mesure_au_lot_4() -> None:
    """**La base ecrit « McCartney », l'API « Mccartney ».**

    C'est ce qui avait rendu « 0 point de service » : un faux negatif de notre
    rapprochement, que le lot 4 a explicitement demande de ne pas reproduire.
    """
    assert serve_stats.rank_candidates("McCartney Kessler", ["Mccartney Kessler"]) == [
        ("Mccartney Kessler", CASSE)
    ]


def test_le_repli_d_accents_est_un_niveau_a_part() -> None:
    """Il se compte separement : si `accents` devient majoritaire, la
    normalisation en amont est mauvaise, et il faut le savoir."""
    assert serve_stats.rank_candidates("Anna Bondár", ["Anna Bondar"]) == [("Anna Bondar", ACCENTS)]


def test_deux_candidats_indiscernables_ne_sont_pas_proposes() -> None:
    """**On ne devine pas, et ici c'est plus severe qu'ailleurs.**

    Il n'existe aucune resolution manuelle pour rattraper : attribuer a un
    joueur les statistiques d'un autre serait pire qu'une ligne absente. Meme
    arbitrage que l'Elo tennis.
    """
    assert serve_stats.rank_candidates("anna bondar", ["Anna Bondar", "ANNA BONDAR"]) == []


def test_un_nom_tronque_qui_rend_plusieurs_joueurs_ne_propose_rien() -> None:
    """« Kessler » rend trois joueuses. Aucune n'est la bonne par defaut."""
    assert serve_stats.rank_candidates("Kessler", ["F Kessler", "J Kessler", "M Kessler"]) == []


def test_une_liste_vide_ne_propose_rien() -> None:
    assert serve_stats.rank_candidates("Jean-Personne", []) == []


def test_le_nom_de_famille_n_est_pris_que_sur_un_nom_compose() -> None:
    """Rechercher « Gauff » quand on cherchait deja « Gauff » serait un appel de
    plus pour la meme reponse."""
    assert serve_stats.surname("Coco Gauff") == "Gauff"
    assert serve_stats.surname("Gauff") == ""


# -- La resolution complete --------------------------------------------------


@respx.mock
async def test_un_profil_vide_fait_essayer_le_candidat_suivant(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Le defaut que la mesure a trouve, et qui a change le dessin.**

    Le premier jet tranchait sur le nom : « Leylah Fernandez » a une
    correspondance **exacte** chez le fournisseur, et ce profil-la porte **zero
    match**. Le vrai est « Leylah Annie Fernandez », 452 matchs, et la recherche
    rend **les deux**.

    Un nom n'est donc pas une resolution : ce qui tranche est le profil qui sert
    des donnees. Meme regle de revue que partout — l'identifiant est celui qui
    designe quelque chose.
    """
    _source(
        {"Leylah Fernandez": ["Leylah Annie Fernandez", "Leylah Fernandez"]},
        {"Leylah Annie Fernandez": [_un_match("Leylah Annie Fernandez")]},
    )

    identity, charge, rejet = await serve_stats.resolve(
        tennis_client, "Leylah Fernandez", "wta", migrated
    )

    assert rejet is None
    assert identity.canonical == "Leylah Annie Fernandez"
    assert charge is not None, "la charge utile est rendue pour ne pas etre repayee"


@respx.mock
async def test_la_validation_ne_coute_aucun_appel_de_plus(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """La reponse `matches-played` est celle qu'on allait demander de toute
    facon : elle valide **et** sert, donc la validation est gratuite."""
    _source(
        {"Alexander Zverev": ["Alexander Zverev"]},
        {"Alexander Zverev": [_un_match("Alexander Zverev")]},
    )

    _, charge, _ = await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)

    assert len(respx.calls) == 2, "une recherche, un profil — et rien de plus"
    assert charge is not None


@respx.mock
async def test_le_nom_de_famille_rattrape_un_prenom_different(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Cas mesure : la source ecrit « Cori Gauff ».**

    « Coco Gauff » rend une liste vide, et aucun repli de casse ou d'accent ne
    rattrape un prenom different. « Gauff » seul rend exactement un candidat, et
    c'est l'unicite qui rend le niveau sur — « Fernandez » en rend 94.
    """
    _source(
        {"Coco Gauff": [], "Gauff": ["Cori Gauff"]},
        {"Cori Gauff": [_un_match("Cori Gauff")]},
    )

    identity, _, rejet = await serve_stats.resolve(tennis_client, "Coco Gauff", "wta", migrated)

    assert rejet is None
    assert identity.canonical == "Cori Gauff"
    assert identity.fallback == NOM


@respx.mock
async def test_un_nom_de_famille_ambigu_ne_resout_rien(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """« Fernandez » rend quatre-vingt-quatorze candidats : aucun n'est propose."""
    _source({"Machin Fernandez": [], "Fernandez": ["Maria Fernandez", "Gigi Fernandez"]})

    identity, _, rejet = await serve_stats.resolve(
        tennis_client, "Machin Fernandez", "wta", migrated
    )

    assert not identity.resolved
    assert rejet is not None


@respx.mock
async def test_la_resolution_memorise_et_ne_rappelle_pas(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Une fois par joueur, puis cache.** Un nom ne se re-resout pas."""
    _source(
        {"McCartney Kessler": ["Mccartney Kessler"]},
        {"Mccartney Kessler": [_un_match("Mccartney Kessler")]},
    )

    premiere, _, rejet = await serve_stats.resolve(
        tennis_client, "McCartney Kessler", "wta", migrated
    )
    appels = len(respx.calls)
    seconde, _, _ = await serve_stats.resolve(tennis_client, "McCartney Kessler", "wta", migrated)

    assert rejet is None
    assert premiere.canonical == "Mccartney Kessler"
    assert premiere.fallback == CASSE
    assert seconde.canonical == premiere.canonical
    assert len(respx.calls) == appels, "la seconde resolution sort du cache"


@respx.mock
async def test_les_accents_declenchent_un_second_appel_et_le_resolvent(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Mesure du 17/08 qui contredit l'ordre de repli du brief.**

    L'endpoint est insensible a la casse en entree — le serveur s'en charge —
    mais **pas** aux accents : `Iva Jović` rend une liste vide quand `Iva Jovic`
    repond. Le repli se fait donc sur l'**entree**, avant l'appel, et pas
    seulement sur les candidats rendus.
    """
    _source(
        {"Iva Jović": [], "Iva Jovic": ["Iva Jovic"]},
        {"Iva Jovic": [_un_match("Iva Jovic")]},
    )

    identity, _, rejet = await serve_stats.resolve(tennis_client, "Iva Jović", "wta", migrated)

    assert rejet is None
    assert identity.canonical == "Iva Jovic"
    assert identity.fallback == ACCENTS


@respx.mock
async def test_un_nom_sans_accent_ne_declenche_aucun_repli_d_accent(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Le second appel ne coute rien au cas ordinaire, et le test le garde.

    « Jean Personne » ne portant pas d'accent, seuls deux appels partent : le
    nom complet, puis le nom de famille.
    """
    _source({})

    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

    demandes = [str(appel.request.url) for appel in respx.calls]
    assert len(demandes) == 2, "nom complet puis nom de famille, pas de repli d'accent"


@respx.mock
async def test_une_non_resolution_part_en_rejet_et_jamais_en_silence(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Jamais un joueur simplement absent des agregats.**

    Une sortie identique pour « pas trouve » et « rien a chercher » est le
    defaut caracteristique du projet.
    """
    _source({})

    identity, _, rejet = await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

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
    connait pas — plusieurs appels par joueur et par passe, pour un constat qui
    ne bougera pas.
    """
    _source({})

    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)
    appels = len(respx.calls)
    await serve_stats.resolve(tennis_client, "Jean Personne", "atp", migrated)

    assert len(respx.calls) == appels
    assert serve_stats.load_identity("Jean Personne", "atp", migrated) is not None


@respx.mock
async def test_le_niveau_de_repli_se_compte(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**C'est la forme qui sert**, et pas une ligne de journal par resolution.

    Si `accents` devient majoritaire, la normalisation en amont est mauvaise.
    Un compte le dit ; un mois de logs que personne ne relit, jamais.
    """
    _source(
        {
            "Alexander Zverev": ["Alexander Zverev"],
            "McCartney Kessler": ["Mccartney Kessler"],
        },
        {
            "Alexander Zverev": [_un_match("Alexander Zverev")],
            "Mccartney Kessler": [_un_match("Mccartney Kessler")],
        },
    )

    await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)
    await serve_stats.resolve(tennis_client, "McCartney Kessler", "wta", migrated)

    assert serve_stats.fallback_tally(migrated) == {EXACT: 1, CASSE: 1}


@respx.mock
async def test_l_identifiant_numerique_est_capture_a_la_resolution(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**C'est le vrai identifiant, et il n'arrive qu'avec le profil.**

    La recherche ne rend que des noms. Regle de revue du projet : avant
    d'ecrire une comparaison de chaines, chercher l'identifiant — et quand il
    arrive, l'ecrire.
    """
    _source(
        {"Alexander Zverev": ["Alexander Zverev"]},
        {"Alexander Zverev": [_un_match("Alexander Zverev")]},
    )

    await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)

    identity = serve_stats.load_identity("Alexander Zverev", "atp", migrated)
    assert identity is not None
    assert identity.provider_id == 1


@respx.mock
async def test_la_resolution_garde_la_reponse_dont_elle_sort(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Sans elle, on saurait qu'un nom a ete resolu et jamais sur quoi."""
    _source(
        {"Alexander Zverev": ["Alexander Zverev"]},
        {"Alexander Zverev": [_un_match("Alexander Zverev")]},
    )

    identity, _, _ = await serve_stats.resolve(tennis_client, "Alexander Zverev", "atp", migrated)

    assert identity.response_id
    assert api_archive.load(identity.response_id, migrated) is not None


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


# -- La timeline, et les trois formes du silence -----------------------------


def test_l_alternance_tient_sur_la_timeline_reelle(load_fixture: Callable[[str], Any]) -> None:
    """**L'invariant de controle, sur le Fritz – Michelsen du 16/08.**

    19 jeux, et 6-3 6-4 en fait bien 19. Le serveur se deduit — on ne breake que
    le service adverse — et la suite deduite doit alterner.
    """
    ligne, motif = serve_stats.parse_timeline(load_fixture("tennisapi_event.json"), "Taylor Fritz")

    assert motif == ""
    assert ligne is not None
    assert ligne.served + ligne.returned == 19
    assert (ligne.served, ligne.held) == (10, 9)
    assert (ligne.returned, ligne.broke) == (9, 3)


def test_la_timeline_se_lit_des_deux_cotes(load_fixture: Callable[[str], Any]) -> None:
    """Les deux camps sortent de la meme reponse, et leurs comptes se completent."""
    payload = load_fixture("tennisapi_event.json")
    fritz, _ = serve_stats.parse_timeline(payload, "Taylor Fritz")
    michelsen, _ = serve_stats.parse_timeline(payload, "Alex Michelsen")

    assert fritz is not None and michelsen is not None
    assert fritz.served == michelsen.returned
    assert michelsen.served == fritz.returned
    assert fritz.served - fritz.held == michelsen.broke


def test_une_timeline_a_trous_est_refusee_et_non_moyennee() -> None:
    """**Sa rupture signale une timeline incomplete**, et une moyenne calculee
    dessus serait fausse sans que rien ne le montre."""
    troue = {
        "result": {
            "participant1": "A",
            "participant2": "B",
            "timeline": [
                {"text": "Game 1 - A - holds to 15"},
                {"text": "Game 2 - A - holds to 15"},
            ],
        }
    }

    ligne, motif = serve_stats.parse_timeline(troue, "A")

    assert ligne is None
    assert motif == "alternance"


def test_un_result_vide_n_est_pas_une_absence_de_donnees() -> None:
    """**`"success": true` sur un `result` vide** : le defaut caracteristique du
    projet, dans la source candidate cette fois."""
    ligne, motif = serve_stats.parse_timeline({"success": True, "result": []}, "A")

    assert (ligne, motif) == (None, "vide")


@respx.mock
async def test_le_jour_annonce_est_essaye_en_premier(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """Cinq des huit rencontres mesurees repondent au premier appel : le cas
    ordinaire ne paie pas les six essais possibles."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )

    trouve, rejet = await serve_stats.fetch_timeline(
        tennis_client, "Taylor Fritz", "Alex Michelsen", "2026-08-16", "Taylor Fritz"
    )

    assert rejet is None
    assert trouve is not None
    assert (trouve.shift, trouve.swapped) == (0, False)
    assert len(respx.calls) == 1


@respx.mock
async def test_la_fenetre_d_un_jour_rattrape_un_decalage_de_date(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**Cas mesure** : le Fernandez – Wang programme chez nous le 16/08 a 19h10
    UTC est date du **17** par la source. Une date exacte le manquerait."""
    evenement = load_fixture("tennisapi_event.json")

    def _repondre(request: httpx.Request) -> httpx.Response:
        corps = evenement if request.url.path.endswith("2026-08-17") else {"result": []}
        return httpx.Response(200, json=corps, headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)

    trouve, _ = await serve_stats.fetch_timeline(
        tennis_client, "Taylor Fritz", "Alex Michelsen", "2026-08-16", "Taylor Fritz"
    )

    assert trouve is not None
    assert trouve.shift == 1


@respx.mock
async def test_les_deux_ordres_sont_essayes(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**L'endpoint est positionnel**, et l'ordre ne correspond pas toujours a
    celui de la base."""
    evenement = load_fixture("tennisapi_event.json")

    def _repondre(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        inverse = "/Alex Michelsen/Taylor Fritz/" in unquote(request.url.path)
        return httpx.Response(
            200, json=evenement if inverse else {"result": []}, headers=QUOTA_HEADERS
        )

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)

    trouve, _ = await serve_stats.fetch_timeline(
        tennis_client, "Taylor Fritz", "Alex Michelsen", "2026-08-16", "Taylor Fritz"
    )

    assert trouve is not None
    assert trouve.swapped is True


@respx.mock
async def test_une_source_muette_part_en_rejet_nomme(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Trois rencontres sur huit restent vides**, graphies canoniques et
    fenetre epuisees. C'est un fait sur la source, pas un defaut de collecte, et
    le taire ferait passer une absence de collecte pour une absence de fait.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json={"success": True, "result": []}, headers=QUOTA_HEADERS
        )
    )

    trouve, rejet = await serve_stats.fetch_timeline(
        tennis_client, "Coco Gauff", "Liudmila Samsonova", "2026-08-16", "Coco Gauff"
    )

    assert trouve is None
    assert rejet is not None
    assert rejet.reason == SOURCE_VIDE
    assert len(respx.calls) == 6, "les deux ordres sur les trois dates"


# -- Les agregats ------------------------------------------------------------


def _ligne(surface: str, points: int, premieres: int, aces: int = 0, **extra: int) -> Any:
    """Une ligne de match reduite a ce que la sommation lit."""
    return serve_stats.ServeLine(
        played_on=extra.pop("jour", "2026-08-01"),
        surface=surface,
        opponent="X",
        first_serve=premieres,
        first_serve_of=points,
        aces=aces,
        **extra,
    )


def test_la_sommation_ne_se_confond_pas_avec_une_moyenne_de_taux() -> None:
    """**Le jeu piege du brief : un abandon de trois jeux et un cinq sets.**

    L'abandon rentre 4 premieres sur 10 (40 %), le cinq sets 108 sur 180 (60 %).
    La moyenne des taux donne 50 % ; la sommation donne 112/190 = **58,9 %**,
    parce que le cinq sets pese ce qu'il vaut.

    Moyenner donnerait le meme poids aux deux, et fausserait le profil des
    joueurs a abandons — ceux que la ligne « Abandons » du bloc signale deja.
    """
    abandon = _ligne("Hard", points=10, premieres=4)
    cinq_sets = _ligne("Hard", points=180, premieres=108)

    agg = serve_stats.aggregate((abandon, cinq_sets), "J", "atp")

    par_sommation = agg.first_serve_pct
    par_moyenne = (4 / 10 + 108 / 180) / 2
    assert par_sommation is not None
    assert round(par_sommation, 4) == round(112 / 190, 4)
    assert abs(par_sommation - par_moyenne) > 0.08, "les deux methodes doivent differer ici"


def test_chaque_indicateur_a_son_denominateur() -> None:
    """**Les aces se rapportent aux points de service, les doubles fautes aux
    secondes balles.**

    Rapporter les aces aux matchs mesurerait la longueur des rencontres ; et un
    joueur qui rentre 75 % de premieres a mecaniquement moins d'occasions de
    commettre une double faute.
    """
    ligne = _ligne(
        "Hard",
        points=100,
        premieres=75,
        aces=10,
        double_faults=5,
        won_first=50,
        won_first_of=75,
        won_second=12,
        won_second_of=25,
        bp_converted=3,
        bp_converted_of=10,
    )

    agg = serve_stats.aggregate((ligne,), "J", "atp")

    assert agg.ace_pct == 10 / 100, "aces sur les points de service"
    assert agg.double_fault_pct == 5 / 25, "doubles fautes sur les secondes balles"
    assert agg.won_first_pct == 50 / 75
    assert agg.won_second_pct == 12 / 25
    assert agg.bp_pct == 3 / 10


def test_un_denominateur_nul_ne_rend_pas_zero_pour_cent() -> None:
    """**« Aucune balle de break jouee » et « aucune convertie » sont deux
    faits differents**, et rendre 0 % sur le premier decrirait un joueur qui
    rate tout."""
    agg = serve_stats.aggregate((_ligne("Hard", points=50, premieres=30),), "J", "atp")

    assert agg.bp_pct is None
    assert agg.hold_pct is None


def test_la_surface_filtre_avant_de_sommer() -> None:
    agg = serve_stats.aggregate(
        (_ligne("Hard", 100, 60), _ligne("Clay", 200, 100)), "J", "atp", surface="Hard"
    )

    assert (agg.matches, agg.first_serve_of) == (1, 100)


def test_l_as_of_est_le_dernier_match_compte() -> None:
    """**C'est le dernier match, pas la date du calcul.** Sans lui, une donnee
    vieille de six jours se lirait comme actuelle."""
    agg = serve_stats.aggregate(
        (
            _ligne("Hard", 100, 60, jour="2026-07-01"),
            _ligne("Hard", 100, 60, jour="2026-08-14"),
        ),
        "J",
        "atp",
    )

    assert agg.as_of == "2026-08-14"


def test_les_jeux_se_somment_a_part_des_points(
    load_fixture: Callable[[str], Any],
) -> None:
    """La timeline a **sa propre couverture**, partielle : le compte de matchs
    a jeux se tient donc separement de celui des points."""
    jeux, _ = serve_stats.parse_timeline(load_fixture("tennisapi_event.json"), "Taylor Fritz")
    assert jeux is not None

    agg = serve_stats.aggregate((_ligne("Hard", 100, 60),), "J", "atp", games=(jeux,))

    assert (agg.matches, agg.games_matches) == (1, 1)
    assert agg.hold_pct == 9 / 10
    assert agg.break_pct == 3 / 9


# -- Le seuil et son repli ---------------------------------------------------


def _pose(migrated: Settings, surface: str, points: int) -> None:
    serve_stats.store_aggregate(
        serve_stats.ServeAggregate(
            player="J",
            circuit="atp",
            surface=surface,
            first_serve_of=points,
            first_serve=points // 2,
            as_of="2026-08-16",
        ),
        migrated,
    )


def test_la_surface_demandee_est_rendue_quand_elle_porte_le_volume(
    migrated: Settings,
) -> None:
    _pose(migrated, "Hard", 900)

    agg = serve_stats.load_aggregate("J", "atp", "Hard", migrated)

    assert agg is not None
    assert agg.surface == "Hard"
    assert not agg.fell_back


def test_le_repli_toutes_surfaces_se_signale(migrated: Settings) -> None:
    """**Un taux de dur presente comme tel alors qu'il melange trois surfaces
    serait une affirmation fausse.**"""
    _pose(migrated, "Hard", 200)
    _pose(migrated, "", 900)

    agg = serve_stats.load_aggregate("J", "atp", "Hard", migrated)

    assert agg is not None
    assert agg.fell_back is True
    assert agg.surface == ""


def test_sous_le_seuil_des_deux_cotes_rien_n_est_rendu(migrated: Settings) -> None:
    """**Jamais une ligne partielle.**

    C'est exactement ce que le lot 3 a refuse au Match Charting Project : « une
    ligne Service a moitie vide est pire que pas de ligne, elle sera lue comme
    un fait ».
    """
    _pose(migrated, "Hard", 200)
    _pose(migrated, "", 300)

    assert serve_stats.load_aggregate("J", "atp", "Hard", migrated) is None


def test_le_seuil_est_en_points_de_service_et_non_en_matchs() -> None:
    """Deux matchs de cinq sets portent plus de points que six abandons : c'est
    le volume qui rend un taux lisible, pas le nombre de rencontres."""
    maigre = serve_stats.ServeAggregate(player="J", circuit="atp", matches=40, first_serve_of=399)
    dense = serve_stats.ServeAggregate(player="J", circuit="atp", matches=5, first_serve_of=400)

    assert not maigre.enough
    assert dense.enough


# -- Le rendu dans le bloc ---------------------------------------------------


def _agg(joueur: str, **extra: Any) -> serve_stats.ServeAggregate:
    """Un agregat plausible : 1 000 points de service, largement au-dessus du seuil."""
    defauts: dict[str, Any] = {
        "first_serve": 648,
        "first_serve_of": 1000,
        "aces": 81,
        "double_faults": 15,
        "won_first": 511,
        "won_first_of": 648,
        "won_second": 190,
        "won_second_of": 352,
        "bp_converted": 42,
        "bp_converted_of": 100,
        "return_points": 980,
        "return_won": 335,
        "served": 317,
        "held": 277,
        "returned": 310,
        "broke": 65,
        "games_matches": 30,
        "as_of": "2026-08-16",
    }
    defauts.setdefault("surface", "Hard")
    defauts.update(extra)
    return serve_stats.ServeAggregate(player=joueur, circuit="atp", **defauts)


def _drapeau(monkeypatch: pytest.MonkeyPatch, actif: bool) -> Settings:
    from myassistantbet.config import get_settings

    monkeypatch.setenv("SERVE_LINES_ENABLED", "1" if actif else "0")
    get_settings.cache_clear()
    return get_settings()


def test_le_drapeau_bas_ne_rend_aucune_ligne(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Defaut a faux, et c'est le point le plus important de la partie
    gabarit.**

    Le budget et ces lignes modifient tous deux ce que le modele produit ;
    livres le meme jour, leurs effets seraient indissociables et le
    `changelog_mesure` ne servirait a rien.
    """
    reglages = _drapeau(monkeypatch, False)
    serve_stats.store_aggregate(_agg("Taylor Fritz"), reglages)
    serve_stats.store_aggregate(_agg("Alex Michelsen"), reglages)

    assert serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages) == []


def test_les_quatre_lignes_sortent_quand_le_drapeau_est_haut(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    reglages = _drapeau(monkeypatch, True)
    serve_stats.store_aggregate(_agg("Taylor Fritz"), reglages)
    serve_stats.store_aggregate(_agg("Alex Michelsen", first_serve=610), reglages)

    lignes = serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages)

    assert [label for label, _ in lignes] == ["Service", "Retour", "Jeux", "Ecart"]


def test_l_as_of_est_obligatoire_sur_la_ligne_rendue(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Sans lui, une donnee vieille de six jours serait lue comme actuelle** —
    le defaut que `Fraicheur` existe pour corriger ailleurs."""
    reglages = _drapeau(monkeypatch, True)
    serve_stats.store_aggregate(_agg("Taylor Fritz"), reglages)
    serve_stats.store_aggregate(_agg("Alex Michelsen"), reglages)

    rendu = dict(serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages))

    assert "arretees au 16/08" in rendu["Service"]
    assert "pts de service" in rendu["Service"], "le denominateur voyage avec"
    assert "[tennis-api.com]" in rendu["Service"], "attribution de la source"


def test_un_seul_joueur_au_dessus_du_seuil_rend_non_disponible_pour_l_autre(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Jamais une ligne muette.** Le bloc porte l'autre joueur, et une moitie
    absente sans mention se lirait comme un oubli de collecte."""
    reglages = _drapeau(monkeypatch, True)
    serve_stats.store_aggregate(_agg("Taylor Fritz"), reglages)

    rendu = dict(serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages))

    assert "Taylor Fritz 64.8% 1re" in rendu["Service"]
    assert "Alex Michelsen non disponible" in rendu["Service"]
    assert "Ecart" not in rendu, "une soustraction demande les deux cotes"


def test_aucune_ligne_quand_les_deux_joueurs_manquent(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Quatre lignes de « non disponible » couteraient de la place pour ne rien
    dire — c'est la regle du bloc depuis toujours."""
    reglages = _drapeau(monkeypatch, True)

    assert serve_stats.serve_lines("A", "B", "atp", "hard", reglages) == []


def test_l_ecart_est_calcule_par_l_application(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**Ce que l'application peut trancher ne se delegue pas au modele** —
    meme regle que le cran de confiance et le comptage de la section C."""
    reglages = _drapeau(monkeypatch, True)
    serve_stats.store_aggregate(_agg("Taylor Fritz", first_serve=648), reglages)
    serve_stats.store_aggregate(_agg("Alex Michelsen", first_serve=610), reglages)

    rendu = dict(serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages))

    assert "service +3.8 pts sur la 1re balle pour Taylor Fritz" in rendu["Ecart"]
    assert "taux non ajustes du niveau d'adversaire" in rendu["Ecart"]


def test_le_repli_de_surface_se_dit_dans_la_ligne(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Un taux de dur qui melange trois surfaces sans le signaler serait une
    affirmation fausse."""
    reglages = _drapeau(monkeypatch, True)
    for joueur in ("Taylor Fritz", "Alex Michelsen"):
        serve_stats.store_aggregate(_agg(joueur, surface=""), reglages)

    rendu = dict(serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages))

    assert "toutes surfaces" in rendu["Service"]
    assert "surface repliee" in rendu["Service"]


def test_le_circuit_se_lit_dans_la_cle_et_jamais_dans_un_libelle() -> None:
    """Les epreuves masculine et feminine de Cincinnati portent des noms
    differents : seule la cle les distingue."""
    assert serve_stats.circuit_of("tennis_atp_cincinnati_open") == "atp"
    assert serve_stats.circuit_of("tennis_wta_cincinnati_open") == "wta"
    assert serve_stats.circuit_of("soccer_epl") == ""


def test_chaque_libelle_rendu_a_son_pictogramme() -> None:
    """**Toute ligne ajoutee a un bloc entre dans `CONTEXT_ICONS` le meme jour.**

    Huit libelles ont deja sorti sans pictogramme, releves en rendant 250
    evenements reels : la colonne se vidait sans rien dire.
    """
    from myassistantbet.services.labels import CONTEXT_ICONS

    assert {"Service", "Retour", "Jeux", "Ecart"} <= set(CONTEXT_ICONS)


# -- La synchronisation ------------------------------------------------------


def _profils(joueurs: dict[str, int]) -> None:
    """Simule les deux endpoints pour une liste de joueurs et leur nombre de matchs."""

    def _repondre(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        chemin = unquote(request.url.path)
        nom = chemin.rsplit("/", 2)[-2]
        if "/matches-played" in chemin:
            matchs = [_un_match(nom) for _ in range(joueurs.get(nom, 0))]
            return httpx.Response(
                200, json={"singles": matchs, "singlesCount": len(matchs)}, headers=QUOTA_HEADERS
            )
        return httpx.Response(200, json=[nom] if nom in joueurs else [], headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)


@respx.mock
async def test_la_passe_ecrit_les_agregats_toutes_surfaces_et_par_surface(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Une seule reponse alimente toutes les fenetres** : la surface est dans
    la meme charge utile, donc aucun appel de plus."""
    _profils({"Taylor Fritz": 3})

    rapport = await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)

    assert rapport.refreshed == 1
    surfaces = {
        row["surface"] for row in query("SELECT surface FROM player_serve_agg", settings=migrated)
    }
    assert surfaces == {"", "Hard"}


@respx.mock
async def test_un_agregat_frais_n_est_pas_redemande(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**C'est ce qui rend la reprise reprenable** : relancer une passe
    interrompue ne repaie que ce qui manquait, pas toute la liste."""
    _profils({"Taylor Fritz": 3})

    await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)
    appels = len(respx.calls)
    seconde = await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)

    assert seconde.skipped == 1
    assert seconde.refreshed == 0
    assert len(respx.calls) == appels, "rien n'est redemande"


@respx.mock
async def test_la_passe_s_arrete_proprement_sur_le_plancher(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Le plancher se verifie avant chaque joueur, pas une fois au depart.**

    Un controle unique laisserait une reprise de 180 joueurs franchir le
    plancher en cours de route et le decouvrir a la fin — trop tard, le quota
    etant mensuel.
    """

    def _repondre(request: httpx.Request) -> httpx.Response:
        return httpx.Response(
            200,
            json=[],
            headers={"x-ratelimit-requests-remaining": "10"},
        )

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)

    rapport = await serve_stats.sync(
        tennis_client, [("A", "atp"), ("B", "atp"), ("C", "atp")], migrated
    )

    assert rapport.stopped is True
    assert "mensuel" in rapport.rejects[-1].detail
    assert rapport.line.endswith("passe arretee sur le plancher de quota")


@respx.mock
async def test_une_passe_complete_se_distingue_d_une_passe_arretee(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """Sans cette distinction, une reprise a moitie faite ressemblerait a une
    reprise finie."""
    _profils({"Taylor Fritz": 3})

    rapport = await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)

    assert rapport.stopped is False
    assert "complete" in rapport.line


@respx.mock
async def test_un_joueur_non_resolu_part_en_rejet_et_la_passe_continue(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Jamais un joueur simplement absent des agregats**, et jamais une passe
    qui tombe sur un nom."""
    _profils({"Taylor Fritz": 3})

    rapport = await serve_stats.sync(
        tennis_client, [("Inconnu Total", "atp"), ("Taylor Fritz", "atp")], migrated
    )

    assert rapport.refreshed == 1
    assert any(r.reason == MATCH_REF_UNRESOLVED for r in rapport.rejects)


@respx.mock
async def test_la_garde_de_peremption_s_applique_a_cette_source(
    tennis_client: TennisAPIClient, migrated: Settings
) -> None:
    """**Une source payante qui repond encore et n'avance plus** est le meme
    defaut qu'un classeur hebdomadaire fige : elle rend 200, les memes matchs,
    indefiniment.
    """
    from myassistantbet.services import freshness

    _profils({"Taylor Fritz": 3})
    await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)

    etat = freshness.state("tennisapi", "atp", migrated)
    assert etat.source_as_of == "2026-08-16", "le dernier match obtenu date la source"


def test_l_entretien_ne_prend_que_les_matchs_a_venir(migrated: Settings) -> None:
    """Un lot tennis porte trente-cinq joueurs : passer tout le catalogue tous
    les jours couterait cent-quatre-vingts appels pour rien."""
    _event(migrated, "tennis_atp_us_open", "A", "B", "2099-01-01T12:00:00Z")
    _event(migrated, "tennis_wta_us_open", "C", "D", "2020-01-01T12:00:00Z")

    a_venir = serve_stats.upcoming_players(migrated)
    tous = serve_stats.known_players(migrated)

    assert set(a_venir) == {("A", "atp"), ("B", "atp")}
    assert set(tous) == {("A", "atp"), ("B", "atp"), ("C", "wta"), ("D", "wta")}


def _event(settings: Settings, cle: str, home: str, away: str, quand: str) -> None:
    from myassistantbet import db

    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=settings)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = ?", (cle,), settings=settings
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, ?, ?, ?, ?, 'api', ?)",
        (sport["id"], competition["id"], f"{home}{away}", home, away, quand, db.utcnow()),
        settings=settings,
    )


# -- La collecte des timelines : quand s'arreter -----------------------------
#
# `fetch_timeline` savait chercher une rencontre depuis le lot 5 et **rien ne
# l'appelait** : les 176 lignes de la base portaient `served = 0`, donc la ligne
# `Jeux` s'omettait partout. Ces tests portent sur le chainon manquant, et sur la
# seule chose qu'il decide vraiment — quand cesser d'appeler.


def _ligne_de_match(jour: str, adversaire: str = "Alex Michelsen") -> serve_stats.ServeLine:
    return serve_stats.ServeLine(played_on=jour, surface="Hard", opponent=adversaire)


@respx.mock
async def test_la_collecte_s_arrete_des_le_seuil_atteint(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**Le dessin entier du §1.** Une quinzaine de rencontres porte les 300 jeux
    que `enough_games` reclame ; 52 semaines en comptent quarante. S'arreter au
    seuil divise la passe par deux ou trois, et au-dela un appel de plus
    n'ajoute rien qu'un taux ne dise deja.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    lignes = tuple(_ligne_de_match(f"2026-0{1 + i // 28}-{1 + i % 28:02d}") for i in range(40))

    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client, "Taylor Fritz", lignes, tennis_client._settings
    )

    porte = sum(jeu.served + jeu.returned for jeu in jeux)
    assert releve.reached, "le seuil est atteint, donc la collecte s'arrete d'elle-meme"
    assert porte >= serve_stats.MIN_GAMES
    assert releve.attempted < len(lignes), "les quarante rencontres ne sont pas toutes payees"


@respx.mock
async def test_les_rencontres_recentes_passent_en_premier(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """Une interruption doit laisser la fenetre **la plus proche du match
    analyse**, jamais un fond de saison."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    lignes = (_ligne_de_match("2026-01-05"), _ligne_de_match("2026-08-16"))

    await serve_stats.collect_games(
        tennis_client, "Taylor Fritz", lignes, tennis_client._settings, target=1
    )

    assert "2026-08-16" in str(respx.calls[0].request.url)


@respx.mock
async def test_une_source_vide_se_compte_a_part_d_une_rupture_d_alternance(
    tennis_client: TennisAPIClient,
) -> None:
    """**Quatre taux et non un.** Une couverture partielle et une timeline a
    trous n'appellent pas le meme comportement : la premiere est un fait sur la
    source, la seconde un rejet de qualite. Les fondre ferait chercher une panne
    la ou il n'y a qu'une absence."""
    troue = {
        "result": {
            "participant1": "A",
            "participant2": "Troue",
            "timeline": [
                {"text": "Game 1 - A - holds to 15"},
                {"text": "Game 2 - A - holds to 15"},
            ],
        }
    }

    def _repondre(request: httpx.Request) -> httpx.Response:
        corps = troue if "Troue" in str(request.url) else {"result": []}
        return httpx.Response(200, json=corps, headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)
    lignes = (_ligne_de_match("2026-08-16", "Troue"), _ligne_de_match("2026-08-15", "Vide"))

    jeux, releve, rejets = await serve_stats.collect_games(
        tennis_client, "A", lignes, tennis_client._settings
    )

    assert jeux == ()
    assert (releve.alternation, releve.empty) == (1, 1)
    assert not releve.reached, "aucun jeu collecte : la ligne Jeux doit s'omettre"
    assert len(rejets) == 2


@respx.mock
async def test_une_reprise_ne_redemande_pas_ce_qui_est_deja_archive(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """C'est ce qui rend la passe **reprenable** : une interruption ne repaie pas
    ce qui est en base. Sans cela, une reprise de 256 joueurs coupee a mi-chemin
    recommencerait a zero."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    lignes = (_ligne_de_match("2026-08-16"),)
    await serve_stats.collect_games(
        tennis_client, "Taylor Fritz", lignes, tennis_client._settings, target=1
    )
    appels = len(respx.calls)

    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client, "Taylor Fritz", lignes, tennis_client._settings, target=1
    )

    assert len(respx.calls) == appels, "la seconde passe ne paie aucun appel"
    assert releve.replayed == 1
    assert len(jeux) == 1, "et elle rend quand meme les jeux"


@respx.mock
async def test_une_rencontre_vide_deja_archivee_ne_se_repaie_pas(
    tennis_client: TennisAPIClient,
) -> None:
    """**Un `result` vide archive compte comme vu.** La moitie des rencontres
    mesurees ne sont pas servies : les redemander a chaque reprise paierait
    six appels pour reentendre la meme reponse."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(200, json={"result": []}, headers=QUOTA_HEADERS)
    )
    lignes = (_ligne_de_match("2026-08-16"),)
    await serve_stats.collect_games(tennis_client, "A", lignes, tennis_client._settings)
    appels = len(respx.calls)

    _, releve, _ = await serve_stats.collect_games(
        tennis_client, "A", lignes, tennis_client._settings
    )

    assert len(respx.calls) == appels
    assert (releve.empty, releve.replayed) == (1, 1)
