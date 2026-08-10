from __future__ import annotations

from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
import pytest
import respx
from jinja2 import TemplateNotFound

from myassistantbet import db
from myassistantbet.config import Settings
from myassistantbet.providers.apifootball import APIFootballClient
from myassistantbet.providers.oddsapi import BASE_URL, OddsAPIClient
from myassistantbet.services import board as board_service
from myassistantbet.services import dossier
from myassistantbet.services import session as session_service
from myassistantbet.services.context import fetch_context
from myassistantbet.services.enrich import run_enrich
from myassistantbet.services.manual import build, save
from myassistantbet.services.prompt import (
    DEFAULT_TEMPLATE,
    build_prompt,
    date_fr,
    list_templates,
    load_tiers,
    save_prompt,
)
from myassistantbet.services.scan import active_competitions, run_scan

from .helpers import (
    DOSSIER_RATE_HEADERS,
    LEAGUE,
    NOW,
    QUOTA_HEADERS,
    mock_context_routes,
    mock_dossier_routes,
)

EVENT_ID = "3c7f9a1b2d4e5f60718293a4b5c6d7e8"
PARIS = ZoneInfo("Europe/Paris")

#: Plafond de tokens d'un lot de six matchs de football entierement enrichis.
#:
#: Recale apres avoir mesure le meme lot des deux cotes : la fixture ne portait
#: que des cotes et tombait a **6572**, la production a **8304**. Les 8000
#: d'origine mesuraient donc un squelette, et les 1400 tokens de marge apparente
#: n'existaient pas — le prompt reel les avait franchis sans que rien ne bronche,
#: ce qui a fausse toute une discussion sur le cout d'une consigne ajoutee.
#:
#: La fixture enrichie mesure **8957**, soit un peu plus que la production : ses
#: six blocs sont tous complets quand un vrai lot en porte de plus pauvres. C'est
#: le pire cas, et c'est ce qu'un plafond doit mesurer.
#:
#: **Ce nombre est une alarme, pas un budget, et la difference a ete tranchee
#: par l'utilisateur** : un prompt long ne le gene pas, quitte a ce que l'analyse
#: prenne dix minutes de plus. Le plafond a donc cesse d'arbitrer les ajouts —
#: il ne sert plus qu'a rattraper une explosion **involontaire**, du genre d'une
#: porte de preambule cassee qui rendrait tout le mode d'emploi sur chaque lot,
#: ou d'un bloc duplique.
#:
#: Il vaut la mesure **plus environ 2000 tokens**, soit largement au-dessus de
#: ce qu'un ajout delibere peut couter et largement en dessous d'un rendu qui
#: derape. A ~500 il transformait chaque ligne ajoutee en arbitrage, et trois
#: sessions de suite s'y sont usees.
#:
#: Ce qui n'a pas change : la densite reste un objectif de qualite — une ligne
#: sans donnee est omise, un mode d'emploi se garde sur son libelle. Le plafond
#: ne remplace pas ces regles, il ne les faisait deja pas respecter.
PROMPT_BUDGET = 11500


async def _session_enrichie(
    client: OddsAPIClient, settings: Settings, load_fixture: Any, *, enrich: bool = True
) -> int:
    for competition in active_competitions(settings):
        key = competition["oddsapi_key"]
        respx.get(f"{BASE_URL}/sports/{key}/odds").mock(
            return_value=httpx.Response(
                200,
                json=load_fixture("oddsapi_allsvenskan_scan.json")
                if key == "soccer_sweden_allsvenskan"
                else [],
                headers=QUOTA_HEADERS,
            )
        )
    await run_scan(client, settings, now=NOW)

    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=settings)
    session_id = board_service.toggle_selection(int(event["id"]), True, settings)

    if enrich:
        respx.get(f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/{EVENT_ID}/odds").mock(
            return_value=httpx.Response(
                200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
            )
        )
        await run_enrich(client, session_id, settings, now=NOW)
    return session_id


# -- Briques ----------------------------------------------------------------


def test_templates_disponibles() -> None:
    names = list_templates()

    assert DEFAULT_TEMPLATE in names
    assert names[0] == DEFAULT_TEMPLATE, "le defaut est propose en premier"


