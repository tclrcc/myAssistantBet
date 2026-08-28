"""Statistiques de service : identite des joueurs, et lecture d'une reponse.

**Le piege numero un de cette source est le nom**, et le lot 4 l'a paye deux
fois : l'API ecrit « Mccartney Kessler » quand la base ecrit « McCartney
Kessler », et une comparaison stricte a rendu « 0 point de service » — un faux
negatif de notre rapprochement, pas de la source.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import httpx
import pytest
import respx

from myassistantbet.config import Settings
from myassistantbet.db import execute, query, query_one, utcnow
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
    """**La perte assumee du retrait de `J+1`, ecrite plutot que masquee.**

    Le cas est reel et documente au lot 5 : le Fernandez – Wang programme chez
    nous le 16/08 a **19h10 UTC** est date du **17** par la source — un match de
    fin de soiree bascule au lendemain chez un fournisseur qui date plus a l'est.
    Le mecanisme est causal, pas accidentel.

    `J+1` a pourtant ete retire le 19/08, sur une mesure d'archive : **zero**
    aboutissement en 564 rencontres, quand `J+0` en rend 106 et `J-1` sept. Les
    deux constats sont vrais et ne portent pas sur la meme population — l'archive
    ne contient pas ce match-la, et elle ne connait pas les heures de coup
    d'envoi, donc elle ne peut pas dire si elle contient des matchs de fin de
    soiree.

    **Ce test garde donc la trace de ce qu'on a decide de perdre.** Il echouera
    le jour ou `J+1` reviendra, et c'est bien : la question se rouvre si des
    matchs tardifs se mettent a manquer.
    """
    evenement = load_fixture("tennisapi_event.json")

    def _repondre(request: httpx.Request) -> httpx.Response:
        corps = evenement if request.url.path.endswith("2026-08-17") else {"result": []}
        return httpx.Response(200, json=corps, headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)

    trouve, rejet = await serve_stats.fetch_timeline(
        tennis_client, "Taylor Fritz", "Alex Michelsen", "2026-08-16", "Taylor Fritz"
    )

    assert 1 not in serve_stats.DAY_SHIFTS, "J+1 est retire — ce test decrit sa perte"
    assert trouve is None, "un match date du lendemain n'est plus rattrape"
    assert rejet is not None and rejet.reason == SOURCE_VIDE


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
    assert len(respx.calls) == 4, "les deux ordres sur les deux dates restantes (J+0, J-1)"


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
    # L'ecart porte sur les points **gagnes** derriere la premiere balle depuis
    # le lot 18 : deux joueurs qui ne different que par leur taux de mise en jeu
    # ne produisent plus de ligne, et c'est le comportement voulu.
    serve_stats.store_aggregate(_agg("Alex Michelsen", won_first=400), reglages)

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
    meme regle que le cran de confiance et le comptage de la section C.

    La grandeur confrontee a change au lot 18 : le taux de premieres balles
    **mises en jeu** disait qui sert ainsi, pas qui en tire quelque chose. Le
    test suit la decision, pas la sortie — c'est un changement de fond.
    """
    reglages = _drapeau(monkeypatch, True)
    serve_stats.store_aggregate(_agg("Taylor Fritz", won_first=648), reglages)
    serve_stats.store_aggregate(_agg("Alex Michelsen", won_first=400), reglages)

    rendu = dict(serve_stats.serve_lines("Taylor Fritz", "Alex Michelsen", "atp", "hard", reglages))

    assert "s/1re " in rendu["Ecart"] and "pour Taylor Fritz" in rendu["Ecart"]
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

    # **L'horloge est donnee**, sinon ce test mesurerait le jour ou il tourne :
    # ses quarante rencontres sont datees de janvier et fevrier, donc hors de la
    # fenetre de retention des le mois de mai. Ce qu'il enonce est que le seuil
    # arrete la collecte, pas que le calendrier la laisse partir.
    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client,
        "Taylor Fritz",
        lignes,
        tennis_client._settings,
        now=datetime(2026, 3, 1, tzinfo=UTC),
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


# -- Le drapeau, des deux cotes ---------------------------------------------


