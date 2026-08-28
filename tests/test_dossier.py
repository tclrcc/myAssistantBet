"""Dossier d'equipe : socle de memorisation par equipe, peremption, plancher.

Ce qui est verifie ici et qui n'allait pas de soi : une donnee qui vaut pour une
equipe ne doit pas se payer une fois par match, un entraineur parti ne doit
jamais etre nomme comme s'il etait en poste, et un plancher d'appels franchi doit
se dire au lieu de ressembler a une panne de rapprochement.
"""

from __future__ import annotations

import re
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
import respx
from fastapi.testclient import TestClient

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.main import app
from myassistantbet.providers.apifootball import BASE_URL, PROVIDER, APIFootballClient
from myassistantbet.providers.base import record_api_usage
from myassistantbet.services import dossier
from myassistantbet.services.context import KIND_SHEETS, KIND_STANDINGS, KIND_TEAMS
from myassistantbet.services.context import store as store_context

from .helpers import (
    DOSSIER_RATE_HEADERS,
    LEAGUE,
    PROPS_LEAGUE,
    RATE_HEADERS,
    mock_dossier_routes,
)

HOME = "BK Hacken"
AWAY = "Djurgardens IF"
COMMENCE = "2026-08-03T15:30:00Z"
NOW = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)


def _seed_event(settings: Settings, *, rapproche: bool = True, league: int = LEAGUE) -> None:
    """Un match rattache a une competition, dont le rapprochement a deja eu lieu."""
    competition = db.query_one(
        "SELECT id, sport_id FROM competitions WHERE apifootball_league_id = ?",
        (league,),
        settings=settings,
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
            1,
            KIND_TEAMS,
            {"home": 376, "away": 377, "league": league, "season": 2026},
            settings,
        )


def _mock_dossier(load_fixture: Any) -> dict[str, respx.Route]:
    """Les routes du dossier, tenues dans `helpers` avec celles du contexte."""
    return mock_dossier_routes(load_fixture)


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
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Entraineur"] == (
        "BK Hacken Per Gustafsson (depuis 06/2023, 3 ans) | "
        "Djurgardens IF Mikael Lindqvist (depuis 06/2026, 1 mois)"
    )