def test_paliers_lus_en_base(migrated: Settings) -> None:
    tiers = load_tiers(migrated)

    assert [tier.key for tier in tiers] == ["safe", "fun", "ultra_fun", "giga_fun", "giga_plus"]
    assert tiers[0].range_label == "1.25 – 1.70"
    assert tiers[0].quota_label == "2-4 🟢"
    assert tiers[-1].range_label.startswith("> 15.00")


@pytest.mark.parametrize(
    ("moment", "expected"),
    [
        (datetime(2026, 8, 3, tzinfo=PARIS), "lundi 3 août 2026"),
        (datetime(2026, 12, 25, tzinfo=PARIS), "vendredi 25 décembre 2026"),
    ],
)
def test_date_en_francais(moment: datetime, expected: str) -> None:
    assert date_fr(moment) == expected


# -- Assemblage -------------------------------------------------------------


@respx.mock
async def test_prompt_contient_les_sections_attendues(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=datetime(2026, 8, 3, tzinfo=PARIS)).body

    assert "# SESSION D'ANALYSE — lundi 3 août 2026" in body
    assert "## TON RÔLE" in body
    assert "## MÉTHODE" in body
    assert "## CE QU'IL FAUT VÉRIFIER" in body
    assert "## MATCHS" in body
    # Les faits avant les opinions : la fiche precede l'analyse dans le corps.
    assert body.index("### A. Fiche de vérification") < body.index("### B. Analyse par match")
    assert "### E. Le match que tu ne jouerais pas" in body
    assert "### F. Ce qui aurait changé ton analyse" in body