def test_drapeau_bas_le_bloc_et_le_gabarit_sont_ceux_d_avant_le_lot(
    migrated: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    """**La garantie que l'activation reste une decision.**

    Les tests voisins portent sur `serve_lines` seule ; celui-ci porte sur le
    **prompt entier**, parce que c'est lui qui part a l'analyse. Le drapeau bas,
    le rendu doit etre **strictement** celui d'une installation qui n'a jamais
    collecte : ni ligne, ni mode d'emploi, ni ligne blanche de plus laissee par
    une porte de preambule qui se serait ouverte pour rien.

    La reference est un rendu produit **sans aucun agregat en base** — c'est
    l'etat d'avant le lot, et le seul qu'on puisse comparer sans recopier une
    sortie du jour dans une assertion.
    """
    from myassistantbet.services import board as board_service
    from myassistantbet.services.manual import build, save
    from myassistantbet.services.prompt import build_prompt

    event_id = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Taylor Fritz",
            "Alex Michelsen",
            "2026-08-04",
            "20:45",
            "Taylor Fritz 1.85\nAlex Michelsen 1.95",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    # Le circuit se lit sur la cle du fournisseur et la surface se saisit : sans
    # les deux, `serve_lines` rend une liste vide quel que soit le drapeau, et
    # le test passerait pour la mauvaise raison.
    execute(
        "UPDATE competitions SET oddsapi_key = 'tennis_atp_gstaad', surface = 'hard' "
        " WHERE id = (SELECT competition_id FROM events WHERE id = ?)",
        (event_id,),
        settings=migrated,
    )

    # 1. L'avant-lot : rien en base, donc rien a rendre.
    avant = build_prompt(session_id, settings=_drapeau(monkeypatch, False)).body

    # 2. Les agregats arrivent, drapeau toujours bas.
    bas = _drapeau(monkeypatch, False)
    serve_stats.store_aggregate(_agg("Taylor Fritz"), bas)
    serve_stats.store_aggregate(_agg("Alex Michelsen"), bas)
    apres = build_prompt(session_id, settings=bas).body

    assert apres == avant, (
        "drapeau bas, la collecte ne doit rien changer au prompt — "
        "sinon le changelog ne pourrait pas separer le budget des lignes de service"
    )
    assert "tennis-api.com" not in apres, "aucune attribution de source non plus"

    # Le cote **haut** du drapeau est verifie par
    # `test_les_quatre_lignes_sortent_quand_le_drapeau_est_haut`, au niveau de
    # `serve_lines` : c'est la que la decision se prend. Le verifier ici
    # demanderait de monter tout le chemin de contexte d'un match de tennis pour
    # reverifier la meme branche.


# -- Le filtre d'age : une falaise, pas une decroissance ---------------------


@respx.mock
async def test_une_rencontre_de_plus_de_90_jours_n_est_pas_tentee(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**Le levier du lot 8, et il est mesure.** 387 rencontres tentees au-dela
    de 90 jours dans l'archive, **zero timeline** ; la tranche precedente en
    sert encore 57 %. Ce n'est pas une decroissance, c'est une falaise, et
    demander au-dela paie six appels pour une reponse connue d'avance.

    Le compte sort a part (`too_old`) : fondu dans `empty`, il ferait lire une
    couverture qui s'effondre la ou il n'y a qu'un filtre qui travaille.
    """
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    maintenant = datetime(2026, 8, 19, tzinfo=UTC)
    vieille = _ligne_de_match("2026-05-01", "Vieux")  # 110 jours

    jeux, releve, rejets = await serve_stats.collect_games(
        tennis_client, "A", (vieille,), tennis_client._settings, now=maintenant
    )

    assert respx.calls.call_count == 0, "aucun appel n'est emis au-dela de la fenetre"
    assert (releve.too_old, releve.attempted, releve.empty) == (1, 0, 0)
    assert jeux == () and rejets == [], "ce n'est pas un echec de source : rien n'a ete demande"


@respx.mock
async def test_une_rencontre_de_80_jours_est_toujours_tentee(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**La borne se prend du cote qui ne perd pas de donnee.** 80 jours est
    l'age maximum d'une rencontre reellement servie dans l'archive : le filtre
    doit la laisser passer, sans quoi il couterait la timeline qu'il devait
    epargner."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    maintenant = datetime(2026, 8, 19, tzinfo=UTC)
    recente = _ligne_de_match("2026-05-31", "Recent")  # 80 jours

    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client, "Taylor Fritz", (recente,), tennis_client._settings, now=maintenant
    )

    assert respx.calls.call_count >= 1
    assert (releve.too_old, releve.attempted) == (0, 1)
    assert len(jeux) == 1


@respx.mock
async def test_une_timeline_deja_archivee_survit_au_filtre_d_age(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """**L'archive passe avant le filtre, et l'ordre porte la regle.** Une
    timeline deja payee se relit gratuitement quel que soit l'age de la
    rencontre : l'ecarter perdrait une donnee qu'on possede pour economiser un
    appel qu'on ne ferait pas."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    ligne = _ligne_de_match("2026-05-01", "Vieux")
    # Payee alors qu'elle etait encore dans la fenetre.
    await serve_stats.collect_games(
        tennis_client,
        "Taylor Fritz",
        (ligne,),
        tennis_client._settings,
        target=1,
        now=datetime(2026, 5, 2, tzinfo=UTC),
    )
    appels = respx.calls.call_count

    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client,
        "Taylor Fritz",
        (ligne,),
        tennis_client._settings,
        target=1,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert respx.calls.call_count == appels, "rien n'est repaye"
    assert len(jeux) == 1, "et la timeline archivee sort quand meme"
    assert releve.too_old == 0


@respx.mock
async def test_le_filtre_d_age_se_desactive_par_le_reglage(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """Le seuil est un **reglage**, pas une constante : c'est le seul moyen de
    rejouer la mesure le jour ou la retention de la source aura bouge. A zero,
    le filtre ne s'applique plus."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    reglages = tennis_client._settings.model_copy(update={"timeline_max_age_days": 0})

    _, releve, _ = await serve_stats.collect_games(
        tennis_client,
        "Taylor Fritz",
        (_ligne_de_match("2024-01-01", "Antique"),),
        reglages,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert (releve.too_old, releve.attempted) == (0, 1)


@respx.mock
async def test_une_rencontre_hors_fenetre_n_interrompt_pas_le_parcours(
    tennis_client: TennisAPIClient, load_fixture: Callable[[str], Any]
) -> None:
    """Les lignes sont triees par date, mais une seule date aberrante ne doit
    pas faire perdre le fond de liste : le filtre **saute**, il n'arrete pas."""
    respx.get(url__startswith=BASE_URL).mock(
        return_value=httpx.Response(
            200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
        )
    )
    lignes = (
        _ligne_de_match("2026-08-18", "Recent"),
        _ligne_de_match("2026-01-01", "Vieux"),
        _ligne_de_match("2026-08-10", "Autre"),
    )

    jeux, releve, _ = await serve_stats.collect_games(
        tennis_client,
        "Taylor Fritz",
        lignes,
        tennis_client._settings,
        now=datetime(2026, 8, 19, tzinfo=UTC),
    )

    assert releve.too_old == 1
    assert releve.attempted == 2, "les deux rencontres dans la fenetre sont bien tentees"
    assert len(jeux) == 2


# -- La file de la passe longue : trois etages -------------------------------


def test_la_file_de_reprise_range_les_lots_recents_avant_le_fond_de_catalogue(
    migrated: Settings,
) -> None:
    """**Une passe longue s'interrompt**, donc son ordre decide de ce qu'elle
    laisse derriere. Un joueur vu dans un lot recent reviendra — un tournoi dure
    une semaine ; un joueur vu une fois en juin n'a aucune raison de revenir.

    Le lot se lit sur `prompt_events`, ce qui est **parti a l'analyse**, jamais
    sur la shortlist : elle se vide a mesure qu'on decoche.
    """
    from myassistantbet import db
    from myassistantbet.timelines import players

    _event(migrated, "tennis_atp_us_open", "A venir 1", "A venir 2", "2099-01-01T12:00:00Z")
    _event(migrated, "tennis_wta_us_open", "Analyse 1", "Analyse 2", "2020-06-01T12:00:00Z")
    _event(migrated, "tennis_wta_us_open", "Oublie 1", "Oublie 2", "2020-01-01T12:00:00Z")

    session = db.execute(
        "INSERT INTO sessions (created_at) VALUES (?)", (db.utcnow(),), settings=migrated
    )
    prompt = db.execute(
        "INSERT INTO prompts (session_id, template_name, body, created_at) "
        "VALUES (?, 'session_default.md.j2', '', ?)",
        (session, db.utcnow()),
        settings=migrated,
    )
    analyse = db.query_one("SELECT id FROM events WHERE home = 'Analyse 1'", settings=migrated)
    db.execute(
        "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
        (prompt, analyse["id"]),
        settings=migrated,
    )

    file = players(migrated, limit=0)

    assert file[:2] == [("A venir 1", "atp"), ("A venir 2", "atp")]
    assert set(file[2:4]) == {("Analyse 1", "wta"), ("Analyse 2", "wta")}
    assert set(file[4:]) == {("Oublie 1", "wta"), ("Oublie 2", "wta")}


def test_une_limite_nulle_ne_borne_pas_la_file(migrated: Settings) -> None:
    """`--joueurs 0` est la passe longue. **C'est une borne de temps de mur, pas
    de quota** : le plancher garde le second, et les confondre ferait chercher
    un garde-fou de credit dans un nombre de joueurs."""
    from myassistantbet.timelines import players

    for index in range(6):
        _event(migrated, "tennis_atp_us_open", f"J{index}", f"K{index}", "2020-01-01T12:00:00Z")

    assert len(players(migrated, limit=0)) == 12
    assert len(players(migrated, limit=3)) == 3


@respx.mock
async def test_la_reprise_leve_la_peremption_de_l_agregat(
    tennis_client: TennisAPIClient, migrated: Settings, load_fixture: Callable[[str], Any]
) -> None:
    """**Deux fraicheurs, et ce n'en est pas une seule.** Un agregat ecrit ce
    matin ne dit rien des timelines, qui sont un second etage de collecte.

    Sans ce drapeau, une reprise lancee le lendemain d'un entretien sautait 221
    joueurs sur 250 et ne collectait rien — un passage complet dont la sortie
    est indiscernable d'un catalogue deja couvert.
    """
    _profils({"Taylor Fritz": 3})
    await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)

    sans = await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated)
    avec = await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated, force=True)

    assert (sans.skipped, sans.refreshed) == (1, 0)
    assert (avec.skipped, avec.refreshed) == (0, 1)


# -- La ligne `Ici` : le tournoi en cours ------------------------------------
#
# **Ce que `Parcours` ne peut pas dire.** Il nomme les adversaires et jamais les
# resultats, parce qu'il sort de nos propres scans, qui programment sans
# rapporter. Ici la source rapporte : le score est un fait.