@respx.mock
@pytest.mark.anyio
async def test_la_feuille_de_match_date_la_fiche_d_entraineur(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Deux sources, et la ligne les montre toutes les deux.**

    Mesure du 13/08/2026 : 4 fiches fausses sur 12 blocs, parce que le
    fournisseur ne referme pas ses etapes de carriere. L'entraineur du banc, lui,
    etait deja telecharge — `/fixtures/lineups` le sert avec son identifiant, et
    ces feuilles sont lues pour reconstruire un effectif — puis jete.

    Rejeu du lot avec les observations reelles : la feuille rattrape **trois**
    des quatre — Rangers, Anderlecht, Hearts. La quatrieme, Pafos, dit Celades
    des deux cotes quand la realite est Sa Pinto, et c'est pour elle que la ligne
    n'ecrit jamais « confirme » : l'accord de deux fiches perimees se
    presenterait comme une verification.
    """
    _seed_event(migrated)
    _mock_dossier(load_fixture)
    store_context(
        1,
        KIND_SHEETS,
        {
            "available": True,
            "home": [],
            "away": [],
            # Concordance : meme identifiant que la fiche, donc la feuille date.
            # **Le nom complet, parce que la feuille l'est toujours** : 287 relevés
            # sur 287 en base, aucun abrege. Une feuille abregee simulait une
            # donnee que le fournisseur ne produit pas.
            "home_coach": {"id": 1001, "name": "Per Gustafsson", "seen": "2026-08-09T15:00:00Z"},
            # Divergence : le banc du 09/08 nomme quelqu'un d'autre.
            "away_coach": {"id": 2002, "name": "Wouter Vrancken", "seen": "2026-08-09T14:00:00Z"},
        },
        migrated,
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    ligne = _lines(migrated)["Entraineur"]

    assert "BK Hacken Per Gustafsson (depuis 06/2023, 3 ans) — vu sur la feuille du 09/08" in ligne
    assert "confirme" not in ligne, "un accord de deux fiches n'est pas une verification"
    assert (
        "Djurgardens IF feuille du 09/08 : Wouter Vrancken | "
        "fiche : Mikael Lindqvist (depuis 06/2026, 1 mois) — divergence"
    ) in ligne


@respx.mock
@pytest.mark.anyio
async def test_sans_feuille_lue_la_fiche_d_entraineur_ne_porte_aucune_mention(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les feuilles ne se telechargent que la ou `/injuries` ne couvre pas.

    Ecrire « non confirme » partout ailleurs ferait paraitre la mention sur
    chaque bloc bien couvert, ou elle cesserait d'etre un signal pour devenir un
    decor — le defaut exact des deux seuils egaux. Meme discipline que les trois
    etats d'`Absents` : « on a regarde » et « on n'a pas regarde » ne se
    confondent pas.
    """
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    ligne = _lines(migrated)["Entraineur"]

    assert "non confirme" not in ligne
    assert "feuille" not in ligne


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
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    ligne = _lines(migrated)["Entraineur"]
    assert "Mikael Lindqvist" in ligne
    assert "T. Kalmar" not in ligne, "son etape a Djurgarden est refermee"


@respx.mock
@pytest.mark.anyio
async def test_l_anciennete_se_compte_dans_l_equipe_du_match_pas_dans_la_precedente(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La carriere porte aussi les clubs precedents : compter depuis le premier
    poste donnerait « depuis 02/2024 » pour une arrivee de juin 2026."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

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
    routes = _mock_dossier(load_fixture)

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
    routes = _mock_dossier(load_fixture)

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
    routes = _mock_dossier(load_fixture)
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
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    plus_tard = NOW + timedelta(hours=dossier.TTL_HOURS[dossier.KIND_COACH] + 1)
    await dossier.refresh_event(api_client, 1, migrated, now=plus_tard)

    lignes = db.query(
        "SELECT team_id FROM team_context WHERE team_id = 376 AND kind = ?",
        (dossier.KIND_COACH,),
        settings=migrated,
    )
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
    routes = _mock_dossier(load_fixture)

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
    routes = _mock_dossier(load_fixture)
    routes["away"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    ligne = _lines(migrated)["Entraineur"]
    assert "BK Hacken Per Gustafsson" in ligne
    assert AWAY not in ligne


@respx.mock
@pytest.mark.anyio
async def test_une_prise_de_fonction_posterieure_au_match_ne_rend_aucune_duree(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une anciennete negative presentee comme une duree serait une absurdite
    affichee. La date reste, elle est verifiable."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    futur = load_fixture("apifootball_coachs_home.json")
    futur["response"][0]["career"][0]["start"] = "2027-01-05"
    routes["home"].mock(return_value=httpx.Response(200, json=futur, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "BK Hacken Per Gustafsson (depuis 01/2027)" in _lines(migrated)["Entraineur"]


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
    routes = _mock_dossier(load_fixture)
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
    routes = _mock_dossier(load_fixture)

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
        # Fiche montee sans `firstname`/`lastname` : il n'y a rien a completer,
        # et le nom abrege reste le seul disponible.
        assert "P. Gustafsson" in _lines(migrated)["Entraineur"]


# -- Historique de saison ----------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_l_historique_donne_les_buts_du_match_et_le_btts(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les deux plus gros marches achetes a l'etage B — `alternate_totals` et
    `btts` — n'avaient aucun angle sportif en face : le bloc portait les cotes
    et rien de ce que les equipes produisent."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Total buts"] == (
        "BK Hacken >2.5 23/36, BTTS 22/36 (2025) | Djurgardens IF >2.5 19/28, BTTS 16/28 (2025)"
    )


def _match(goals: tuple[int, int], halftime: tuple[int, int] | None, at_home: bool) -> dict:
    """Un match de l'historique de saison, dans la forme que `_summarize` ecrit."""
    entry: dict[str, Any] = {"goals": list(goals), "at_home": at_home}
    if halftime is not None:
        entry["halftime"] = list(halftime)
    return entry


def test_a_la_pause_compte_du_bon_cote_du_score() -> None:
    """**Le signe est ce que ce fragment peut produire de pire**, et il ne casse
    rien : une ligne qui compterait le camp adverse resterait plausible, avec des
    fractions bien formees. C'est le defaut deja paye sur le handicap.

    Les deux moities du meme score sont donc montees a l'identique, une fois a
    domicile et une fois a l'exterieur : l'equipe mene dans le premier cas et
    jamais dans le second."""
    # Le **meme** score, vu des deux cotes : `1-0` a la pause pour l'equipe qui
    # recoit, donc l'equipe qui se deplace est menee. Un fragment qui lirait la
    # mauvaise moitie rendrait « 4/4 » dans les deux cas.
    devant = [_match((2, 0), (1, 0), at_home=True) for _ in range(4)]
    derriere = [_match((2, 0), (1, 0), at_home=False) for _ in range(4)]

    assert dossier._halftime_fragment("Lyon", (devant, 2026), 2026).startswith("Lyon mene 4/4")
    assert dossier._halftime_fragment("Lyon", (derriere, 2026), 2026).startswith("Lyon mene 0/4")


def test_a_la_pause_ne_compte_que_les_matchs_dont_la_pause_est_connue() -> None:
    """Le denominateur est celui des matchs **dont le score a la pause est
    connu**, et il peut donc differer de celui de « Total buts ». Il est ecrit
    dans la ligne, donc l'ecart se voit — mais il faut qu'il soit juste, sans quoi
    la ligne compterait un match qu'elle n'a pas lu."""
    matches = [
        _match((1, 0), (1, 0), at_home=True),
        _match((3, 1), (2, 0), at_home=True),
        _match((0, 0), None, at_home=True),
    ]

    rendu = dossier._halftime_fragment("Lyon", (matches, 2026), 2026)

    assert rendu == "Lyon mene 2/2, >1.5 1/2"


def test_a_la_pause_se_tait_quand_aucune_pause_n_est_connue() -> None:
    """Un historique sans aucun score a la pause ne rend **rien**. `0/0` ne
    decrirait personne — meme regle que « 1re MT » sur une equipe qui n'a ni
    marque ni encaisse."""
    matches = [_match((1, 0), None, at_home=True) for _ in range(6)]

    assert dossier._halftime_fragment("Lyon", (matches, 2026), 2026) == ""


def test_a_la_pause_ecrit_la_saison_de_repli() -> None:
    """Meme regle que « Total buts », dont elle partage l'historique : une
    frequence sur la saison passee reste lisible, mais taire laquelle c'est
    laisser croire a la saison en cours."""
    matches = [_match((1, 0), (1, 0), at_home=True) for _ in range(5)]

    assert dossier._halftime_fragment("Lyon", (matches, 2025), 2026).endswith("(2025)")
    assert "(" not in dossier._halftime_fragment("Lyon", (matches, 2026), 2026)


def test_le_niveau_des_adversaires_se_tait_sous_trois_rangs_connus() -> None:
    """Un rang moyen sur deux matchs decrit une soiree, pas un calendrier — meme
    seuil que `PROFILE_MIN_MATCHES` et meme raison.

    Mesure sur 606 equipes : **86 % ont au moins trois adversaires classes** parmi
    leurs cinq derniers, donc le seuil ne tait pas la ligne, il ecarte la queue.
    """
    rangs = {"10": 3, "11": 7}
    matches = [_match((1, 0), (1, 0), at_home=True) | {"opponent": adv} for adv in (10, 11, 99)]

    assert dossier._level_fragment("Lyon", (matches, 2026), rangs) == ""


def test_le_niveau_des_adversaires_ne_compte_que_les_classes() -> None:
    """Un adversaire de coupe ou de coupe d'Europe n'est dans aucune table : le
    denominateur tombe alors sous cinq, et il est **ecrit**. Mesure : 60 % des
    equipes ont leurs cinq adversaires classes, 86 % en ont au moins trois."""
    rangs = {"10": 2, "11": 8, "12": 14}
    matches = [_match((1, 0), (1, 0), at_home=True) | {"opponent": adv} for adv in (10, 11, 12, 99)]

    # (2 + 8 + 14) / 3 = 8.0, et le quatrieme adversaire n'est pas classe.
    assert dossier._level_fragment("Lyon", (matches, 2026), rangs) == "Lyon 8.0e moy/3"


def test_le_niveau_des_adversaires_porte_sur_la_fenetre_de_forme_5() -> None:
    """La fenetre est celle de `Forme 5`, dont le niveau eclaire la lecture. Une
    autre ferait porter les deux lignes sur deux periodes — ce que le compte de
    `Forme 5` a deja du corriger une fois."""
    rangs = {str(adv): 1 for adv in range(1, 6)} | {str(adv): 20 for adv in range(90, 96)}
    vieux = [_match((1, 0), (1, 0), at_home=True) | {"opponent": adv} for adv in range(90, 96)]
    recents = [_match((1, 0), (1, 0), at_home=True) | {"opponent": adv} for adv in range(1, 6)]

    rendu = dossier._level_fragment("Lyon", (vieux + recents, 2026), rangs)

    assert rendu == "Lyon 1.0e moy/5", "seuls les cinq derniers comptent"


def test_le_niveau_des_adversaires_se_tait_sans_classement() -> None:
    """Un releve de classement anterieur au 28/08/2026 ne porte pas la table des
    rangs : la ligne ne sort qu'apres le prochain enrichissement, et jamais un
    « inconnu » qui se lirait comme un fait sur l'equipe."""
    matches = [_match((1, 0), (1, 0), at_home=True) | {"opponent": adv} for adv in (10, 11, 12)]

    assert dossier._level_fragment("Lyon", (matches, 2026), {}) == ""


@respx.mock
@pytest.mark.anyio
async def test_le_niveau_des_adversaires_traverse_le_parcours_reel(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**Les deux moities etaient telechargees et jetees** : `_summarize` gardait
    tout d'un match sauf l'adversaire, et `_standings_entry` parcourt le
    classement entier pour n'en retenir que deux lignes. Zero appel de plus.

    Le banc croise les **deux echelles** — l'historique est range par equipe, le
    classement par evenement — parce qu'un banc pose sur le seul fragment ne
    verifierait pas ce croisement. Le releve de classement est pose comme
    `fetch_context` l'ecrit : les deux modules sont separes par construction, et
    c'est l'autre moitie qui est gardee cote `test_context`.
    """
    _seed_event(migrated)
    _mock_dossier(load_fixture)
    store_context(
        1,
        KIND_STANDINGS,
        {"home": {"rank": 4}, "rangs": {"215": 5, "242": 9, "217": 2, "4724": 12, "211": 1}},
        migrated,
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    lignes = _lines(migrated)
    assert "Niveau adv." in lignes
    assert re.fullmatch(r"[^|]+ \d+\.\de moy/\d", lignes["Niveau adv."].split(" | ")[0])


@respx.mock
@pytest.mark.anyio
async def test_a_la_pause_sort_du_meme_historique_que_total_buts(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """**La ligne dormait en base depuis l'origine du module** : `_summarize`
    gardait `score.halftime` de chaque match et rien ne le lisait, quand quatre
    marches de mi-temps sont achetes et rendus sur la moitie des blocs.

    Le banc passe par le parcours reel plutot que par le seul fragment : ce qui
    devait etre garanti est qu'elle sorte **sur la meme population que « Total
    buts »**, dont elle partage l'appel, la fenetre et le seuil."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    lignes = _lines(migrated)
    assert ("Total buts" in lignes) == ("A la pause" in lignes)
    assert "A la pause" in lignes

    # Le denominateur est celui des matchs **dont la pause est connue**, donc un
    # sous-ensemble de ceux que « Total buts » compte : la fixture en porte 38
    # sur 41. C'est la propriete qui garantit que la ligne n'a pas recopie un
    # denominateur qu'elle n'a pas lu.
    def _denominateurs(ligne: str) -> list[int]:
        return [int(found) for found in re.findall(r"/(\d+)", ligne)]

    assert max(_denominateurs(lignes["A la pause"])) <= max(_denominateurs(lignes["Total buts"]))
    # Et le repli de saison traverse le parcours reel, comme pour « Total buts ».
    assert ("(2025)" in lignes["A la pause"]) == ("(2025)" in lignes["Total buts"])


@respx.mock
@pytest.mark.anyio
async def test_la_saison_de_repli_est_ecrite_et_non_tue(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« 18/34 » sur la saison passee et sur la saison en cours ne se lisent pas
    pareil. Taire laquelle c'est laisser croire a la seconde — et en aout c'est
    toujours la premiere : la saison en cours ne porte que des amicaux."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "(2025)" in _lines(migrated)["Total buts"]


@respx.mock
@pytest.mark.anyio
async def test_la_saison_precedente_n_est_demandee_que_si_la_courante_ne_dit_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un appel par equipe et par saison : le demander d'office doublerait la
    facture d'une soiree de championnat, ou la saison en cours suffit."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    pleine = load_fixture("apifootball_fixtures_season_home_prev.json")
    routes["season_home"].mock(return_value=httpx.Response(200, json=pleine, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["season_home_prev"].call_count == 0, "la saison en cours suffisait"
    assert routes["season_away_prev"].call_count == 1, "l'autre equipe, non"


@respx.mock
@pytest.mark.anyio
async def test_les_amicaux_n_entrent_dans_aucun_compte(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une victoire 4-3 en preparation ne dit rien de la saison. En juillet ce
    sont les seuls matchs joues : les compter donnerait « >2.5 dans 4/4 » a une
    equipe qui n'a pas encore joue un match officiel."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    courante = dossier.load(376, dossier.KIND_SEASON, "2026", migrated)
    assert courante is not None
    joues = [match for match in courante[0] if match["status"] == "FT"]
    assert joues and all(match["friendly"] for match in joues), (
        "la saison en cours de la fixture reelle ne porte que des amicaux joues"
    )
    # Quatre matchs joues et pourtant un repli : c'est qu'aucun n'a compte.
    assert "(2025)" in _lines(migrated)["Total buts"]


@respx.mock
@pytest.mark.anyio
async def test_un_match_annule_ou_reporte_ne_compte_pas(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un match qui ne s'est pas joue n'a rien a dire, et un 3-0 sur tapis vert
    fausserait autant les buts que la serie. La fixture reelle porte bien un
    `CANC`, mais c'est un amical : le report se teste sur un match officiel,
    sinon l'autre regle suffirait a faire passer le test."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    saison = load_fixture("apifootball_fixtures_season_home_prev.json")
    officiel = next(
        match
        for match in saison["response"]
        if match["league"]["id"] == 113 and match["fixture"]["status"]["short"] == "FT"
    )
    officiel["fixture"]["status"]["short"] = "PST"
    routes["season_home_prev"].mock(
        return_value=httpx.Response(200, json=saison, headers=RATE_HEADERS)
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    # 36 officiels joues dans la fixture intacte, 35 des qu'un est reporte — et
    # le match retire etait un BTTS a moins de 2.5 buts, d'ou 22 puis 21.
    assert "BK Hacken >2.5 23/35, BTTS 21/35" in _lines(migrated)["Total buts"]


def _saison_en_cours_pleine(routes: dict[str, Any], load_fixture: Any) -> None:
    """Sert la saison complete sous l'annee **en cours**, donc sans repli.

    Les fixtures decrivent volontairement un mois d'aout : la saison en cours n'y
    porte que des amicaux et des matchs a venir, si bien que `_history` se replie
    sur N-1. C'est le cas normal, teste a part — mais la mecanique de la serie,
    elle, ne peut se verifier que sur une saison en cours qui dit quelque chose.
    """
    for cote, fichier in (
        ("season_home", "apifootball_fixtures_season_home_prev.json"),
        ("season_away", "apifootball_fixtures_season_away_prev.json"),
    ):
        routes[cote].mock(
            return_value=httpx.Response(
                200, json=load_fixture(fichier), headers=DOSSIER_RATE_HEADERS
            )
        )


@respx.mock
@pytest.mark.anyio
async def test_la_serie_en_cours_n_est_pas_le_record_de_la_saison(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`biggest.streak` de `/teams/statistics` donne le record, ce qui se lit
    comme la serie en cours et dit l'inverse : une equipe qui a gagne quatre fois
    en mars et perd depuis un mois y afficherait « 4 »."""
    _seed_event(migrated)
    _saison_en_cours_pleine(_mock_dossier(load_fixture), load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Serie"] == "Djurgardens IF 2D"


@respx.mock
@pytest.mark.anyio
async def test_une_serie_de_un_match_n_est_pas_une_serie(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """L'equipe a domicile sort d'une seule defaite : ecrire « 1D » habillerait
    un resultat isole en tendance."""
    _seed_event(migrated)
    _saison_en_cours_pleine(_mock_dossier(load_fixture), load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert HOME not in _lines(migrated)["Serie"]


def _saison_avec_statut(settings: Settings, statut: str, jour: str = "2026-08-03") -> None:
    """Un match de l'equipe a domicile, ce jour-la, avec le statut voulu."""
    dossier.store(
        376,
        dossier.KIND_SEASON,
        [{"date": f"{jour}T15:30:00+00:00", "status": statut, "league_id": 113, "at_home": True}],
        "2026",
        settings,
    )


def test_un_match_reporte_est_dit_avant_tout_le_reste(migrated: Settings) -> None:
    """L'information dormait deja en base : `_summarize` garde le statut de
    chaque match, et le match analyse figure dans l'historique de sa propre
    equipe. Personne ne le lisait — le bloc a servi « Rakow - Zaglebie » avec ses
    cotes le jour ou il etait reporte depuis neuf jours, et seule une recherche
    exterieure l'a rattrape. Aucun appel n'est ajoute pour le savoir."""
    from myassistantbet.services import session

    _seed_event(migrated)
    _saison_avec_statut(migrated, "PST")

    lignes = session.context_block(1, HOME, AWAY, COMMENCE, "football", settings=migrated)

    assert lignes[0] == ("Statut", "reporte (fournisseur de contexte)"), "en tete du bloc"


def test_un_match_normal_ne_produit_aucune_ligne_de_statut(migrated: Settings) -> None:
    """`NS` est le cas ordinaire : une ligne par match dirait le contraire de ce
    qu'elle sert a signaler."""
    from myassistantbet.services import session

    _seed_event(migrated)
    _saison_avec_statut(migrated, "NS")

    lignes = session.context_block(1, HOME, AWAY, COMMENCE, "football", settings=migrated)

    assert "Statut" not in dict(lignes)


def test_le_statut_se_rapproche_sur_la_journee_et_non_sur_l_heure(migrated: Settings) -> None:
    """Un report s'accompagne souvent d'un changement d'horaire : exiger la minute
    ferait manquer precisement le cas qu'on cherche."""
    from myassistantbet.services import session

    _seed_event(migrated)
    dossier.store(
        376,
        dossier.KIND_SEASON,
        [{"date": "2026-08-03T19:00:00+00:00", "status": "PST", "league_id": 113, "at_home": True}],
        "2026",
        migrated,
    )

    lignes = session.context_block(1, HOME, AWAY, COMMENCE, "football", settings=migrated)

    assert dict(lignes)["Statut"].startswith("reporte")


def test_sans_rapprochement_aucun_statut_n_est_devine(migrated: Settings) -> None:
    """Une absence de ligne ne prouve pas qu'un match aura lieu : elle dit
    seulement que rien ne s'y oppose dans ce que nous savons."""
    from myassistantbet.services import session

    _seed_event(migrated, rapproche=False)

    lignes = session.context_block(1, HOME, AWAY, COMMENCE, "football", settings=migrated)

    assert "Statut" not in dict(lignes)


def test_le_prompt_dit_qu_un_statut_est_bloquant(migrated: Settings) -> None:
    """Le mode d'emploi est garde sur le libelle : il n'est paye que par les lots
    qui portent vraiment la ligne, comme toutes les portes du preambule."""
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    _saison_avec_statut(migrated, "PST")
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )
    db.execute("INSERT INTO session_events (session_id, event_id) VALUES (1, 1)", settings=migrated)

    body = build_prompt(1, settings=migrated, now=NOW).body
    # La prose est justifiee a largeur fixe : la chercher dans le corps brut la
    # couperait a la premiere fin de ligne. La ligne du bloc, elle, garde son
    # alignement — qu'une normalisation des blancs effacerait.
    corps = " ".join(body.split())

    assert "  Statut      reporte (fournisseur de contexte)" in body
    assert "ne donne pas ce match comme jouable" in corps
    assert "l'absence de cette ligne ne prouve rien" in corps


def test_le_prompt_presente_l_entraineur_comme_une_piste(migrated: Settings) -> None:
    """Mesure sur la base reelle : **92 des 110 clubs** ont plusieurs etapes de
    carriere ouvertes chez eux, et le fournisseur peut n'avoir jamais enregistre
    une nomination. Le bloc nommait ainsi R. Jans a Utrecht, parti depuis, son
    successeur ne figurant nulle part dans la reponse — releve du matin meme,
    donc sans rapport avec la peremption.

    Aucune regle de choix ne rattrape une nomination absente : c'est le preambule
    qui doit dire que la ligne est une piste, sans quoi une anciennete longue se
    lit comme une preuve de continuite."""
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    dossier.store(
        376,
        dossier.KIND_COACH,
        [{"name": "P. Gustafsson", "career": [{"team": {"id": 376}, "start": "2023-06-01"}]}],
        settings=migrated,
    )
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )
    db.execute("INSERT INTO session_events (session_id, event_id) VALUES (1, 1)", settings=migrated)

    corps = " ".join(build_prompt(1, settings=migrated, now=NOW).body.split())

    assert "Le fournisseur ne referme pas ses fiches" in corps
    assert "ne prouve donc pas la continuité" in corps
    assert "Traite cette ligne comme une piste, jamais comme un fait" in corps


def test_le_mode_d_emploi_de_l_entraineur_ne_se_paie_pas_sans_la_ligne(
    migrated: Settings,
) -> None:
    """Un lot monte a la main n'a pas de dossier d'equipe : le paragraphe entier
    disparait, comme toutes les portes du preambule."""
    from myassistantbet.services.prompt import build_prompt

    _lot(migrated, "football")

    corps = build_prompt(1, settings=migrated).body

    assert "Le fournisseur ne referme pas ses fiches" not in corps
    assert "La ligne **« Entraîneur »**" not in corps


def test_le_mode_d_emploi_du_statut_ne_se_paie_pas_sans_la_ligne(migrated: Settings) -> None:
    """Meme regle que le reste du preambule : ce que le lot ne porte pas ne se
    documente pas."""
    from myassistantbet.services.prompt import build_prompt

    _lot(migrated, "football")

    corps = build_prompt(1, settings=migrated).body

    assert "ne donne pas ce match comme jouable" not in corps


@respx.mock
@pytest.mark.anyio
async def test_aucune_serie_quand_l_historique_se_replie_sur_la_saison_passee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une serie « en cours » est une affirmation sur **maintenant**. Lue sur la
    saison passee, elle n'est pas seulement perimee : elle est fausse, parce que
    le repli se declenche justement quand la nouvelle saison compte moins de
    `SEASON_MIN_MATCHES` matchs — donc en ignorant ceux qui l'ont deja rompue.

    Constate en reel : le bloc donnait « Cracovia Krakow 5N » quand la ligne
    « Forme 5 » juste au-dessus montrait un nul puis une **defaite** dans la
    nouvelle saison.

    « Total buts » ne suit pas cette regle et c'est voulu : une frequence sur
    trente-six matchs decrit encore un profil d'equipe, et son annee ecrite
    suffit a la situer."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    lignes = _lines(migrated)

    assert "(2025)" in lignes["Total buts"], "on est bien sur un repli"
    assert "Serie" not in lignes


@respx.mock
@pytest.mark.anyio
async def test_le_calendrier_annonce_le_prochain_match(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """« match 3 jours apres » est une des verifications que le prompt demande, et
    l'analyse allait la chercher a la main, match par match. Les matchs a venir
    sont dans la meme charge utile que l'historique : aucun appel de plus."""
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Calendrier"] == "BK Hacken dans 4j | Djurgardens IF dans 4j"


@respx.mock
@pytest.mark.anyio
async def test_le_calendrier_nomme_la_competition_seulement_si_elle_change(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une coupe entre deux journees de championnat est le cas interessant. Le
    repeter sous chaque affiche de championnat couterait des tokens pour ne rien
    apprendre."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    saison = load_fixture("apifootball_fixtures_season_home.json")
    for match in saison["response"]:
        if match["fixture"]["status"]["short"] == "NS":
            match["league"] = {"id": 96, "name": "Svenska Cupen", "season": 2026, "round": "1/8"}
            break
    routes["season_home"].mock(return_value=httpx.Response(200, json=saison, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    lignes = _lines(migrated)
    assert "BK Hacken dans 4j (Svenska Cupen)" in lignes["Calendrier"]
    assert "Djurgardens IF dans 4j" in lignes["Calendrier"], "meme competition : pas de nom"


@respx.mock
@pytest.mark.anyio
async def test_une_prolongation_ne_compte_que_les_90_minutes(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le marche O/U d'un bookmaker se regle sur les 90 minutes. `goals` porte le
    total prolongation comprise, `score.fulltime` le score a 90 : compter le
    premier gonflerait la frequence des « plus de 2.5 » sur toutes les coupes.

    Le cas est construit d'apres la semantique documentee des deux champs — aucun
    match de prolongation ne figurait dans l'echantillon releve."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    saison = load_fixture("apifootball_fixtures_season_home_prev.json")
    joues = [f for f in saison["response"] if f["fixture"]["status"]["short"] == "FT"]
    prolongation = joues[0]
    prolongation["fixture"]["status"]["short"] = "AET"
    prolongation["score"]["fulltime"] = {"home": 1, "away": 1}
    prolongation["score"]["extratime"] = {"home": 2, "away": 1}
    prolongation["goals"] = {"home": 3, "away": 2}
    routes["season_home_prev"].mock(
        return_value=httpx.Response(200, json=saison, headers=RATE_HEADERS)
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    passee = dossier.load(376, dossier.KIND_SEASON, "2025", migrated)
    assert passee is not None
    retenu = next(match for match in passee[0] if match["status"] == "AET")
    assert retenu["goals"] == [1, 1], "le score a 90 minutes, pas le 3-2 final"


@respx.mock
@pytest.mark.anyio
async def test_une_saison_terminee_n_est_pas_rafraichie_toutes_les_douze_heures(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Elle ne changera plus : lui appliquer la peremption de la saison en cours
    paierait un appel par equipe deux fois par jour pour reecrire les memes
    lignes."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    lendemain = NOW + timedelta(hours=24)
    await dossier.refresh_event(api_client, 1, migrated, now=lendemain)

    assert routes["season_home"].call_count == 2, "la saison en cours, oui"
    assert routes["season_home_prev"].call_count == 1, "la saison terminee, non"


def test_la_peremption_depend_du_perimetre_et_non_du_seul_type() -> None:
    assert dossier.ttl_for(dossier.KIND_SEASON, "2026", 2026) == dossier.TTL_HOURS["season"]
    assert dossier.ttl_for(dossier.KIND_SEASON, "2025", 2026) == dossier.PAST_SEASON_TTL_HOURS


def test_le_prompt_distingue_les_buts_du_match_de_ceux_de_l_equipe(
    migrated: Settings,
) -> None:
    """Deux lignes voisines, deux grandeurs differentes : « Buts marq. » ne compte
    que les buts de l'equipe, « Total buts » ceux du match. Les confondre ferait
    lire un O/U de rencontre sur une distribution par equipe."""
    from myassistantbet.services.prompt import build_prompt

    _lot(migrated, "football")

    body = build_prompt(1, settings=migrated).body

    assert "les buts **du match, les deux équipes réunies**" in body
    assert "à 90 minutes" in body, "une prolongation de coupe n'entre pas dans un O/U"
    assert "saison précédente" in body, "l'annee entre parentheses doit etre expliquee"


def test_le_prompt_donne_les_deux_causes_d_une_serie_absente(migrated: Settings) -> None:
    """Le preambule n'en nommait qu'une — « un resultat isole » — et c'etait vrai
    tant que c'etait la seule. Depuis que `Serie` se tait aussi sur un repli de
    saison, cette phrase affirme le contraire de la verite : sur un prompt reel,
    quatre blocs sur six perdaient la ligne parce que leur historique venait de
    2025, dont un Celtic sur six victoires de rang.

    Meme regle que « Non servis », qui porte ses trois causes : une absence a
    plusieurs causes se dit en entier, sinon elle en affirme une fausse. Et le
    lecteur peut les distinguer, le `(2025)` etant visible dans le meme bloc."""
    from myassistantbet.services.prompt import build_prompt

    _lot(migrated, "football")

    corps = " ".join(build_prompt(1, settings=migrated).body.split())

    assert "Deux raisons de ne pas la voir" in corps
    assert "un résultat isolé n'est pas une série" in corps
    assert "la seule série connue serait celle de la saison passée" in corps


@respx.mock
@pytest.mark.anyio
async def test_le_match_analyse_n_est_pas_son_propre_prochain_match(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Bug constate en reel sur une qualification europeenne : « Motherwell dans
    0j ». Le match analyse figure dans l'historique de sa propre equipe, et
    l'heure stockee par le fournisseur etait posterieure de peu a celle de
    l'evenement — il devenait donc sa propre echeance."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    saison = load_fixture("apifootball_fixtures_season_home.json")
    a_venir = next(f for f in saison["response"] if f["fixture"]["status"]["short"] == "NS")
    # Le match du jour, tel que le fournisseur le date : une heure plus tard.
    a_venir["fixture"]["date"] = "2026-08-03T16:30:00+00:00"
    routes["season_home"].mock(return_value=httpx.Response(200, json=saison, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "dans 0j" not in _lines(migrated)["Calendrier"]
    assert HOME not in _lines(migrated)["Calendrier"], "son prochain match est celui-la meme"


@respx.mock
@pytest.mark.anyio
async def test_un_match_reporte_n_est_pas_une_echeance_a_preparer(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Annoncer « dans 3j » sur un match reporte ferait chercher une rotation
    d'effectif pour une rencontre qui n'aura pas lieu a cette date."""
    _seed_event(migrated)
    routes = _mock_dossier(load_fixture)
    saison = load_fixture("apifootball_fixtures_season_home.json")
    for match in sorted(saison["response"], key=lambda f: f["fixture"]["date"]):
        if match["fixture"]["status"]["short"] == "NS":
            match["fixture"]["status"]["short"] = "PST"
            break
    routes["season_home"].mock(return_value=httpx.Response(200, json=saison, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    # Le suivant est au-dela de la fenetre : plus aucune echeance pour cette equipe.
    assert HOME not in _lines(migrated)["Calendrier"]


# -- Buteurs et effectif ------------------------------------------------------


@respx.mock
@pytest.mark.anyio
async def test_les_buteurs_portent_les_buts_et_la_part_de_penaltys(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les props buteurs etaient achetees sur six competitions sans qu'aucune
    ligne ne dise qui marque dans ces equipes. La part de penaltys est dite parce
    qu'elle change la nature du pari : douze buts dont dix sur penalty ne se
    parient pas comme douze buts dans le jeu."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert _lines(migrated)["Buteurs"] == (
        "BK Hacken L. Suárez 28b (4 pen), V. Pavlidis 23b (10 pen), Y. Begraoui 20b (5 pen) | "
        "Djurgardens IF C. Ramírez 18b (6 pen), R. Zalazar 16b (10 pen)"
    )


@respx.mock
@pytest.mark.anyio
async def test_les_buteurs_ne_sont_pas_payes_hors_des_competitions_a_props(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """C'est le garde-fou principal de la phase. Ailleurs que sur les six
    competitions de `PLAYER_PROPS_LEAGUES`, aucun bookmaker ne sert de props :
    la ligne n'aurait aucun marche en face, et l'appel serait paye pour des
    tokens en pure perte."""
    _seed_event(migrated, league=LEAGUE)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["scorers"].call_count == 0
    assert "Buteurs" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_les_buteurs_ne_sont_payes_qu_une_fois_pour_toute_la_competition(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le releve est range par competition et non par equipe : c'est ce qui fait
    que le cout ne croit pas avec la taille du lot. Ranger la meme liste sous
    chaque equipe la stockerait vingt fois et la paierait vingt fois."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    # Un second match de la meme competition, un autre jour.
    db.execute(
        "INSERT INTO events (id, sport_id, competition_id, home, away, commence_time, source, "
        "created_at) SELECT 2, sport_id, competition_id, home, away, commence_time, source, "
        "created_at FROM events WHERE id = 1",
        settings=migrated,
    )
    store_context(
        2, KIND_TEAMS, {"home": 376, "away": 377, "league": PROPS_LEAGUE, "season": 2026}, migrated
    )
    await dossier.refresh_event(api_client, 2, migrated, now=NOW)

    assert routes["scorers"].call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_une_equipe_sans_buteur_classe_ne_produit_aucune_ligne(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Limite assumee de l'endpoint : il ne rend que les vingt meilleurs de la
    competition. Nommer une equipe sans buteur ferait croire qu'elle n'en a pas,
    alors qu'aucun des siens n'est dans les vingt premiers."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    buteurs = load_fixture("apifootball_topscorers.json")
    for row in buteurs["response"]:
        # Tous les buteurs appartiennent a des equipes tierces.
        row["statistics"][0]["team"] = {"id": 999, "name": "Autre club"}
    routes["scorers"].mock(return_value=httpx.Response(200, json=buteurs, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "Buteurs" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_les_buteurs_non_couverts_ne_sont_pas_appeles(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Meme regle que le classement et les absents, appliquee au drapeau
    `top_scorers` : la couverture est deja en main, memorisee au rapprochement,
    donc le garde-fou ne coute pas un appel."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    store_context(
        1,
        KIND_TEAMS,
        {
            "home": 376,
            "away": 377,
            "league": PROPS_LEAGUE,
            "season": 2026,
            "coverage": {"top_scorers": False, "players": False},
        },
        migrated,
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["scorers"].call_count == 0
    assert "Buteurs" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_une_couverture_absente_du_releve_ne_bloque_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les rapprochements faits avant que la couverture soit memorisee n'en
    portent pas. Un champ absent vaut couvert, sinon la mise a jour ferait
    disparaitre des lignes qui arrivaient hier."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["scorers"].call_count == 1


@respx.mock
@pytest.mark.anyio
async def test_l_effectif_n_est_plus_appele(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """`/players/squads` a ete collecte des mois sans **aucun** lecteur : un
    appel par equipe et par mois pour une liste de noms que rien ne rendait.
    Son commentaire annoncait lui-meme sa sortie — « si rien ne le lit a terme,
    il se retire en supprimant son type ».

    Ce test garde la porte fermee. Le rouvrir demande d'abord un lecteur : sans
    statistique a cote, vingt-six noms restent du bruit dans un prompt.
    """
    _seed_event(migrated)
    _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    appels = [str(call.request.url) for call in respx.calls]
    assert not [url for url in appels if "players/squads" in url]
    assert not hasattr(dossier, "KIND_SQUAD")
    assert not hasattr(api_client, "squad")


@respx.mock
@pytest.mark.anyio
async def test_aucun_buteur_rendu_en_debut_de_saison(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Verifie en reel : en aout l'endpoint rend une liste **vide** — aucune
    journee jouee — puis, des septembre, vingt joueurs a un ou deux buts. Les
    lister ferait passer un classement de coincidences pour une hierarchie de
    buteurs, et « 1b » se lirait comme une reference."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    debut = load_fixture("apifootball_topscorers.json")
    for row in debut["response"]:
        row["statistics"][0]["goals"]["total"] = 2
    routes["scorers"].mock(return_value=httpx.Response(200, json=debut, headers=RATE_HEADERS))

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "Buteurs" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_une_liste_de_buteurs_vide_n_est_pas_redemandee_pendant_sa_duree(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La reponse d'aout est vide, et c'est une reponse : la memoriser evite de
    repayer l'appel a chaque enrichissement de la journee."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    routes["scorers"].mock(
        return_value=httpx.Response(200, json={"errors": [], "response": []}, headers=RATE_HEADERS)
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)
    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["scorers"].call_count == 1
    assert "Buteurs" not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_le_prompt_dit_ce_que_la_ligne_buteurs_ne_dit_pas(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le garde-fou compte autant que la donnee. Une equipe absente de la ligne
    n'est pas une equipe sans buteur, et un total de saison ne dit ni la
    disponibilite ni la forme du moment.

    Le lot porte de vrais buteurs : le preambule ne documente que les lignes
    presentes, et un lot sans buteur n'a pas a payer leur mode d'emploi."""
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated, league=PROPS_LEAGUE)
    _mock_dossier(load_fixture)
    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    body = build_prompt(_session_sur_event(migrated), settings=migrated, now=NOW).body

    assert "n'est pas une équipe sans buteur" in body
    assert "elle ne dit pas qui est disponible" in body
    assert "part inscrite sur penalty" in body


# -- Ce que la fiche d'un match doit faire ------------------------------------


@pytest.fixture
def client(migrated: Settings) -> Iterator[TestClient]:
    with TestClient(app) as test_client:
        yield test_client


@respx.mock
def test_le_bouton_de_la_fiche_recupere_aussi_le_dossier(
    client: TestClient,
    migrated: Settings,
    load_fixture: Any,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Constate en reel : « Rafraichir » sur la fiche d'un match ne recuperait que
    le contexte, jamais le dossier. Ce bouton et l'enrichissement d'une session
    doivent recuperer la meme chose, sinon la fiche reste sans entraineur ni
    historique sans que rien ne l'explique.

    Le plancher est abaisse parce que les fixtures de contexte simulent un quota
    d'essai — 82 appels restants — sous lequel le dossier serait suspendu a juste
    titre. Ce qui est teste ici est le branchement, pas le garde-fou.
    """
    from myassistantbet.config import get_settings

    from .test_context import _mock_all

    monkeypatch.setenv("APIFOOTBALL_CALL_FLOOR", "0")
    get_settings.cache_clear()
    _seed_event(migrated, rapproche=False)
    _mock_all(load_fixture)
    _mock_dossier(load_fixture)

    reponse = client.post("/events/1/context")

    assert reponse.status_code == 200
    assert dossier.load(376, dossier.KIND_COACH, settings=migrated) is not None
    assert "Per Gustafsson" in reponse.text, "et la fiche affiche la ligne"


def test_la_fiche_affiche_les_lignes_du_dossier(client: TestClient, migrated: Settings) -> None:
    """Sans ce rendu, tout ce qui est collecte n'existerait que dans le prompt —
    donc invisible tant qu'une session n'a pas ete montee."""
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

    page = client.get("/events/1")

    assert "Entraineur" in page.text
    # Meme fiche sans prenom ni nom de famille : aucune completion possible.
    assert "P. Gustafsson" in page.text


# -- Absences longue duree ----------------------------------------------------


#: Historique d'indisponibilite, de la forme reelle relevee sur le fournisseur :
#: des dizaines d'entrees couvrant toute la carriere, dont au plus une ouverte.
def _indispo(entrees: list[dict[str, Any]]) -> dict[str, Any]:
    return {"errors": [], "response": entrees}


@respx.mock
@pytest.mark.anyio
async def test_un_buteur_indisponible_est_dit_avec_sa_date(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """La date accompagne toujours l'absence. Ce que le fournisseur publie est un
    historique de carriere : une periode sans date de fin dit qu'il ne l'a pas
    refermee, ce qui n'est pas tout a fait une absence en cours. Datee, la ligne
    se verifie en une recherche ; seche, « absent » serait une affirmation qu'on
    ne peut pas gager."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    routes["sidelined"].mock(
        return_value=httpx.Response(
            200,
            json=_indispo(
                [
                    {"type": "Ankle Injury", "start": "2024-03-02", "end": "2024-04-01"},
                    {"type": "Knee Injury", "start": "2026-07-12", "end": None},
                ]
            ),
            headers=RATE_HEADERS,
        )
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "L. Suárez absent depuis 12/07" in _lines(migrated)["Buteur abs."]


@respx.mock
@pytest.mark.anyio
async def test_une_indisponibilite_refermee_avant_le_match_ne_produit_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le joueur est revenu : l'annoncer absent serait faux, et c'est le piege
    d'un historique de carriere lu comme un etat du jour."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    routes["sidelined"].mock(
        return_value=httpx.Response(
            200,
            json=_indispo([{"type": "Knee Injury", "start": "2026-05-02", "end": "2026-06-30"}]),
            headers=RATE_HEADERS,
        )
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "Buteur abs." not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_une_indisponibilite_posterieure_au_match_ne_produit_rien(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une periode qui commence apres le match ne dit rien de ce match-la."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    routes["sidelined"].mock(
        return_value=httpx.Response(
            200,
            json=_indispo([{"type": "Knee Injury", "start": "2026-09-01", "end": None}]),
            headers=RATE_HEADERS,
        )
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "Buteur abs." not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_l_indisponibilite_n_est_demandee_que_pour_les_buteurs_rendus(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un appel par joueur : payer l'absence d'un joueur que le bloc ne nomme pas
    serait acheter une donnee que rien ne lira. Trois buteurs par equipe rendus,
    donc six appels au plus — pas les trente-six joueurs d'un effectif."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    # Trois buteurs a domicile, deux a l'exterieur dans la fixture.
    assert routes["sidelined"].call_count == 5
    demandes = {int(appel.request.url.params["player"]) for appel in routes["sidelined"].calls}
    rendus = _lines(migrated)["Buteurs"]
    assert len(demandes) == 5
    assert all(str(joueur) not in rendus for joueur in demandes), "des identifiants, pas des noms"


@respx.mock
@pytest.mark.anyio
async def test_aucune_indisponibilite_demandee_hors_des_competitions_a_props(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sans buteurs identifies, aucun joueur a interroger. C'est la portee reelle
    de cette donnee : verifie en reel, `/sidelined` repond pour n'importe quel
    joueur mais ne rend aucune entree hors des competitions dont le fournisseur
    couvre les blessures."""
    _seed_event(migrated, league=LEAGUE)
    routes = _mock_dossier(load_fixture)

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert routes["sidelined"].call_count == 0
    assert "Buteur abs." not in _lines(migrated)


@respx.mock
@pytest.mark.anyio
async def test_la_periode_la_plus_recente_prime(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un historique de carriere en compte des dizaines, et une ancienne periode
    jamais refermee par le fournisseur ne doit pas masquer la blessure du mois."""
    _seed_event(migrated, league=PROPS_LEAGUE)
    routes = _mock_dossier(load_fixture)
    routes["sidelined"].mock(
        return_value=httpx.Response(
            200,
            json=_indispo(
                [
                    {"type": "Groin Injury", "start": "2019-02-01", "end": None},
                    {"type": "Calf Injury", "start": "2026-07-20", "end": None},
                ]
            ),
            headers=RATE_HEADERS,
        )
    )

    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    assert "absent depuis 20/07" in _lines(migrated)["Buteur abs."]


@respx.mock
@pytest.mark.anyio
async def test_le_prompt_traite_une_absence_comme_une_piste_datee(
    api_client: APIFootballClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le garde-fou compte autant que la donnee. Une periode sans date de fin dit
    que le fournisseur ne l'a pas refermee, pas qu'un joueur est forcement absent
    aujourd'hui — et la recherche doit pouvoir la contredire."""
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated, league=PROPS_LEAGUE)
    _mock_dossier(load_fixture)
    await dossier.refresh_event(api_client, 1, migrated, now=NOW)

    body = build_prompt(_session_sur_event(migrated), settings=migrated, now=NOW).body

    assert "Buteur abs." in body
    assert "datée à confirmer, pas comme un fait" in body
    assert "c'est ta recherche qui gagne" in body
    assert "L'absence de cette ligne ne prouve rien" in body


def _session_sur_event(settings: Settings, event_id: int = 1) -> int:
    """Une session portant cet evenement deja renseigne, et son identifiant.

    Le preambule ne documente pas seulement les **sports** du lot : il ne
    documente que les **lignes de contexte reellement presentes**. Un lot sans
    buteur n'a donc aucun mode d'emploi des buteurs a payer — ce qui oblige les
    tests de garde-fou a porter sur un lot qui en a. Un evenement vide les
    ferait passer sans que le parcours reel fonctionne.
    """
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (1, ?)",
        (event_id,),
        settings=settings,
    )
    return 1


def _lot(settings: Settings, sport: str) -> int:
    """Une session portant un match de ce sport, et son identifiant.

    Le preambule du prompt ne documente que les sports **presents dans le lot** :
    une session de football n'a pas a payer les quarante lignes d'explication du
    tennis. Ces tests portent donc sur un lot du bon sport — sur une session
    vide, aucun garde-fou ne se rendrait, et pour cause.
    """
    row = db.query_one(f"SELECT id FROM sports WHERE key = '{sport}'", settings=settings)
    db.execute(
        "INSERT INTO events (id, sport_id, home, away, commence_time, source, created_at) "
        "VALUES (900, ?, 'A', 'B', '2099-01-01T18:00:00Z', 'api', ?)",
        (row["id"], db.utcnow()),
        settings=settings,
    )
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=settings,
    )
    db.execute(
        "INSERT INTO session_events (session_id, event_id) VALUES (1, 900)", settings=settings
    )
    return 1


# -- L'entraineur : trois etats, et le troisieme est le cas ordinaire --------


@pytest.mark.parametrize(
    ("feuille", "fiche"),
    [
        ("Laurent Guyot", "L. Guyot"),
        ("Sylvain Ripoll", "S. Ripoll"),
        ("Olivier Frapolli", "O. Frapolli"),
        ("Pablo Correa", "P. Correa"),
        # Le cas diacritique : le repli s'applique **des deux cotes**, sinon un
        # trema suffit a faire deux hommes d'un seul.
        ("Nicolas Usai", "N. Usaï"),
        ("Alexander Blessin", "A. Blessin"),
        # Un nom de famille compose contre deux prenoms : la comparaison se fait
        # en suffixe, exiger la meme longueur inventerait une divergence.
        ("João Pedro Machado Sacramento", "J. Machado Sacramento"),
    ],
)
def test_un_prenom_abrege_ne_fait_pas_deux_hommes(feuille: str, fiche: str) -> None:
    """**Mesure du 14/08/2026 : 20 paires annoncees « divergence », dont 10 sont
    le meme homme sous deux ecritures.**

    La comparaison stricte les declarait differents, et la ligne la plus
    decisive du dossier d'equipe se noyait dans son propre bruit.
    """
    assert dossier._coach_match({"name": fiche}, {"name": feuille}) == dossier.COACH_INITIAL


@pytest.mark.parametrize(
    ("feuille", "fiche"),
    [
        ("Ian Cathro", "Philippe Montanier"),
        ("David Vignes", "M. Pires"),
        ("Dietmar Kuhbauer", "João Pedro Machado Sacramento"),
        ("Lars Friis", "O. Cinel"),
        ("Tiago Margarido", "Gil Lameiras"),
        ("Tian Tang", "Peng Han"),
        # Meme initiale, nom de famille different : c'est bien une divergence.
        ("Mario Despotovic", "M. Senft"),
        ("David Guion", "G. Proment"),
    ],
)
def test_une_vraie_divergence_reste_dite(feuille: str, fiche: str) -> None:
    assert dossier._coach_match({"name": fiche}, {"name": feuille}) == dossier.COACH_DIFFERENT


def test_deux_libelles_identiques_concluent(migrated: Settings) -> None:
    """L'egalite stricte, elle, tranche : c'est le meme libelle."""
    assert dossier._coach_match({"name": "Derek McInnes"}, {"name": "derek mcinnes"}) == (
        dossier.COACH_SAME
    )


def test_l_identifiant_prime_sur_le_libelle() -> None:
    """Deux libelles du meme homme figurent dans la meme fiche : l'identifiant
    tranche quand les deux sources le portent."""
    assert dossier._coach_match({"id": 7, "name": "D. McInnes"}, {"id": 7, "name": "Derek"}) == (
        dossier.COACH_SAME
    )
    assert dossier._coach_match(
        {"id": 7, "name": "A. Blessin"}, {"id": 9, "name": "A. Blessin"}
    ) == (dossier.COACH_DIFFERENT)


def test_la_ligne_d_initiale_ne_conclut_pas_et_porte_le_prenom_entier(
    migrated: Settings,
) -> None:
    """**Le cas indecidable se nomme.**

    Deux prenoms partageant l'initiale et le nom sont deux hommes differents que
    la regle declarera compatibles — rare, mais les fratries existent au
    football. La ligne dit donc sur quoi elle repose au lieu de trancher, et
    porte le prenom **entier** de la feuille : c'est lui qui rend une recherche
    possible, la fiche l'abregeant.
    """
    dossier.store(
        12,
        dossier.KIND_COACH,
        [{"id": 1, "name": "N. Usaï", "career": [{"team": {"id": 12}, "start": "2026-07-01"}]}],
        settings=migrated,
    )

    ligne = dossier._coach_fragment(
        "Stade de Reims",
        12,
        NOW,
        migrated,
        observed={"name": "Nicolas Usai", "seen": "2026-08-08T18:00:00+00:00"},
        sheets_read=True,
    )

    assert "Nicolas Usai" in ligne, "le prenom entier vient de la feuille"
    assert "apparié sur l'initiale du prénom" in ligne
    assert "divergence" not in ligne
    assert "vu sur la feuille" not in ligne, "elle ne conclut pas non plus a l'identite"


# -- Le cas muet a deux causes, et une seule se dit --------------------------


def _fiche(settings: Settings, team_id: int = 12, nom: str = "C. Eichner") -> None:
    dossier.store(
        team_id,
        dossier.KIND_COACH,
        [{"id": 1, "name": nom, "career": [{"team": {"id": team_id}, "start": "2020-02-01"}]}],
        settings=settings,
    )


def test_une_competition_sans_compositions_le_dit_sur_la_ligne(migrated: Settings) -> None:
    """**Le cas le plus dangereux etait le plus muet.**

    Sur le lot du 21/08, les deux entraineurs de M2 — DFB-Pokal — etaient faux,
    dont un credite de six ans d'anciennete, et la ligne ne portait **aucune**
    mention. La cause est structurelle : cette competition declare
    `injuries: false` **et** `fixtures.lineups: false`, donc aucune feuille ne
    peut etre lue et rien ne recoupe la fiche.

    C'est l'exact contraire de l'angle mort deja documente — les competitions
    bien couvertes, ou les feuilles ne sont pas necessaires. Une chose qu'on n'a
    pas verifiee et une chose qu'on ne **peut pas** verifier ne s'ecrivent pas
    pareil, meme discipline que les trois etats d'`Absents`.
    """
    _fiche(migrated)

    ligne = dossier._coach_fragment(
        "Karlsruher SC", 12, NOW, migrated, sheets_read=False, sheets_possible=False
    )

    assert "fiche seule, aucune feuille servie ici" in ligne
    assert "C. Eichner" in ligne


def test_une_competition_bien_couverte_ne_paie_aucune_mention(migrated: Settings) -> None:
    """**34 % des evenements, et la mention y serait du decor.**

    Quand `/injuries` couvre, les feuilles ne sont pas telechargees parce
    qu'elles ne servent a rien d'autre — pas parce qu'elles manquent. Ecrire la
    mention sur un bloc sur trois la ferait cesser d'etre un signal, exactement
    le defaut des deux seuils egaux.
    """
    _fiche(migrated)

    ligne = dossier._coach_fragment(
        "IK Sirius", 12, NOW, migrated, sheets_read=False, sheets_possible=True
    )

    assert "fiche seule" not in ligne
    assert "non confirme" not in ligne
    assert ligne.startswith("IK Sirius C. Eichner")


def test_une_couverture_inconnue_n_affirme_aucune_absence(migrated: Settings) -> None:
    """On n'affirme pas une absence qu'on n'a pas lue. `_sheets_possible` rend
    donc **vrai** par defaut : seul un `lineups: false` constate fait paraitre
    la mention."""
    assert dossier._sheets_possible({}) is True
    assert dossier._sheets_possible({"coverage": {}}) is True
    assert dossier._sheets_possible({"coverage": {"fixtures": {}}}) is True
    assert dossier._sheets_possible({"coverage": {"fixtures": {"lineups": True}}}) is True
    assert dossier._sheets_possible({"coverage": {"fixtures": {"lineups": False}}}) is False


def test_une_feuille_lue_sans_entraineur_prime_sur_la_couverture(migrated: Settings) -> None:
    """Les deux etats ne peuvent pas coexister, et c'est le releve qui gagne :
    si une feuille a ete lue, la couverture declaree est dementie par le fait."""
    _fiche(migrated)

    ligne = dossier._coach_fragment(
        "Karlsruher SC", 12, NOW, migrated, sheets_read=True, sheets_possible=False
    )

    assert "non confirme" in ligne
    assert "aucune feuille servie ici" not in ligne


def test_le_chapitre_definit_chaque_mention_que_le_code_produit(migrated: Settings) -> None:
    """**Un libelle sans definition dans le chapitre est le defaut que ce prompt
    evite partout**, et il y en avait deux : « non confirme », rendu sur 6 % des
    fragments, et « (feuille du JJ/MM) ». Le chapitre annoncait « trois
    mentions » quand le code en produit cinq.

    Le test enonce la propriete — chaque mention produite est definie — et non
    la liste du jour.
    """
    from myassistantbet.services.prompt import build_prompt

    _seed_event(migrated)
    dossier.store(
        376,
        dossier.KIND_COACH,
        [{"name": "P. Gustafsson", "career": [{"team": {"id": 376}, "start": "2023-06-01"}]}],
        settings=migrated,
    )
    db.execute(
        "INSERT INTO sessions (id, label, created_at) VALUES (1, 'test', ?)",
        (db.utcnow(),),
        settings=migrated,
    )
    db.execute("INSERT INTO session_events (session_id, event_id) VALUES (1, 1)", settings=migrated)

    corps = build_prompt(1, settings=migrated, now=NOW).body
    chapitre = corps.split("**« Entraîneur »**")[1]

    for mention in (
        "vu sur la feuille du JJ/MM",
        "apparié sur l'initiale du prénom",
        "divergence",
        "non confirmé",
        "fiche seule, aucune feuille servie ici",
    ):
        assert mention in chapitre, f"« {mention} » n'est definie nulle part"
    # Et le cas majoritaire — la ligne nue — est dit lui aussi : 45 a 59 % des
    # fragments ne portent aucune mention, et le chapitre n'en disait rien.
    assert "Sans mention, la fiche est seule" in chapitre


# -- Le nom de l'entraineur : le fournisseur en sert trois, et abrege le sien --


def test_un_nom_abrege_se_complete_par_le_prenom_et_le_nom_de_la_fiche() -> None:
    """**Le champ propre etait a cote du champ casse.**

    Mesure du 21/08/2026 sur les 1 631 fiches en base : **970 portent un nom
    abrege**, dont 876 completables. Cas emblematique et il vient du
    fournisseur — Sebastian Hoeneß y figure sous `name = "S. Hoeneb"`, le ß rendu
    par un b, quand `lastname` porte « Hoeneß » sans faute.
    """
    from myassistantbet.services.dossier import coach_name

    assert coach_name({"name": "S. Hoeneb", "firstname": "Sebastian", "lastname": "Hoeneß"}) == (
        "Sebastian Hoeneß"
    )


def test_un_nom_d_usage_complet_ne_se_remplace_jamais() -> None:
    """**Et c'est ce qui interdit le correctif naif.**

    `firstname + lastname` rend « Enrique Setién Solar » la ou `name` dit
    « Quique Setién », et « Jesús Rodríguez Tato » la ou il dit « Tato ». Le nom
    d'usage est celui avec lequel on cherche : il ne se remplace que lorsqu'il
    est **abrege**, jamais parce qu'il est court.
    """
    from myassistantbet.services.dossier import coach_name

    fiche = {"name": "Quique Setién", "firstname": "Enrique", "lastname": "Setién Solar"}
    assert coach_name(fiche) == "Quique Setién"
    assert coach_name({"name": "Xie Feng", "firstname": "Feng", "lastname": "Xie"}) == "Xie Feng"


def test_aucune_concordance_n_est_exigee_entre_les_champs() -> None:
    """**Il n'y a rien a apparier : les trois champs decrivent la meme fiche.**

    Le premier jet exigeait que le nom de famille abrege se retrouve dans
    `lastname`. Cette condition refusait 42 completions justes — noms composes,
    accents polonais, particules — et laissait passer le seul cas ou le champ
    ment vraiment.
    """
    from myassistantbet.services.dossier import coach_name

    assert coach_name(
        {"name": "A. Franco", "firstname": "Antonio", "lastname": "Franco López"}
    ) == ("Antonio Franco López")
    assert (
        coach_name({"name": "R. Demil", "firstname": "Rik", "lastname": "De Mil"}) == "Rik De Mil"
    )


def test_une_fiche_sans_prenom_garde_son_abrege() -> None:
    """94 fiches sur 970 n'ont ni prenom ni nom de famille : l'abrege est alors
    tout ce qu'on a, et inventer serait pire."""
    from myassistantbet.services.dossier import coach_name

    assert coach_name({"name": "H. Hansen"}) == "H. Hansen"
    assert coach_name({"name": "H. Hansen", "firstname": "", "lastname": ""}) == "H. Hansen"


def test_un_second_prenom_ne_fait_pas_deux_hommes() -> None:
    """**Apparu en completant, et mesure avant d'etre corrige.**

    Comparer deux noms complets faisait tomber trois paires en « divergence »
    qui sont le meme homme : la fiche porte un second prenom que la feuille omet,
    ou le prenom entier la ou la feuille abrege.
    """
    from myassistantbet.services.dossier import COACH_SAME, _coach_match

    for fiche, feuille in (
        ("Alexander Matthias Blessin", "Alexander Blessin"),
        ("Desmond Buckingham", "Des Buckingham"),
        ("Fabian Marc Hürzeler", "Fabian Hurzeler"),
    ):
        assert _coach_match({"name": fiche}, {"name": feuille}) == COACH_SAME, fiche


def test_deux_freres_restent_deux_hommes() -> None:
    """Le nom de famille porte l'identite, les prenoms doivent etre compatibles.
    Deux prenoms differents partageant le nom sont deux personnes — les fratries
    existent au football, et c'est ce que la tolerance ne doit pas avaler."""
    from myassistantbet.services.dossier import COACH_SAME, _coach_match

    assert _coach_match({"name": "Alexander Zverev"}, {"name": "Mischa Zverev"}) != COACH_SAME