@respx.mock
async def test_le_prompt_interdit_toujours_le_calcul_de_value(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Garde-fou de la section 9 de SPEC.md : le prompt doit porter l'interdit."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    for interdit in ("value bet", "EV", "edge", "CLV", "devigging", "Kelly"):
        assert interdit in body, f"« {interdit} » doit rester explicitement interdit"


def _lot_de(migrated: Settings, matchs: int) -> int:
    """Un lot de `matchs` affiches de football, montees a la main."""
    session_id = 0
    for index in range(matchs):
        event_id = save(
            build(
                "football",
                "Match amical",
                f"Lyon {index}",
                f"Nice {index}",
                "2026-08-04",
                "20:45",
                f"Lyon {index} 2.10\nNul 3.40\nNice {index} 3.20",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)
    return session_id


def test_le_prompt_fait_compter_les_lignes_de_vainqueur(migrated: Settings) -> None:
    """Section B rappelait deja que le vainqueur est le plus grossier des
    debouches, sans que rien ne verifie jamais la forme du tableau rendu. Mesure
    sur les selections reelles : 28 des 35 selections tennis tranchees portaient
    sur un « Vainqueur », a 13/28 — le plus gros regroupement de la base et le
    plus faible, quand il ne restait que six handicaps jeux et un total.

    Le controle porte sur le **lot** et jamais sur une selection prise seule : si
    tous les angles decrivent bien une issue, le tableau est juste et le dit."""
    # Le texte est justifie a une largeur fixe : chercher une phrase entiere
    # dans le corps brut la couperait a la premiere fin de ligne.
    corps = " ".join(build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body.split())

    assert "sa **nature en un mot — « issue » ou « manière »**" in corps
    assert "Compte tes lignes avant de rendre" in corps
    assert "plus de la moitié du tableau porte sur le marché du vainqueur" in corps
    assert "garde-les et dis-le en une ligne sous le tableau" in corps


def test_le_prompt_refuse_les_lignes_en_quart_au_football(migrated: Settings) -> None:
    """Mesure sur une analyse reelle : les **deux** selections rendues portaient
    une ligne en quart — « Over 2.75 » et « Slask -0.25 », toutes deux a `(ref.)`
    — donc deux paris impossibles a poser chez le bookmaker principal. Un pari
    asiatique scinde n'existe pas sur le marche français, quel que soit le book.

    La ligne reste affichee, parce qu'elle situe le match mieux qu'aucune autre ;
    c'est la **selection** qui est interdite. Sa cote entrerait sinon en base et
    fausserait le palier comme le taux de reussite, exactement comme une cote
    inventee — la meme raison qui tient le score exact en sets hors du tableau."""
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "les lignes en quart ne se posent pas" in corps
    assert "elles ne deviennent jamais une sélection" in corps
    assert "X ne perd pas, ou perd d'un but exactement" in corps
    assert "n'entre pas dans ce tableau" in corps
    assert "sans cote et hors de tout combiné" in corps


def test_les_lignes_entieres_et_demies_restent_selectionnables(migrated: Settings) -> None:
    """Le garde-fou ne doit pas repeter l'erreur du libelle « Non jouable », qui
    a fait renoncer a des paris posables : seules les lignes en quart sortent, et
    le prompt nomme l'equivalent des autres pour qu'aucune ne parte avec elles."""
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "gardent un équivalent direct et restent sélectionnables" in corps
    assert "remboursé si match nul" in corps
    assert "le handicap européen" in corps


def test_aucune_double_ligne_vide_dans_un_prompt(migrated: Settings) -> None:
    """Chaque porte du preambule laisse sa ligne vide quand elle ne rend rien :
    un lot de tennis en portait onze coupures de deux lignes ou plus, dont une de
    quatre. Regler les blancs porte par porte marche une fois puis se defait a la
    porte suivante — et il s'en ajoute a chaque ligne de contexte documentee."""
    corps = build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body

    assert "\n\n\n" not in corps
    assert "\n\n" in corps, "les paragraphes restent separes"


def test_le_comptage_se_tait_sur_un_lot_trop_court(migrated: Settings) -> None:
    """« Plus de la moitie » ne decrit rien sur deux ou trois lignes, et les
    quotas se reduisent deja a proportion du lot. Meme regle que partout
    ailleurs : sous quelques observations, une proportion ne dit rien.

    Les deux morceaux tombent **ensemble**, parce qu'ils n'en font qu'un : le mot
    de la section B n'existe que pour etre relu au moment du comptage. Garder le
    premier sans le second aurait coute au budget de tokens du lot le plus lourd
    — trois sports pour trois matchs — sans rien mettre en face."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "Compte tes lignes avant de rendre" not in corps
    assert "nature en un mot" not in corps
    assert "puis le marché qui le traduit le mieux" in corps, "la consigne d'origine tient"
    assert "le plus grossier des débouchés d'une analyse" in corps, "le rappel de fond reste"


@respx.mock
async def test_prompt_contient_scores_exacts_et_over_under(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Score exact" in body
    assert "1-1 6.50" in body
    assert "O/U" in body
    assert "2.5: 1.72/2.05" in body
    assert "BTTS        Oui 1.60 / Non 2.25" in body
    assert "Corners     O/U 9.5: 1.85/1.90" in body


@respx.mock
async def test_paliers_injectes_dans_le_prompt(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "🟢 SAFE         1.25 – 1.70" in body
    assert "💥 GIGA+" in body
    assert "Quotas indicatifs : 2-4 🟢, 3-5 🔵, 2-4 🟠, 1-3 🔴, 0-2 💥." in body


@respx.mock
async def test_note_perso_injectee_telle_quelle(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT id FROM events WHERE home = 'BK Hacken'", settings=migrated)
    session_service.set_note(session_id, int(event["id"]), "Gardien n°2 annoncé", migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "NOTE PERSO  Gardien n°2 annoncé" in body


@respx.mock
async def test_prompt_reste_sous_le_budget_pour_six_matchs(
    odds_client: OddsAPIClient,
    api_client: APIFootballClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Critere d'acceptation de la phase 2, avec le pire cas : 6 matchs enrichis.

    **« Enrichi » veut dire cotes profondes ET bloc CONTEXTE complet**, et la
    nuance a fini par tout changer. Ce test ne clonait que les cotes : il
    mesurait 6572 tokens quand un vrai lot de six matchs de football en pesait
    8304 — mille sept cents d'ecart, soit tout ce que les phases 11 a 15 ont
    ajoute au bloc et que la fixture n'a jamais recu. Le plafond avait donc
    l'air d'avoir 1400 tokens de marge alors qu'il etait franchi en production
    depuis des mois, et c'est ce qui a rendu la discussion sur le cout d'une
    consigne ajoutee au prompt entierement fausse.
    """
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    source = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    deep = db.query("SELECT * FROM odds WHERE event_id = ?", (source["id"],), settings=migrated)

    # Le contexte et le dossier passent par leur vrai parcours, une fois, puis
    # se recopient comme les cotes : c'est le bloc qui est mesure, pas le nombre
    # d'appels. `KIND_TEAMS` est recopie avec le reste, donc chaque clone
    # retrouve les memes identifiants d'equipe et donc le meme dossier.
    mock_context_routes(load_fixture, DOSSIER_RATE_HEADERS)
    mock_dossier_routes(load_fixture)
    await fetch_context(
        api_client,
        {
            "id": source["id"],
            "home": source["home"],
            "away": source["away"],
            "commence_time": source["commence_time"],
            "apifootball_league_id": LEAGUE,
        },
        migrated,
    )
    await dossier.refresh_event(api_client, int(source["id"]), migrated, now=NOW)
    context_rows = db.query(
        "SELECT kind, payload_json, fetched_at FROM context WHERE event_id = ?",
        (source["id"],),
        settings=migrated,
    )
    assert len(context_rows) >= 8, "sans bloc CONTEXTE, la mesure ne vaut rien"

    for index in range(5):
        db.execute(
            "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
            "commence_time, source, created_at) VALUES (?, ?, ?, ?, ?, ?, 'api', ?)",
            (
                source["sport_id"],
                source["competition_id"],
                f"clone-{index}",
                f"Equipe A{index}",
                f"Equipe B{index}",
                source["commence_time"],
                db.utcnow(),
            ),
            settings=migrated,
        )
        clone = db.query_one(
            "SELECT id FROM events WHERE oddsapi_event_id = ?",
            (f"clone-{index}",),
            settings=migrated,
        )
        with db.connect(migrated) as conn:
            for row in deep:
                conn.execute(
                    "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, "
                    "description, point, price, fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        clone["id"],
                        row["bookmaker"],
                        row["market_key"],
                        row["outcome_name"],
                        row["description"],
                        row["point"],
                        row["price"],
                        row["fetched_at"],
                    ),
                )
            for row in context_rows:
                conn.execute(
                    "INSERT INTO context (event_id, kind, payload_json, fetched_at) "
                    "VALUES (?, ?, ?, ?)",
                    (clone["id"], row["kind"], row["payload_json"], row["fetched_at"]),
                )
        board_service.toggle_selection(int(clone["id"]), True, migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 6
    assert "Classement" in prompt.body and "Entraineur" in prompt.body, "contexte et dossier"
    assert prompt.token_estimate < PROMPT_BUDGET, (
        f"prompt trop lourd : {prompt.token_estimate} tokens"
    )


@respx.mock
async def test_matchs_numerotes_dans_l_ordre(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    second = db.query_one("SELECT id FROM events WHERE home = 'IFK Norrkoping'", settings=migrated)
    session_id = board_service.toggle_selection(int(second["id"]), True, migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "### M1 · FOOT · Allsvenskan · BK Hacken – Djurgardens IF" in body
    assert "### M2 · FOOT · Allsvenskan · IFK Norrkoping – Malmo FF" in body


def test_session_vide_produit_un_prompt_sans_bloc(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    assert prompt.blocks == 0
    assert "## MATCHS" in prompt.body


def test_template_inconnu_refuse(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)

    with pytest.raises(TemplateNotFound):
        build_prompt(session_id, "inexistant.md.j2", migrated)


# -- Sauvegarde -------------------------------------------------------------


def test_prompt_sauvegarde_en_base(migrated: Settings) -> None:
    session_id = board_service.current_session(migrated)
    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    prompt_id = save_prompt(session_id, prompt, migrated)

    row = db.query_one("SELECT * FROM prompts WHERE id = ?", (prompt_id,), settings=migrated)
    assert row["session_id"] == session_id
    assert row["template_name"] == DEFAULT_TEMPLATE
    assert row["body"] == prompt.body
    assert row["token_estimate"] == prompt.token_estimate > 0


@respx.mock
async def test_le_prompt_archive_les_matchs_qu_il_porte(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le denominateur du taux de selection s'enregistre a la generation.

    La shortlist ne peut pas le fournir : elle se vide a mesure qu'on decoche,
    et une session reelle porte 4 lignes de shortlist pour 29 selections.
    """
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    prompt = build_prompt(session_id, settings=migrated, now=NOW)

    prompt_id = save_prompt(session_id, prompt, migrated)

    archives = {
        row["event_id"]
        for row in db.query(
            "SELECT event_id FROM prompt_events WHERE prompt_id = ?",
            (prompt_id,),
            settings=migrated,
        )
    }
    assert archives == set(prompt.event_ids)
    assert len(archives) == prompt.blocks > 0


@respx.mock
async def test_regenerer_le_meme_lot_ne_le_gonfle_pas(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le lot compte des **matchs**, pas des prompts.

    C'est ce qui le rend juste sur une journee reelle : le 09/08, seize prompts
    ont ete generes, et le lot ne doit valoir que le nombre de matchs distincts
    qui y sont entres.
    """
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    prompt = build_prompt(session_id, settings=migrated, now=NOW)
    for _ in range(3):
        save_prompt(session_id, prompt, migrated)

    lot = db.query_one(
        "SELECT COUNT(DISTINCT pe.event_id) AS lot FROM prompts p "
        "JOIN prompt_events pe ON pe.prompt_id = p.id WHERE p.session_id = ?",
        (session_id,),
        settings=migrated,
    )["lot"]

    assert lot == prompt.blocks


# -- Contexte sportif dans le prompt ----------------------------------------


@respx.mock
async def test_le_prompt_contient_le_bloc_contexte(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Critere d'acceptation de la phase 3, vu depuis le prompt final."""
    from myassistantbet.providers.apifootball import APIFootballClient
    from myassistantbet.services.context import fetch_context

    from .test_context import _mock_all

    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    _mock_all(load_fixture)
    await fetch_context(
        APIFootballClient(http_client, migrated),
        {
            "id": event["id"],
            "home": event["home"],
            "away": event["away"],
            "commence_time": event["commence_time"],
            "apifootball_league_id": 113,
        },
        migrated,
    )

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "CONTEXTE" in body
    assert "  Classement  BK Hacken 4e (34pts, 16j)" in body
    assert "  Forme 5     BK Hacken VVNDV (9-4/5)" in body
    assert "  Absents     BK Hacken — M. Rygaard" in body
    assert "  H2H (3)     1-1 · 0-2 D · 2-2" in body
    # Le bloc MARCHES suit immediatement le contexte, sans ligne vide parasite.
    assert "MARCHES (Betclic" in body


@respx.mock
async def test_absents_des_deux_equipes_sur_deux_lignes_alignees(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    from myassistantbet.providers.apifootball import APIFootballClient
    from myassistantbet.services.context import fetch_context

    from .test_context import _mock_all

    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    event = db.query_one("SELECT * FROM events WHERE home = 'BK Hacken'", settings=migrated)
    _mock_all(load_fixture)
    await fetch_context(
        APIFootballClient(http_client, migrated),
        {
            "id": event["id"],
            "home": event["home"],
            "away": event["away"],
            "commence_time": event["commence_time"],
            "apifootball_league_id": 113,
        },
        migrated,
    )

    rows = build_prompt(session_id, settings=migrated, now=NOW).body.splitlines()
    # La ligne **rendue**, reconnue a son indentation de deux espaces : le mot
    # apparait aussi dans les consignes du template, qui n'ont pas cette forme.
    absents = next(index for index, row in enumerate(rows) if row.startswith("  Absents"))

    assert rows[absents].startswith("  Absents     BK Hacken — ")
    assert rows[absents + 1] == "              Djurgardens IF — aucun signale"


@respx.mock
async def test_le_prompt_verrouille_les_cotes_du_bloc(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une cote substituee classerait la selection dans le mauvais palier."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Ne remplace jamais une cote du bloc" in body
    assert "au centime près" in body


@respx.mock
async def test_le_prompt_impose_une_hierarchie_de_sources(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Une blessure decide d'un pari : elle ne peut pas venir d'un agregateur."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Qualité des sources" in body
    assert "non confirmé" in body


@respx.mock
async def test_le_prompt_refuse_de_gonfler_un_combine(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "N'ajoute\njamais une jambe sous confiance 3" in body.replace("**", "")


# -- Ce que le prompt annonce du lot ----------------------------------------
#
# Sans ces annonces, l'analyste redemande des marches que le fournisseur ne sert
# pas, recalcule la taille du lot a chaque session et devine le fuseau des
# horaires. Trois devinettes evitables, toutes verifiables ici.


@respx.mock
async def test_le_prompt_annonce_les_marches_demandes(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Football : " in body
    assert "Score exact" in body, "un marche demande et servi est annonce"
    assert "BTTS" in body


@respx.mock
async def test_le_catalogue_suit_le_sport_du_lot(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Un lot de tennis n'annonce pas les marches du football."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    sport = db.query_one("SELECT id FROM sports WHERE key = 'tennis'", settings=migrated)
    competition = db.query_one(
        "SELECT id FROM competitions WHERE oddsapi_key = 'tennis_atp_us_open'", settings=migrated
    )
    db.execute(
        "INSERT INTO events (sport_id, competition_id, oddsapi_event_id, home, away, "
        "commence_time, source, created_at) VALUES (?, ?, 'evt', 'Fils', 'Rune', "
        "'2026-08-03T18:00:00Z', 'oddsapi', ?)",
        (sport["id"], competition["id"], db.utcnow()),
        settings=migrated,
    )
    event = db.query_one("SELECT MAX(id) AS id FROM events", settings=migrated)
    board_service.toggle_selection(int(event["id"]), True, migrated)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    ligne = next(row for row in body.splitlines() if row.strip().startswith("· Tennis"))
    assert "Hand. jeux" in ligne and "Set 1" in ligne
    assert "BTTS" not in ligne and "Score exact" not in ligne


@respx.mock
async def test_le_prompt_donne_la_taille_du_lot_et_son_plafond(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Ce lot comporte **1 match(s)**" in body
    assert "le total ne\npeut donc pas dépasser 1, tous paliers confondus." in body


@respx.mock
async def test_le_fuseau_des_horaires_est_dit(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Toutes les heures sont en Europe/Paris" in body


@respx.mock
async def test_le_multichoix_n_est_propose_que_si_le_marche_existe(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Sur un lot sans scores exacts, l'imposer fait ecrire « impossible » pour rien."""
    sans = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
    assert "multichoix scores exacts" not in build_prompt(sans, settings=migrated, now=NOW).body

    respx.get(f"{BASE_URL}/sports/soccer_sweden_allsvenskan/events/{EVENT_ID}/odds").mock(
        return_value=httpx.Response(
            200, json=load_fixture("oddsapi_event_odds_football.json"), headers=QUOTA_HEADERS
        )
    )
    await run_enrich(odds_client, sans, migrated, now=NOW)

    assert "multichoix scores exacts" in build_prompt(sans, settings=migrated, now=NOW).body


@respx.mock
async def test_l_arbitrage_des_paliers_est_ecrit(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les bandes se chevauchent : sans regle, le modele en invente une par session."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "la confiance tranche" in body


def test_chaque_porte_du_preambule_vise_un_libelle_qui_existe() -> None:
    """Le preambule ne documente que les lignes **presentes dans le lot**, par des
    conditions du genre `{% if 'Buteurs' in context_labels %}`.

    Une faute de frappe dans un de ces libelles ne casse rien : la condition est
    simplement toujours fausse, et le mode d'emploi disparait sans un mot — la
    donnee, elle, reste affichee et se lit de travers. Meme piege que les
    identifiants du sprite, meme garde-fou : `CONTEXT_ICONS` est le registre des
    libelles que le code sait produire.
    """
    import re

    from myassistantbet.services.labels import CONTEXT_ICONS
    from myassistantbet.services.prompt import TEMPLATES_DIR

    for chemin in TEMPLATES_DIR.glob("*.md.j2"):
        vises = set(re.findall(r"'([^']+)' in context_labels", chemin.read_text(encoding="utf-8")))
        inconnus = vises - set(CONTEXT_ICONS)
        assert not inconnus, f"{chemin.name} : libelles jamais produits {sorted(inconnus)}"
        assert vises, f"{chemin.name} : aucune porte, le preambule paie tout"


def test_aucun_libelle_de_contexte_ne_remplit_sa_colonne() -> None:
    """Le separateur entre un libelle et sa valeur n'existe pas en propre :
    c'est le remplissage du champ qui le fabrique. Un libelle qui occupe les
    douze caracteres ne laisse donc rien, et sort colle a sa valeur — constate
    en reel sur un prompt de six matchs, `Buts encais.Lillestrom >0.5 10/15`.

    Les cles de marche etaient deja tronquees a `LABEL_MAX` ; les libelles de
    contexte ne passaient par aucune troncature. C'est ce test qui tient la
    regle, `line()` ne faisant que degrader proprement."""
    from myassistantbet.services.labels import CONTEXT_ICONS
    from myassistantbet.services.render import LABEL_MAX, line

    trop_longs = {label for label in CONTEXT_ICONS if len(label) > LABEL_MAX}

    assert not trop_longs, f"libelles sans separateur possible : {sorted(trop_longs)}"
    for label in CONTEXT_ICONS:
        assert line(label, "valeur").endswith(" valeur"), label