#: **Les deux tables ferment l'invariant `totalPointsWon`**, sans quoi
#: `parse_matches_played` les ecarte : points de service gagnes + points de
#: retour gagnes doit valoir le total annonce, des deux cotes. C'est le seul
#: garde-fou qui rattache les colonnes adverses aux notres, et une fixture qui
#: ne le respecte pas testerait le rejet au lieu du rendu.
_STATS_1 = {
    "firstServe": 30,
    "firstServeOf": 50,
    "aces": 3,
    "doubleFaults": 2,
    "winningOnFirstServe": 22,
    "winningOnFirstServeOf": 30,
    "winningOnSecondServe": 10,
    "winningOnSecondServeOf": 20,
    "breakPointsConverted": 2,
    "breakPointsConvertedOf": 5,
    "totalPointsWon": 48,  # 22 + 10 + (48 - 20 - 12)
}
_STATS_2 = {
    "firstServe": 28,
    "firstServeOf": 48,
    "aces": 2,
    "doubleFaults": 3,
    "winningOnFirstServe": 20,
    "winningOnFirstServeOf": 28,
    "winningOnSecondServe": 12,
    "winningOnSecondServeOf": 20,
    "breakPointsConverted": 3,
    "breakPointsConvertedOf": 6,
    "totalPointsWon": 50,  # 20 + 12 + (50 - 22 - 10)
}


#: Le nom du tournoi chez le fournisseur de profils pour `tennis_atp_us_open`,
#: seede par la migration 069. Les fragments `Ici` ne se rendent que si le nom
#: porte par le match tombe dessus.
TOURNOI_DECLARE = "U.S. Open - New York"


def _match_source(gagnant: str, perdant: str, jour: str, score: str, tournoi: int = 900) -> dict:
    """Un match tel que la source le rend : **`player1` est le vainqueur**.

    Recoupe sur 12 049 rencontres contre `tennis_matches` : la position est juste
    a 99,98 %, quand le vainqueur deduit du nombre de sets ne l'est qu'a 98,85 %
    — 15 de ses 16 erreurs etant des abandons, ou celui qui menait a perdu.
    """
    return {
        "tournamentId": tournoi,
        "date": f"{jour}T12:00:00.000Z",
        "result": score,
        "player1": {"name": gagnant, "stats": dict(_STATS_1)},
        "player2": {"name": perdant, "stats": dict(_STATS_2)},
        # **Le nom du tournoi est celui que la table du lot 17 declare** pour
        # `tennis_atp_us_open`. Un libelle de fantaisie ferait tomber la garde de
        # corroboration et taire toute la ligne — ce qui est le comportement
        # voulu, mais pas ce que ces bancs mesurent.
        "tournament": {"id": tournoi, "name": TOURNOI_DECLARE, "court": {"name": "Hard"}},
    }


def _profil_tournoi(
    joueur: str,
    matchs: list[dict],
    settings: Settings,
    releve: str = "2026-08-18T06:00:00Z",
) -> None:
    """Archive une reponse `matches-played`. **Aucun appel** — c'est le contrat.

    `releve` est la date d'archivage. Elle a un defaut pour que les bancs
    anterieurs ne bougent pas d'une ligne, et elle se regle parce qu'une charge
    utile **anterieure au tournoi** ne dit pas la meme chose qu'une charge utile
    fraiche : voir les deux bancs du releve perime.
    """
    import json as _json

    from myassistantbet.providers.tennisapi import PREFIX, PROVIDER

    execute(
        "INSERT INTO api_responses (provider, endpoint, path, params, raw_json, sha256, "
        "  http_status, fetched_at) VALUES (?, 'profile/matches-played', ?, '{}', ?, ?, 200, ?)",
        (
            PROVIDER,
            f"{PREFIX}/profile/{joueur}/matches-played",
            _json.dumps({"singles": matchs}),
            f"sha-{joueur}",
            releve,
        ),
        settings=settings,
    )


def _tournoi(settings: Settings, jours: list[tuple[str, str, str]]) -> int:
    """Une edition de tournoi en base — c'est elle qui dit quel tournoi se joue."""
    for home, away, quand in jours:
        _event(settings, "tennis_atp_us_open", home, away, quand)
    row = db_query_one(settings)
    return int(row["id"])


def db_query_one(settings: Settings):
    from myassistantbet import db

    return db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'", settings=settings
    )


def _avec_ligne(settings: Settings) -> Settings:
    return settings.model_copy(update={"current_event_line_enabled": True})


def test_la_ligne_ici_rend_les_resultats_du_tournoi_en_cours(migrated: Settings) -> None:
    """**Le score est un fait, pas une deduction.** `Parcours` nomme les
    adversaires et s'arrete la, parce qu'il sort de nos scans, qui programment
    sans rapporter ; la source, elle, rapporte."""
    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    _profil_tournoi(
        "A",
        [
            _match_source("A", "X", "2026-08-14", "6-3 6-4"),
            _match_source("Y", "A", "2026-08-16", "7-6(5) 6-2"),
        ],
        migrated,
    )
    _profil_tournoi("B", [], migrated)

    lignes = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )

    assert [label for label, _ in lignes] == ["Ici"]
    valeur = lignes[0][1]
    assert "14/08 bat X 6-3 6-4" in valeur
    # **Le score se lit du point de vue du joueur nomme**, comme `H2H` et
    # `Aller` : la source l'ecrit du cote du vainqueur, et deux conventions dans
    # le meme bloc se liraient a l'envers.
    assert "16/08 perd contre Y 6-7(5) 2-6" in valeur
    assert "service ici" in valeur and "pts)" in valeur


def test_un_joueur_qui_entre_en_lice_le_dit(migrated: Settings) -> None:
    """**Jamais un silence.** Un joueur qui entre en lice est un fait sur le
    match — le fait dominant quand l'autre sort de trois tours — et un blanc se
    lirait comme un defaut de collecte.

    L'identifiant de tournoi lui vient de son adversaire : sans ce partage, un
    entrant n'aurait aucune reference et sa ligne se tairait la ou elle a le plus
    a dire.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-14", "6-3 6-4")], migrated)
    _profil_tournoi("B", [_match_source("B", "Z", "2026-08-05", "6-1 6-1", tournoi=700)], migrated)

    lignes = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )

    assert "B aucun match dans ce tournoi" in lignes[0][1]
    assert "Z" not in lignes[0][1], "le tournoi de la semaine passee n'est pas « ici »"


def test_un_forfait_ne_se_rend_pas_comme_un_match_gagne(migrated: Settings) -> None:
    """**Coherence avec `Non joue`.** Un match programme et non dispute ne doit
    pas apparaitre comme joue, et le sens du forfait est dit : un tour gagne sans
    jouer et un tour abandonne ne decrivent pas le meme joueur au tour suivant."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-16T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-16", "w/o")], migrated)
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert "16/08 forfait de X" in valeur
    assert "bat" not in valeur
    # Un tapis vert n'apporte aucun point : la statistique de service se tait.
    assert "service ici" not in valeur


def test_la_ligne_ici_porte_la_date_de_son_releve(migrated: Settings) -> None:
    """Sans elle, une liste qui s'arrete la veille se lit comme un parcours
    complet — constate sur le rendu reel du 19/08, ou un match joue apres le
    releve manquait sans qu'un mot le dise."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-16T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-16", "6-3 6-4")], migrated)
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert "[releve au 18/08]" in valeur


def test_la_ligne_ici_ne_sort_pas_tant_que_le_drapeau_est_bas(migrated: Settings) -> None:
    """Le drapeau est bas par defaut : la coupe budget/lignes de service est deja
    jointe au 18/08, et une troisieme variable a la meme date rendrait les trois
    effets indissociables."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-16T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-16", "6-3 6-4")], migrated)

    assert (
        serve_stats.here_lines("A", "B", "atp", competition, "2026-08-18T12:00:00Z", migrated) == []
    )


def test_la_ligne_ici_ne_demande_rien_au_reseau(migrated: Settings) -> None:
    """Le contrat du module : ce bloc se rend a chaque prompt et a chaque
    ouverture de fiche. **Aucune route n'est simulee** — le moindre appel ferait
    echouer ce test."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-16T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-16", "6-3 6-4")], migrated)
    _profil_tournoi("B", [], migrated)

    lignes = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )

    assert lignes and "bat X" in lignes[0][1]


def test_le_verbe_et_le_score_ne_peuvent_pas_se_contredire(migrated: Settings) -> None:
    """**L'invariant, plutot qu'une valeur.** Une selection lue a l'envers est
    l'erreur la plus couteuse qu'un bloc de tennis puisse produire : le test
    verifie que le camp annonce par le verbe est celui qui mene au score, quel
    que soit le cote depuis lequel la source ecrit."""
    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    _profil_tournoi(
        "A",
        [
            _match_source("A", "X", "2026-08-14", "6-3 6-4"),
            _match_source("Y", "A", "2026-08-16", "7-6(5) 6-2"),
        ],
        migrated,
    )
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    for morceau in valeur.split(" | "):
        if "bat" not in morceau and "perd contre" not in morceau:
            continue
        sets = [jeton for jeton in morceau.split() if serve_stats._SET.match(jeton) is not None]
        gagnes = sum(
            1
            for jeton in sets
            if int(serve_stats._SET.match(jeton).group(1))
            > int(serve_stats._SET.match(jeton).group(2))
        )
        perdus = len(sets) - gagnes
        if "perd contre" in morceau:
            assert perdus > gagnes, f"« {morceau} » annonce une defaite sur un score gagnant"
        else:
            assert gagnes > perdus, f"« {morceau} » annonce une victoire sur un score perdant"


# -- Les jeux atteignent-ils l'agregat par surface ? --------------------------
#
# **Le defaut le plus couteux du lot 8, et aucun test unitaire ne l'aurait vu.**
# Les quatre lignes de service sortaient, `Jeux` restait absente, et son absence
# est son comportement normal sous le seuil : l'echec avait exactement la meme
# sortie que le cas ordinaire.


def test_l_horodatage_d_une_timeline_est_un_entier_et_se_lit_comme_tel() -> None:
    """`startTimestamp` vaut `1786892400`, pas `2026-08-16`.

    Il etait lu `str(value)[:10]`, ce qui rend les dix premiers **chiffres** —
    une chaine qui ressemble a une date par sa longueur et n'en est pas une. La
    fixture porte la valeur reelle de la source : ce test aurait suffi.
    """
    assert serve_stats._from_epoch(1786892400) == "2026-08-16"
    assert serve_stats._from_epoch("pas un entier") == ""
    assert serve_stats._from_epoch(None) == ""


@respx.mock
async def test_les_jeux_atteignent_l_agregat_de_leur_surface(
    tennis_client: TennisAPIClient, migrated: Settings, load_fixture: Callable[[str], Any]
) -> None:
    """**La propriete qui manquait.** `serve_lines` est appele avec la surface du
    tournoi, donc c'est l'agregat par surface qu'il lit : des jeux qui n'y
    arrivent pas rendent la ligne `Jeux` inatteignable sur **tous** les blocs, y
    compris pour un joueur qui vient de passer le seuil de 300 jeux.

    Le rapprochement se faisait par la date, et il ne pouvait pas tomber. Il se
    fait desormais par la surface **portee** par le jeu, recopiee de la ligne de
    service qui a demande cette timeline — le seul endroit ou le couple est connu
    avec certitude, la timeline se trouvant parfois a J-1.
    """

    def _repondre(request: httpx.Request) -> httpx.Response:
        from urllib.parse import unquote

        chemin = unquote(request.url.path)
        if "/event/get/" in chemin:
            return httpx.Response(
                200, json=load_fixture("tennisapi_event.json"), headers=QUOTA_HEADERS
            )
        nom = chemin.rsplit("/", 2)[-2]
        if "/matches-played" in chemin:
            return httpx.Response(
                200, json={"singles": [_un_match(nom)], "singlesCount": 1}, headers=QUOTA_HEADERS
            )
        return httpx.Response(200, json=[nom], headers=QUOTA_HEADERS)

    respx.get(url__startswith=BASE_URL).mock(side_effect=_repondre)

    await serve_stats.sync(tennis_client, [("Taylor Fritz", "atp")], migrated, with_games=True)

    lignes = query(
        "SELECT surface, served, returned FROM player_serve_agg WHERE player = 'Taylor Fritz'",
        settings=migrated,
    )
    par_surface = {row["surface"]: row["served"] for row in lignes}
    assert par_surface[""] > 0, "l'agregat toutes surfaces porte les jeux"
    surfaces = [cle for cle in par_surface if cle]
    assert surfaces, "la fixture porte au moins une surface"
    assert any(par_surface[cle] > 0 for cle in surfaces), (
        "aucun agregat par surface ne porte de jeux : la ligne « Jeux » serait "
        "inatteignable sur tous les blocs, exactement le defaut du lot 8"
    )


def test_la_ligne_jeux_se_replie_sur_toutes_surfaces_et_le_dit(migrated: Settings) -> None:
    """**Le seuil de jeux ne s'atteint pas par surface, et c'est structurel.**

    `collect_games` s'arrete a `MIN_GAMES` jeux **toutes surfaces confondues** :
    aucun agregat par surface ne peut donc l'atteindre. Mesure sur la base
    servie : **zero ligne par surface au-dessus de 300**, maximum observe 225. La
    ligne etait inatteignable sur tout bloc portant une surface — c'est-a-dire
    sur tous, `competitions.surface` etant renseignee au tennis.

    Le choix n'est pas entre deux portees mais entre **une ligne repliee qui se
    declare** et pas de ligne du tout. Le seuil, lui, ne bouge pas.
    """
    serve_stats.store_aggregate(
        serve_stats.ServeAggregate(
            player="A",
            circuit="atp",
            surface="Hard",
            matches=20,
            first_serve=1200,
            first_serve_of=2000,
            won_first=900,
            won_first_of=1200,
            won_second=400,
            won_second_of=800,
            return_points=2000,
            return_won=800,
            games_matches=8,
            served=105,
            held=90,
            returned=104,
            broke=25,
            as_of="2026-08-12",
        ),
        migrated,
    )
    serve_stats.store_aggregate(
        serve_stats.ServeAggregate(
            player="A",
            circuit="atp",
            surface="",
            matches=30,
            first_serve=1800,
            first_serve_of=3000,
            won_first=1400,
            won_first_of=1800,
            won_second=600,
            won_second_of=1200,
            return_points=3000,
            return_won=1200,
            games_matches=15,
            served=160,
            held=140,
            returned=156,
            broke=40,
            as_of="2026-08-18",
        ),
        migrated,
    )
    avec = migrated.model_copy(update={"serve_lines_enabled": True})

    lignes = dict(serve_stats.serve_lines("A", "B", "atp", "hard", avec))

    assert "Jeux" in lignes, "la ligne sort, alors que l'agregat de surface est sous le seuil"
    assert "tenue" in lignes["Jeux"] and "160 jeux servis" in lignes["Jeux"]
    assert "toutes surfaces" in lignes["Jeux"], "le repli se declare, il ne se tait pas"
    # **La date est sur cette ligne**, les deux agregats pouvant differer.
    assert "arretees au 18/08" in lignes["Jeux"]
    assert "Hard" in lignes["Service"], "les points de service restent sur leur surface"


# -- §4 — la contradiction entre `Non joue` et `Ici` --------------------------
#
# **Le cas s'est produit, et `CLAUDE.md` l'annonçait comme impossible.** La
# déduction « un joueur ne dispute qu'une rencontre par journée de tournoi »
# portait la limite : *« un tableau retardé par la pluie peut faire jouer deux
# simples dans la même journée. Le cas ne s'observe pas en base. »* Relevé du
# 19/08/2026 — Xiyu Wang a joué Kudermetova puis Andreescu le 13/08, et le bloc
# annonçait « adversaire remplacé, non disputée » sur un match dont la source
# porte le score et les statistiques de service.


def test_la_source_compte_les_matchs_disputes_par_jour(migrated: Settings) -> None:
    """**Elle sert à démonter une prémisse, pas à nommer un adversaire.**

    C'est ce qui la rend utilisable : la source écrit « Bianca Vanessa
    Andreescu » où nos scans disent « Bianca Andreescu », donc `sort_key` ne
    tombe pas et un rapprochement souple serait le « en cas de doute on devine »
    que le projet refuse partout. Le **jour**, lui, est le même des deux côtés.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi(
        "A",
        [
            _match_source("A", "X", "2026-08-14", "6-3 6-4"),
            _match_source("A", "Z", "2026-08-14", "6-0 6-4"),
            _match_source("A", "Y", "2026-08-16", "6-2 6-2"),
        ],
        migrated,
    )

    compte = serve_stats.contested_days("A", competition, "2026-08-18T12:00:00Z", migrated)

    assert compte == {"2026-08-14": 2, "2026-08-16": 1}


def test_un_forfait_ne_compte_pas_comme_un_match_dispute(migrated: Settings) -> None:
    """Un tapis vert n'est pas un match joué — même règle qu'`Usure`, et c'est
    elle qui empêche la levée de s'appliquer à un vrai forfait."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    # La source encode le forfait dans le score (`w/o`), jamais dans un champ a
    # part : c'est `UNPLAYED_MARKS` qui le lit, et un test qui inventerait un
    # autre format testerait la fixture au lieu de la regle.
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-14", "w/o")], migrated)

    assert serve_stats.contested_days("A", competition, "2026-08-18T12:00:00Z", migrated) == {}


def test_la_source_muette_ne_leve_rien(migrated: Settings) -> None:
    """**Positif seulement.** Un jour absent de la réponse ne prouve rien : la
    source peut ne pas couvrir ce tournoi, ce joueur, ou n'avoir pas encore
    publié. On ne lève la déduction que là où elle **affirme**."""
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )

    assert serve_stats.contested_days("A", competition, "2026-08-18T12:00:00Z", migrated) == {}


# -- La couverture de la ligne `Ici` -----------------------------------------


def test_la_couverture_separe_les_trois_etats(migrated: Settings) -> None:
    """**Trois états et non deux**, parce qu'ils n'appellent pas la même lecture.

    Un bloc `renseigné` porte des résultats des deux côtés ; un bloc `partiel`
    en porte d'un côté et « aucun match dans ce tournoi » de l'autre — ce qui est
    un **fait sur le match**, souvent le fait dominant quand l'un sort de trois
    tours et l'autre entre en lice ; un bloc `absent` n'a pas de ligne du tout,
    et c'est le seul des trois qui décrive un manque de collecte.

    Les fondre ferait lire une entrée en lice comme un trou, exactement ce que
    la mention explicite existe pour éviter.
    """
    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
            ("C", "D", "2026-08-18T13:00:00Z"),
        ],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-14", "6-3 6-4")], migrated)
    _profil_tournoi("B", [], migrated)
    # `C` et `D` n'ont aucun profil archive : leur bloc n'aura pas de ligne.
    execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (utcnow(),),
        settings=migrated,
    )
    session_id = int(query_one("SELECT MAX(id) AS id FROM sessions", settings=migrated)["id"])
    execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', '', 0, ?)",
        (session_id, utcnow()),
        settings=migrated,
    )
    prompt_id = int(query_one("SELECT MAX(id) AS id FROM prompts", settings=migrated)["id"])
    for row in query(
        "SELECT id FROM events WHERE competition_id = ? ORDER BY id",
        (competition,),
        settings=migrated,
    ):
        execute(
            "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
            (prompt_id, int(row["id"])),
            settings=migrated,
        )

    couverture = serve_stats.here_coverage(migrated)

    assert len(couverture) == 1
    lot = couverture[0]
    assert (lot.month, lot.circuit) == ("2026-08", "atp")
    assert lot.blocks == 3
    # A – B : A a joué, B entre en lice -> partiel. A – X et C – D : absents,
    # X et C/D n'ayant aucun profil.
    assert (lot.partial, lot.absent) == (1, 2)
    assert lot.served == lot.filled + lot.partial + lot.stale


def test_un_releve_perime_ne_compte_pas_comme_bloc_renseigne(migrated: Settings) -> None:
    """**Compter un releve perime comme `renseigne` ferait passer une fenetre de
    collecte pour un bloc servi** — le defaut caracteristique du projet, pose sur
    la mesure elle-meme.

    Le quatrieme etat porte donc son compte, et `served` reste ce que son nom
    dit : les blocs qui portent une ligne, quelle qu'elle dise.
    """
    competition = _tournoi(
        migrated,
        [("B", "W", "2026-08-25T12:00:00Z"), ("A", "B", "2026-08-27T12:00:00Z")],
    )
    _profil_tournoi("A", [], migrated, releve="2026-08-19T06:00:00Z")
    _profil_tournoi("B", [_match_source("B", "W", "2026-08-25", "6-0 6-0")], migrated)
    execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (utcnow(),),
        settings=migrated,
    )
    session_id = int(query_one("SELECT MAX(id) AS id FROM sessions", settings=migrated)["id"])
    execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', '', 0, ?)",
        (session_id, utcnow()),
        settings=migrated,
    )
    prompt_id = int(query_one("SELECT MAX(id) AS id FROM prompts", settings=migrated)["id"])
    event = query_one(
        "SELECT id FROM events WHERE competition_id = ? AND home = 'A'",
        (competition,),
        settings=migrated,
    )
    execute(
        "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
        (prompt_id, int(event["id"])),
        settings=migrated,
    )

    lot = serve_stats.here_coverage(migrated)[0]

    assert (lot.stale, lot.filled, lot.partial) == (1, 0, 0)
    assert lot.served == 1


def test_la_couverture_se_mesure_hors_du_drapeau(migrated: Settings) -> None:
    """**Gardée par le drapeau, elle rendrait des zéros le jour où il redescend**
    — et ça se lirait comme une source tarie plutôt que comme une configuration.

    Le drapeau est forcé là, et nulle part ailleurs.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-14", "6-3 6-4")], migrated)
    _profil_tournoi("B", [], migrated)
    execute(
        "INSERT INTO sessions (label, created_at) VALUES ('essai', ?)",
        (utcnow(),),
        settings=migrated,
    )
    session_id = int(query_one("SELECT MAX(id) AS id FROM sessions", settings=migrated)["id"])
    execute(
        "INSERT INTO prompts (session_id, template_name, body, token_estimate, created_at) "
        "VALUES (?, 't.md.j2', '', 0, ?)",
        (session_id, utcnow()),
        settings=migrated,
    )
    prompt_id = int(query_one("SELECT MAX(id) AS id FROM prompts", settings=migrated)["id"])
    event = query_one(
        "SELECT id FROM events WHERE competition_id = ? AND away = 'B'",
        (competition,),
        settings=migrated,
    )
    execute(
        "INSERT INTO prompt_events (prompt_id, event_id) VALUES (?, ?)",
        (prompt_id, int(event["id"])),
        settings=migrated,
    )

    # `migrated` porte le drapeau bas : la ligne ne se rend pas...
    assert (
        serve_stats.here_lines("A", "B", "atp", competition, "2026-08-18T12:00:00Z", migrated) == []
    )
    # ...et la couverture le mesure quand même.
    assert serve_stats.here_coverage(migrated)[0].served == 1


def test_la_ligne_ici_nomme_ce_que_la_source_ne_couvre_pas(migrated: Settings) -> None:
    """**La soustraction est deterministe, donc elle ne se delegue pas.**

    `Parcours` nomme quatre adversaires, `Ici` en rapporte trois : le quatrieme
    est celui dont le bloc ne dit pas l'issue, et c'etait a l'analyse de le
    trouver en croisant trois lignes de tete. Mesure du 20/08/2026 sur le bloc
    Bejlek — Keys : le match manquant etait **Sabalenka**, jouee la veille.
    """
    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            ("A", "Z", "2026-08-17T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    _profil_tournoi(
        "A",
        [
            _match_source("A", "X", "2026-08-14", "6-3 6-4"),
            _match_source("A", "Y", "2026-08-16", "6-1 6-2"),
        ],
        migrated,
    )
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert f"1 match {serve_stats.HERE_UNCOVERED} : Z" in valeur
    # Ce que la source **couvre** n'y figure pas : la ligne dit ce qui manque,
    # elle ne recopie pas ce qui est juste au-dessus.
    assert "couvert : X" not in valeur and "couvert : Y" not in valeur


def test_un_parcours_entierement_non_couvert_ne_recopie_pas_parcours(migrated: Settings) -> None:
    """`(tout le Parcours)` — **le vocabulaire de `Fraicheur`, mot pour mot**.

    Deux formulations du meme fait se liraient comme deux faits, et le lecteur
    connait deja celle-la.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [], migrated)
    _profil_tournoi("B", [_match_source("B", "W", "2026-08-15", "6-0 6-0")], migrated)
    _event(migrated, "tennis_atp_us_open", "B", "W", "2026-08-15T12:00:00Z")

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert f"1 match {serve_stats.HERE_UNCOVERED} {serve_stats.WHOLE_PATH}" in valeur
    # Le joueur reste annonce comme entrant chez la source : les deux faits se
    # disent, ils ne se remplacent pas.
    assert serve_stats.HERE_NO_MATCH in valeur


def test_un_releve_anterieur_au_tournoi_n_affirme_aucun_match(migrated: Settings) -> None:
    """**Une absence qui mesure notre collecte se lisait comme un fait.**

    Cas reel, prompts 227 et 229 du 27/08/2026 : le bloc servait

        Ici    Diane Parry aucun match dans ce tournoi [releve au 19/08]

    quand le tournoi avait commence le 22/08 et que `Parcours` lui donnait deux
    adversaires. La charge utile datait de trois jours avant le premier match :
    elle ne **pouvait** contenir aucune rencontre de ce tournoi, et « aucun
    match » decrivait la fenetre de collecte, pas la joueuse.

    C'est le zero credible de l'instruction Tennis API, en production, sur la
    ligne qui a le plus d'autorite du bloc.

    **La cause est structurelle et non accidentelle** : la passe d'entretien lit
    `upcoming_players()` a heure fixe, et l'affiche du tour suivant n'existe
    qu'une fois le tour precedent fini — donc apres elle. Voir le chantier
    identifie non instruit.
    """
    competition = _tournoi(
        migrated,
        [("B", "W", "2026-08-25T12:00:00Z"), ("A", "B", "2026-08-27T12:00:00Z")],
    )
    _profil_tournoi("A", [], migrated, releve="2026-08-19T06:00:00Z")
    _profil_tournoi("B", [_match_source("B", "W", "2026-08-25", "6-0 6-0")], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-27T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert serve_stats.HERE_NO_INFO in valeur
    assert serve_stats.HERE_NO_MATCH not in valeur
    # La date reste dite : c'est elle qui rend l'affirmation verifiable.
    assert "[releve au 19/08]" in valeur


def test_un_releve_posterieur_au_premier_jour_affirme_toujours_aucun_match(
    migrated: Settings,
) -> None:
    """**Le libelle d'origine est juste dans son cas, et il ne bouge pas.**

    Aussi important que le banc precedent : sur les cinq fragments « aucun match
    dans ce tournoi » du corpus archive, **trois** portent un releve posterieur
    au premier jour connu du tournoi — Mertens, Chwalinska, Alexandrova, toutes
    avec zero match non couvert. La source a eu l'occasion de rapporter et n'a
    rien : c'est un fait sur la joueuse, souvent le fait dominant quand l'autre
    sort de trois tours.

    Elargir la correction a ces trois-la ferait perdre l'entree en lice, qui est
    exactement ce que `HERE_NO_MATCH` existe pour dire.
    """
    competition = _tournoi(
        migrated,
        [("B", "W", "2026-08-25T12:00:00Z"), ("A", "B", "2026-08-27T12:00:00Z")],
    )
    _profil_tournoi("A", [], migrated, releve="2026-08-26T06:00:00Z")
    _profil_tournoi("B", [_match_source("B", "W", "2026-08-25", "6-0 6-0")], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-27T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert serve_stats.HERE_NO_MATCH in valeur
    assert serve_stats.HERE_NO_INFO not in valeur


def test_ici_ne_rend_pas_le_tournoi_de_la_semaine_passee(migrated: Settings) -> None:
    """**Le mode sur la fenetre designait un autre tournoi.**

    Deux tournois se chevauchent une semaine sur deux, et notre fenetre
    d'edition contient la fin du precedent : un joueur qui entre en lice ici
    apres une demi-finale ailleurs voyait ses matchs de l'autre tournoi rendus
    sous le titre « ici ». Mesure du 20/08/2026 : **14 fragments sur 223**.

    La corroboration par nos propres scans ferme la porte — aucun match de
    l'autre tournoi ne porte un adversaire ni un jour que nous avons vus ici.
    """
    competition = _tournoi(
        migrated,
        [
            # Notre edition ouvre le 11 : la fin du tournoi precedent y tombe.
            ("C", "D", "2026-08-11T12:00:00Z"),
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    _profil_tournoi(
        "A",
        [
            # La semaine passee, ailleurs : trois matchs, donc le mode. Ils
            # tombent dans la fenetre de notre edition — c'est exactement ce que
            # la fenetre seule ne sait pas ecarter.
            _match_source("A", "P", "2026-08-11", "6-1 6-1", tournoi=901),
            _match_source("A", "Q", "2026-08-12", "6-2 6-2", tournoi=901),
            _match_source("A", "R", "2026-08-13", "6-3 6-3", tournoi=901),
            # Ici : un seul, et c'est celui-la qu'il faut.
            _match_source("A", "X", "2026-08-14", "7-5 6-4", tournoi=900),
        ],
        migrated,
    )
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert "bat X 7-5 6-4" in valeur
    for etranger in ("P", "Q", "R"):
        assert f"bat {etranger}" not in valeur


def test_deux_ecritures_du_meme_adversaire_ne_font_pas_un_match_non_couvert(
    migrated: Settings,
) -> None:
    """**Le sens de l'erreur commande la tolerance.**

    Nos scans ecrivent « Leylah Fernandez », la source « Leylah Annie
    Fernandez », et le jour differe d'un cran. L'egalite stricte declarait le
    match non couvert alors que son score figure sur la ligne juste au-dessus —
    une place de dossier envoyee chercher ce que le bloc rend deja. Mesure du
    20/08/2026 : trois fragments sur quinze etaient dans ce cas.
    """
    competition = _tournoi(
        migrated,
        [
            ("A", "Leylah Fernandez", "2026-08-16T12:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    _profil_tournoi(
        "A", [_match_source("A", "Leylah Annie Fernandez", "2026-08-17", "6-3 6-4")], migrated
    )
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert serve_stats.HERE_UNCOVERED not in valeur


def test_deux_freres_ne_se_confondent_pas(migrated: Settings) -> None:
    """La generosite s'arrete aux prenoms : `alexander` et `mischa` ne se
    prefixent pas, donc les Zverev restent deux joueurs."""
    assert serve_stats._same_player("Leylah Fernandez", "Leylah Annie Fernandez")
    assert serve_stats._same_player("Bianca Andreescu", "Bianca Vanessa Andreescu")
    assert not serve_stats._same_player("Alexander Zverev", "Mischa Zverev")
    assert not serve_stats._same_player("Alexander Zverev", "Alexander Bublik")


def test_la_phase_inconnue_dit_quand_meme_le_nombre_de_tours(migrated: Settings) -> None:
    """**Le tour ne se devine pas, le compte des tours s'etablit.**

    Sur le bloc du 20/08, `Tour` disait « phase non renseignee (118 joueurs
    vus) » quand `Parcours` et `Ici` etablissaient quatre tours d'un cote et
    trois de l'autre. Le gabarit fait de l'enjeu asymetrique une condition
    d'acces aux paliers hauts : entre une joueuse jamais allee au-dela du 3e
    tour ici et une lauréate de Grand Chelem en demie, il existe.
    """
    from myassistantbet.services import tennis_round

    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-14T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            ("B", "Z", "2026-08-16T14:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    valeur = tennis_round.lines(competition, "2026-08-18T12:00:00Z", migrated, "A", "B")[0][1]

    assert tennis_round.UNSET_ROUND in valeur, "la phase reste inconnue"
    assert f"{tennis_round.ROUNDS_PLAYED} 2 tours disputes par A, 1 par B" in valeur


def test_le_compte_de_tours_prend_le_plus_complet_des_deux_releves(
    migrated: Settings,
) -> None:
    """**Deux bornes inferieures du meme nombre, donc leur maximum.**

    `Tour` compte sur nos scans, bornes par leur fenetre ; `Ici` compte sur la
    source, bornee par la date de son releve. Aucune des deux n'est fausse — le
    mot « au moins » est exact des deux cotes — mais la plus basse se lisait
    seule, a quatre lignes d'une liste qui en portait le double.

    Mesure du 28/08/2026 sur les 31 blocs a phase inconnue du corpus : **5**
    voient l'asymetrie du bloc **retournee**, de « 1-1 » a « 2-1 » ou « 3-1 ».
    C'est la ligne dont le gabarit dit qu'elle « dit si l'enjeu est asymetrique »,
    et elle disait « egalite » la ou le bloc portait le double d'un cote.
    """
    from myassistantbet.services import tennis_round

    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-17T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    valeur = tennis_round.lines(
        competition, "2026-08-18T12:00:00Z", migrated, "A", "B", {"A": 3, "B": 1}
    )[0][1]

    assert f"{tennis_round.ROUNDS_PLAYED} 3 tours disputes par A, 1 par B" in valeur


def test_le_compte_de_tours_ne_baisse_jamais_sur_le_releve_de_la_source(
    migrated: Settings,
) -> None:
    """**Le maximum, et jamais le compte de la source seul.**

    Cas reel, prompt 212 : `Parcours` nommait cinq adversaires de Tiafoe, la
    source quatre, et `_uncovered` n'en signalait aucun de non couvert — son
    rapprochement par **nom ou jour** est genereux a dessein, et le cinquieme
    partageait sa journee avec le quatrieme. Cette generosite protege la fiche de
    recherche ; reprise comme un **compte**, elle perd un tour en silence.

    C'est ce qui interdit l'appariement unique : les deux consommateurs ont
    besoin d'erreurs de sens opposes — ne pas sur-affirmer un manque d'un cote,
    ne pas sous-affirmer un compte de l'autre.
    """
    from myassistantbet.services import tennis_round

    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-15T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            # Deux joueurs de plus : a quatre, la population forme un tableau et
            # la ligne nomme « finale », donc ne porte plus de compte.
            ("P", "Q", "2026-08-16T13:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    valeur = tennis_round.lines(competition, "2026-08-18T12:00:00Z", migrated, "A", "B", {"A": 1})[
        0
    ][1]

    assert f"{tennis_round.ROUNDS_PLAYED} 2 tours disputes par A" in valeur


def test_l_assembleur_porte_le_compte_de_la_source_jusqu_a_la_ligne_tour(
    migrated: Settings,
) -> None:
    """**Le service et sa surface se livrent ensemble.**

    Les deux bancs ci-dessus appellent `tennis_round.lines` directement : ils
    resteraient verts si l'assembleur ne branchait rien. C'est exactement le
    defaut du chantier 4 — un banc qui mesure le lecteur ne voit pas un defaut
    dans la porte — et ici la porte est `session.context_block`, seul endroit ou
    les deux lignes se rencontrent.
    """
    from myassistantbet.services import session as session_service

    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-15T12:00:00Z"),
            ("A", "Y", "2026-08-16T12:00:00Z"),
            ("A", "Z", "2026-08-17T12:00:00Z"),
            ("B", "W", "2026-08-17T14:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    # Nos scans ne voient qu'un tour de B ; la source en connait trois.
    _profil_tournoi(
        "A",
        [
            _match_source("A", "X", "2026-08-15", "6-3 6-4"),
            _match_source("A", "Y", "2026-08-16", "6-1 6-2"),
            _match_source("A", "Z", "2026-08-17", "6-2 6-2"),
        ],
        migrated,
    )
    _profil_tournoi(
        "B",
        [
            _match_source("B", "U", "2026-08-14", "6-0 6-1"),
            _match_source("B", "V", "2026-08-15", "6-4 6-4"),
            _match_source("B", "W", "2026-08-17", "7-5 6-3"),
        ],
        migrated,
    )
    lignes = dict(
        session_service.context_block(
            1,
            "A",
            "B",
            "2026-08-18T12:00:00Z",
            "tennis",
            oddsapi_key="tennis_atp_us_open",
            competition_id=competition,
            settings=_avec_ligne(migrated),
        )
    )

    from myassistantbet.services import tennis_round

    assert f"{tennis_round.ROUNDS_PLAYED} 3 tours disputes par A, 3 par B" in lignes["Tour"]


def test_le_mode_d_emploi_du_tour_ne_dit_plus_nos_scans_seuls() -> None:
    """**Toute condition ajoutee a une ligne se verifie contre la phrase qui
    l'explique.**

    Le gabarit disait « sur nos propres releves » — vrai tant que le compte ne
    sortait que de nos scans, faux des que la source y entre. Corriger le rendu
    sans relire son mode d'emploi laisse une affirmation fausse a l'endroit
    precis ou l'on vient de gagner en justesse : c'est la regle que `Serie` et
    `Non joue` ont deja payee.
    """
    from pathlib import Path

    from myassistantbet.services.prompt import TEMPLATES_DIR

    texte = " ".join(
        Path(TEMPLATES_DIR, "session_default.md.j2").read_text(encoding="utf-8").split()
    )

    assert "les tours déjà joués ici, sur **le plus complet de nos deux relevés**" in texte
    assert "sur nos propres relevés" not in texte


def test_un_seul_tour_s_accorde_au_singulier(migrated: Settings) -> None:
    """`au moins 1 tour dispute`, jamais `1 tour disputes`.

    Le pluriel etait porte par le seul nom et jamais par le participe, si bien
    qu'un joueur entre en lice depuis un tour lisait « 1 tour disputes ». Rien ne
    casse — et c'est ce qui le rend genant : le lecteur se demande si le pluriel
    porte une information que le chiffre ne porte pas.
    """
    from myassistantbet.services import tennis_round

    competition = _tournoi(
        migrated,
        [
            ("A", "X", "2026-08-15T12:00:00Z"),
            ("P", "Q", "2026-08-16T13:00:00Z"),
            ("A", "B", "2026-08-18T12:00:00Z"),
        ],
    )
    valeur = tennis_round.lines(competition, "2026-08-18T12:00:00Z", migrated, "A", "B")[0][1]

    assert f"{tennis_round.ROUNDS_PLAYED} 1 tour dispute par A" in valeur
    assert "1 tour disputes" not in valeur


def test_un_tour_nomme_ne_repete_pas_le_compte(migrated: Settings) -> None:
    """`quart de finale` situe deja le joueur : le compte n'ajouterait rien, et
    une ligne qui sort partout cesse d'informer."""
    from myassistantbet.services import tennis_round

    jours = [(f"J{i}", f"K{i}", "2026-08-14T12:00:00Z") for i in range(8)]
    competition = _tournoi(migrated, [*jours, ("J0", "J1", "2026-08-18T12:00:00Z")])
    valeur = tennis_round.lines(competition, "2026-08-18T12:00:00Z", migrated, "J0", "J1")[0][1]

    assert tennis_round.UNSET_ROUND not in valeur
    assert tennis_round.ROUNDS_PLAYED not in valeur


async def test_le_palmares_ne_se_borne_pas_sur_le_temps_de_mur_des_timelines(
    migrated: Settings, monkeypatch: Any
) -> None:
    """**Deux couts, deux bornes.** Une timeline coute quatre a six appels **par
    rencontre**, un palmares une mediane de trois **par joueur** : `BATCH` borne
    le temps de mur de la premiere, et l'appliquer au second le rendait invisible
    sur la majorite des lots.

    Mesure du 20/08/2026 sur les 18 journees de board archivees : mediane 32
    joueurs de tennis par journee, maximum 99, et `BATCH` (12) ne couvre la
    journee que **4 fois sur 18**.

    **Ce test poste la passe et relit ce qu'elle a demande**, jamais le service
    seul : le defaut vivait dans la porte, pas dans le lecteur — un palmares
    correct n'aurait rien montre.
    """
    from myassistantbet import timelines
    from myassistantbet.services import palmares as palmares_service

    for index in range(20):
        _event(migrated, "tennis_atp_us_open", f"J{index}", f"K{index}", "2099-01-01T12:00:00Z")

    demandes: list[list[tuple[str, str]]] = []

    async def _faux_palmares(_client: Any, joueurs: Any, _settings: Any = None) -> int:
        demandes.append(list(joueurs))
        return 0

    async def _faux_sync(*_args: Any, **_kwargs: Any) -> serve_stats.SyncReport:
        return serve_stats.SyncReport()

    monkeypatch.setattr(palmares_service, "refresh", _faux_palmares)
    monkeypatch.setattr(serve_stats, "sync", _faux_sync)

    await timelines.run(settings=migrated)

    assert len(demandes) == 1
    # Les 40 joueurs a venir, et non les `BATCH` premiers.
    assert len(demandes[0]) == 40
    assert len(timelines.players(migrated)) == timelines.BATCH


def _paire_agg(nom: str, **champs: int) -> serve_stats.ServeAggregate:
    """Un agregat de volume realiste, pour comparer deux joueurs a la main."""
    base: dict[str, int] = {
        "matches": 30,
        "first_serve": 900,
        "first_serve_of": 1400,
        "won_first": 900,
        "won_first_of": 1400,
        "won_second": 250,
        "won_second_of": 500,
        "double_faults": 60,
        "return_points": 1400,
        "return_won": 600,
    }
    base.update(champs)
    return serve_stats.ServeAggregate(player=nom, circuit="wta", **base)


def test_l_ecart_se_tait_sur_une_difference_qui_tient_dans_le_bruit() -> None:
    """`+0.1 pts` sur 1 400 points n'est pas un petit avantage, c'est **rien**.

    Le bloc du 20/08 nommait « service +0.1 pts sur la 1re balle » pendant que
    la ligne juste au-dessus portait 6,1 points sur les points gagnes et 7,5 sur
    les doubles fautes. Le seuil ne s'invente pas : il se lit sur les
    denominateurs, par l'intervalle de Newcombe.
    """
    identiques = _gap = serve_stats._gap_fragment("A", "B", _paire_agg("A"), _paire_agg("B"))
    assert identiques == "", f"une difference nulle ne se nomme pas : {_gap!r}"


def test_l_ecart_nomme_les_points_gagnes_et_non_la_mise_en_jeu() -> None:
    """**Le taux de premieres balles dit qui sert ainsi, pas qui en tire quelque
    chose.** Deux joueurs peuvent rentrer la meme part de premieres et n'en tirer
    pas du tout la meme chose."""
    # A rentre bien plus de premieres, B en gagne bien plus.
    a = _paire_agg("A", first_serve=1200, won_first=800)
    b = _paire_agg("B", first_serve=700, won_first=1000)

    valeur = serve_stats._gap_fragment("A", "B", a, b)

    assert valeur.startswith("s/1re "), valeur
    assert "pour B" in valeur, "l'avantage va a celui qui gagne les points"
    assert "1re balle" not in valeur, "la mise en jeu reste sur « Service »"


def test_sur_les_doubles_fautes_l_avantage_est_au_plus_bas() -> None:
    """Le seul piege de la ligne, et il designerait le mauvais joueur."""
    a = _paire_agg("A", double_faults=40)
    b = _paire_agg("B", double_faults=140)

    valeur = serve_stats._gap_fragment("A", "B", a, b)

    fragment = next(part for part in valeur.split(" | ") if part.startswith("df "))
    assert "pour A" in fragment, fragment
    assert "pour B" not in fragment, fragment


def test_les_doubles_fautes_du_tournoi_se_rendent_en_taux(migrated: Settings) -> None:
    """**Deux unites dans la meme famille de lignes ne se comparent pas.**

    `Ici` rendait `12 df` — un compte brut — a cote de `61.8% 1re`, quand
    `Service` rend `11.3% df` sur les **secondes balles**. Les deux lignes
    decrivent le meme joueur a deux profondeurs, et le rapprochement demandait un
    calcul intermediaire : 12 doubles fautes sur ~81 secondes balles font 14,8 %
    sur ce tournoi contre 11,3 % sur 52 semaines.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    _profil_tournoi("A", [_match_source("A", "X", "2026-08-14", "6-3 6-4")], migrated)
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    service = next(part for part in valeur.split("\n") if part.startswith("service ici"))
    assert "% df" in service, service
    # Les trois grandeurs sont des taux : plus un seul compte nu dans le
    # fragment, la parenthese portant deja le denominateur.
    assert service.count("%") == 3, service


def test_le_tournoi_du_fragment_se_corrobore_contre_celui_du_bloc(migrated: Settings) -> None:
    """**La table du lot 17 existait et n'etait branchee que sur le palmares.**

    La corroboration par nos scans peut tomber sur un joueur qui a croise le meme
    adversaire dans les deux tournois de la quinzaine ; le **nom du tournoi**,
    lui, ne le peut pas. Les deux gardes sont cumulatives.
    """
    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    ailleurs = _match_source("A", "X", "2026-08-14", "7-5 6-4", tournoi=901)
    ailleurs["tournament"] = {"id": 901, "name": "Ailleurs Open - Ailleurs"}
    _profil_tournoi("A", [ailleurs], migrated)
    _profil_tournoi("B", [], migrated)

    # Nos scans corroborent — meme adversaire, meme jour — et pourtant le tournoi
    # n'est pas celui du bloc : rien ne se rend.
    assert (
        serve_stats.here_lines(
            "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
        )
        == []
    )


def test_une_competition_non_rattachee_ne_rend_pas_la_garde_negative(
    migrated: Settings,
) -> None:
    """**Un ensemble declare vide n'affirme rien.** Meme regle que la moitie
    « ici » du palmares, qui se tait plutot que d'ecrire « jamais joue »."""
    from myassistantbet import db

    competition = _tournoi(
        migrated,
        [("A", "X", "2026-08-14T12:00:00Z"), ("A", "B", "2026-08-18T12:00:00Z")],
    )
    db.execute(
        "UPDATE competitions SET matchesplayed_tournaments = '' WHERE id = ?",
        (competition,),
        settings=migrated,
    )
    inconnu = _match_source("A", "X", "2026-08-14", "7-5 6-4")
    inconnu["tournament"] = {"id": 900, "name": "Tournoi jamais declare"}
    _profil_tournoi("A", [inconnu], migrated)
    _profil_tournoi("B", [], migrated)

    valeur = serve_stats.here_lines(
        "A", "B", "atp", competition, "2026-08-18T12:00:00Z", _avec_ligne(migrated)
    )[0][1]

    assert "bat X 7-5 6-4" in valeur
