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
    QUOTA_FLOOR_TIERS,
    QUOTA_REFERENCE_LOT,
    TEMPLATES_DIR,
    Tier,
    build_prompt,
    date_fr,
    list_templates,
    load_tiers,
    save_prompt,
)
from myassistantbet.services.scan import active_competitions, run_scan
from myassistantbet.services.thresholds import THRESHOLDS
from myassistantbet.services.thresholds import save as save_threshold
from myassistantbet.services.thresholds import value_of as threshold_value

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

    **Le mot, lui, ne tombe plus avec le comptage**, et les deux traitements
    sont justes parce que les deux grandeurs n'ont pas la meme nature. Ils
    n'en faisaient qu'un tant que le mot n'existait que pour etre relu au
    moment du comptage ; depuis qu'il est une **colonne du tableau**, il decrit
    une selection prise seule — donc il vaut des la premiere — quand la
    proportion, elle, a toujours besoin de volume."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "Compte tes lignes avant de rendre" not in corps
    assert "nature en un mot" in corps, "le mot est une colonne : il vaut a tout volume"
    assert "| Type | Source |" in corps
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
    # Les bornes **de ce lot**, pas celles d'un lot de dix. Le prompt annoncait
    # « 2-4 🟢, 3-5 🔵… » sur un lot d'un match, puis expliquait en prose que
    # ces quotas « se reduisent a proportion » — une borne qu'il faut recalculer
    # soi-meme ne contraint rien.
    assert "Quotas **de ce lot** : 1-1 🟢, 1-1 🔵, 0-0 🟠, 0-0 🔴, 0-0 💥." in body
    assert "se réduisent" not in body, "le paragraphe explicatif n'a plus lieu d'etre"


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
    assert "  Forme 5     BK Hacken VVNDV (5j) 9-4/5" in body
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
    assert "le total ne peut pas dépasser 1, tous paliers confondus" in " ".join(body.split())


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
async def test_le_palier_se_lit_sur_la_cote_seule(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Les bandes **ne se chevauchent pas**, et l'arbitrage a ete retire.

    Le paragraphe faisait trancher la confiance « dans une zone commune a deux
    paliers » — sauf que les bandes ne se touchent qu'en un point exact (1.70,
    2.30, 3.60, 8.00) : sur cent selections, aucune cote n'y est jamais tombee.
    Deux cents mots de consigne pour un cas qui ne se produit pas.

    Et s'il s'etait produit, la regle aurait ete nuisible : ce palier sert a
    calculer un taux par **bande de cote**, et faire dependre le classement de
    la confiance aurait mis deux selections au meme prix dans deux paliers
    differents. Le prompt le disait lui-meme deux phrases plus bas — « une
    classification variable rendrait ce taux ininterpretable ».
    """
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "la confiance tranche" not in body
    assert "La cote décide seule" in body
    assert "la borne haute appartient au palier suivant" in body
    assert "la confiance a déjà son propre axe" in body


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


# -- Les crans de confiance, et les paliers qu'on n'atteint pas --------------


def test_les_cinq_crans_de_confiance_sont_definis(migrated: Settings) -> None:
    """Le prompt n'ancrait que 5, 3 et 1 : les crans 2 et 4 n'avaient aucune
    definition, et tout tombait donc en 3. Mesure sur cent selections — 99 %
    portaient un 3 ou un 4, et les crans 1 et 5 n'ont jamais servi. Une echelle
    dont deux crans sur cinq portent tout ne note plus rien."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    for ancre in (
        "≥ 2 facteurs indépendants",
        "1 facteur dominant vérifié en niveau 1-2",
        "mais un manque de la section A touche ce facteur",
        "fait principal issu d'une source de niveau 3-4",
        "aucun fait daté — lecture des blocs seuls",
    ):
        assert ancre in corps, f"le cran « {ancre} » doit être ancré"


def test_les_crans_se_lisent_avec_la_colonne_source(migrated: Settings) -> None:
    """Une seule echelle de sources dans tout le prompt : celle du preambule
    nourrit la colonne du tableau, qui nourrit le cran de confiance. Trois
    ecritures de la meme notion se seraient contredites."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "`lecture` va avec 1, une source de niveau 3-4 plafonne à 2" in corps
    assert "c'est le cran qu'il faut corriger" in corps


def test_les_deux_plafonds_en_doublon_ont_disparu(migrated: Settings) -> None:
    """La table des crans les porte desormais tous les deux ; les laisser a cote
    aurait donne deux regles pour un meme cas, sans dire laquelle gagne.

    Ce qui reste de la phrase sur les sources de niveau 4, c'est ce qu'elle
    disait en propre : ne jamais presenter une telle information comme un fait.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "ne dépasse pas 2" not in corps
    assert "plafonne la confiance de la sélection à 2" not in corps
    assert "rapporté par X, non confirmé" in corps, "le fond de la règle reste"


def test_un_palier_vide_se_commente(migrated: Settings) -> None:
    """ULTRA FUN est a 0/7, GIGA FUN et GIGA+ n'ont jamais servi en cent
    selections. On ne force pas leur remplissage — un quota rempli avec du vide
    est l'erreur que le prompt nomme lui-meme comme la plus couteuse — on rend
    la vacance sortante : un palier vide est un resultat, un palier vide non
    commente est un oubli."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "Un palier vide se commente, en une ligne sous le tableau" in corps
    assert "en nommant ce qu'il aurait fallu trouver" in corps
    assert "un palier vide non commenté est un oubli" in corps
    # Et surtout : rien qui invite a le remplir.
    assert "Ne remplis jamais un palier avec une sélection qui appartient" in corps


# -- L'ordre du prompt : ce qui decide avant ce qui explique -----------------


@respx.mock
async def test_le_mode_d_emploi_des_lignes_vient_apres_la_sortie_attendue(
    odds_client: OddsAPIClient,
    http_client: httpx.AsyncClient,
    migrated: Settings,
    load_fixture: Any,
) -> None:
    """Trois cents lignes de mode d'emploi se lisaient **avant** la methode.

    Le lecteur apprenait ce que veut dire « Buts marq. » et comment se pose un
    handicap jeux bien avant de savoir ce qu'il devait produire : les consignes
    qui decident de la sortie etaient noyees au milieu de celles qui expliquent.
    Deplacer ne change pas un token — c'est l'ordre qui etait faux.
    """
    from myassistantbet.providers.apifootball import APIFootballClient

    from .test_context import _mock_all

    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)
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

    assert "## COMMENT LIRE LES BLOCS" in body, "le mode d'emploi existe toujours"
    assert "Une ligne **« Classement »**" in body or "Classement" in body
    for section in ("## MÉTHODE", "## SORTIE ATTENDUE", "### F. Ce qui aurait changé"):
        assert body.index(section) < body.index("## COMMENT LIRE LES BLOCS"), (
            f"« {section} » doit précéder le mode d'emploi"
        )


@respx.mock
async def test_les_regles_qui_decident_restent_en_tete(
    odds_client: OddsAPIClient, migrated: Settings, load_fixture: Any
) -> None:
    """Le partage n'est pas « tout ce qui est long descend ».

    Ce qui **decide de ce qu'on rend** reste avant la methode : les interdits,
    la cote du bloc qui fait autorite, « A relever » qui rend un marche
    selectionnable, les lignes en quart qui ne le sont pas. Ce qui **explique
    une ligne** descend. Un marche qu'on croit interdit faute d'avoir lu jusqu'au
    bout est exactement l'erreur que ce prompt a deja payee une fois.
    """
    session_id = await _session_enrichie(odds_client, migrated, load_fixture, enrich=False)

    body = build_prompt(session_id, settings=migrated, now=NOW).body
    tete = body[: body.index("## MÉTHODE")]

    assert "INTERDIT, sans exception" in tete
    assert "Ces cotes font autorité" in tete
    assert "Une ligne **« A relever »**" in tete
    assert "les lignes en quart ne se posent pas" in tete


def test_le_renvoi_se_garde_comme_le_chapitre_qu_il_annonce(migrated: Settings) -> None:
    """La porte du chapitre est celle de son contenu, `sports`, et non
    `context_labels` : ses paragraphes de football et de tennis se gardent sur le
    sport du lot, pas sur les lignes recuperees. Un lot de football sans contexte
    a bien un mode d'emploi a lire — celui de ses marches.

    Un renvoi garde autrement que le chapitre qu'il annonce est le pire des deux
    mondes : il promet une section absente, ou il tait une section presente.
    """
    session_id = board_service.current_session(migrated)
    vide = build_prompt(session_id, settings=migrated, now=NOW).body
    football = build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body

    assert "COMMENT LIRE LES BLOCS" not in vide, "aucun sport, aucun mode d'emploi"
    assert "Le mode d'emploi des lignes de contexte" not in vide
    assert "COMMENT LIRE LES BLOCS" in football
    assert "Le mode d'emploi des lignes de contexte" in football


# -- Trois contradictions internes du gabarit -------------------------------


def test_le_cran_5_est_atteignable(migrated: Settings) -> None:
    """La section A demande de nommer **tout** ce qu'on n'a pas trouve : une
    colonne « Ce qui manque » vide n'existe pratiquement jamais.

    Exiger le vide au cran 5 le rendait structurellement inaccessible — le
    defaut meme que la table des crans devait corriger. Ce qui compte est que le
    trou soit **sans rapport** avec ce qui porte la selection, comme au cran 4.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "« Ce qui manque » vide sur ce match" not in corps
    assert "aucun manque de la section A ne touche ces facteurs" in corps
    assert "Exiger le vide rendrait le cran 5 inatteignable" in corps


def test_la_clause_de_silence_ne_couvre_que_les_taux(migrated: Settings) -> None:
    """« N'en tire aucune tendance, et n'ecris rien a ce sujet » fermait le
    chapitre et annulait, seize lignes plus haut, la demande de commenter un lot
    dont le taux de selection sort de l'ordinaire.
    """
    # Le gabarit est relu a plat : ses phrases sont coupees en lignes de 80.
    gabarit = " ".join((TEMPLATES_DIR / DEFAULT_TEMPLATE).read_text(encoding="utf-8").split())

    assert "N'en tire aucune tendance, et n'écris rien à ce sujet." not in gabarit
    assert "n'écris rien **sur ces taux**" in gabarit
    assert "Le constat sur le taux de sélection, en tête de ce chapitre, n'est pas" in gabarit


def test_la_clause_de_perimetre_existe_aussi_quand_le_recul_suffit(migrated: Settings) -> None:
    """Sans elle, le probleme reapparaitrait le jour ou le seuil est franchi —
    la liste « ce qu'il ne fait jamais » se lirait alors comme couvrant tout."""
    gabarit = (TEMPLATES_DIR / DEFAULT_TEMPLATE).read_text(encoding="utf-8")
    enough = " ".join(
        gabarit.split("{% if feedback.enough %}", 1)[1].split("{% else %}", 1)[0].split()
    )

    assert "rien de ce qui précède ne vise le taux de sélection" in enough


def test_un_petit_lot_ne_demande_qu_un_combine(migrated: Settings) -> None:
    """Sur cinq matchs et un taux de selection median de 36 %, la sortie
    attendue tourne autour de deux selections : reclamer un combine de 3-4
    jambes **et** un second de 4-5, une seule selection par match, etait
    insatisfiable avant meme que l'analyse commence."""
    seuil = threshold_value("combo_min_lot", migrated)

    corps = " ".join(
        build_prompt(_lot_de(migrated, seuil - 1), settings=migrated, now=NOW).body.split()
    )

    assert "**Un seul combiné**" in corps
    assert "combiné « frisson »" not in corps
    assert "réellement différents" not in corps, "le paragraphe suppose deux combinés"
    assert "n'ajoute aucune jambe pour faire le compte" in corps


def test_un_lot_assez_grand_garde_les_deux_combines(migrated: Settings) -> None:
    seuil = threshold_value("combo_min_lot", migrated)

    corps = " ".join(
        build_prompt(_lot_de(migrated, seuil), settings=migrated, now=NOW).body.split()
    )

    assert "combiné « solide »" in corps and "combiné « frisson »" in corps
    assert "**Un seul combiné**" not in corps
    assert "réellement différents" in corps


def test_le_seuil_des_combines_se_lit_dans_les_reglages(migrated: Settings) -> None:
    """« A partir de combien de matchs un lot porte-t-il deux combines » est une
    decision de l'utilisateur, pas une constante du projet."""
    save_threshold("combo_min_lot", "3", migrated)

    corps = " ".join(build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body.split())

    assert "combiné « frisson »" in corps, "quatre matchs passent le seuil abaissé"


def test_un_seuil_hors_bornes_revient_au_defaut(migrated: Settings) -> None:
    """Un seuil mal saisi doit degrader vers un comportement connu, pas casser
    une generation de prompt."""
    save_threshold("combo_min_lot", "beaucoup", migrated)
    assert threshold_value("combo_min_lot", migrated) == THRESHOLDS["combo_min_lot"].default

    save_threshold("combo_min_lot", "9999", migrated)
    assert threshold_value("combo_min_lot", migrated) == THRESHOLDS["combo_min_lot"].default


def test_les_blocs_ne_sont_jamais_une_source(migrated: Settings) -> None:
    """L'echelle definit le niveau 3 comme « statistiques de reference, feuilles
    de match, ordre du jeu ». Or les blocs CONTEXTE **sont** des statistiques de
    fournisseur : une selection qui ne repose que sur eux pouvait se declarer
    `3` — plafond confiance 2 — ou `lecture` — plafond confiance 1, au hasard.

    C'est precisement la comparaison que les colonnes Type et Source existent
    pour mesurer. Sans arbitrage, elles ne mesurent rien.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "**Les blocs de ce prompt ne sont jamais une source**" in corps
    assert "est une `lecture`, quelle que soit la qualité du fournisseur" in corps
    assert "une statistique que **ta recherche** a rapportée" in corps
    assert "ces deux colonnes ne mesureraient plus rien" in corps


# -- Les quotas se calculent, ils ne s'expliquent plus -----------------------


def test_les_quotas_se_reduisent_a_proportion_du_lot(migrated: Settings) -> None:
    """Le prompt affichait les bornes d'un lot de dix sur un lot de cinq, puis
    expliquait en prose qu'elles « se réduisent à proportion ». Une borne qu'il
    faut recalculer soi-meme ne contraint rien."""
    tiers = load_tiers(migrated)
    safe, giga_plus = tiers[0], tiers[-1]

    assert safe.quota_for(QUOTA_REFERENCE_LOT, safest=True) == (2, 4), "le lot de reference"
    assert safe.quota_for(5, safest=True) == (2, 2), "moitie moins de matchs, moitie moins"
    assert giga_plus.quota_for(5, safest=False) == (0, 1)


def test_les_deux_paliers_les_plus_surs_gardent_un_plancher(migrated: Settings) -> None:
    """Un petit lot doit pouvoir porter une selection sure : sans plancher, la
    reduction interdirait de rendre quoi que ce soit."""
    tiers = load_tiers(migrated)

    assert [
        tier.quota_for(1, safest=rank < QUOTA_FLOOR_TIERS)[1] for rank, tier in enumerate(tiers)
    ] == [
        1,
        1,
        0,
        0,
        0,
    ]


def test_une_borne_basse_ne_depasse_jamais_la_haute(migrated: Settings) -> None:
    """« 2-1 » ne se lit pas. La borne reglee peut depasser la borne reduite."""
    safe = load_tiers(migrated)[0]

    low, high = safe.quota_for(2, safest=True)

    assert low <= high


def test_un_lot_vide_ne_reclame_aucun_palier(migrated: Settings) -> None:
    assert load_tiers(migrated)[0].quota_for(0, safest=True) == (0, 0)


def test_l_arrondi_des_quotas_ne_depend_pas_du_palier(migrated: Settings) -> None:
    """`round()` de Python arrondit les moities vers le pair : 2.5 rendrait 2 et
    1.5 rendrait 2, soit deux comportements sur deux paliers voisins. L'arrondi
    est donc au plus proche, moities vers le haut."""
    tiers = load_tiers(migrated)
    cinq = Tier(**{**tiers[0].__dict__, "quota_min": 0, "quota_max": 5})
    trois = Tier(**{**tiers[0].__dict__, "quota_min": 0, "quota_max": 3})

    assert cinq.quota_for(5, safest=False)[1] == 3, "2.5 monte a 3"
    assert trois.quota_for(5, safest=False)[1] == 2, "1.5 monte a 2"


# -- Ne proposer que les paliers atteignables --------------------------------


def _lot_aux_cotes(migrated: Settings, *blocs: str) -> int:
    """Un lot de football dont chaque bloc porte les cotes demandees.

    `_lot_de` fige les siennes ; ici c'est precisement la cote qui est le sujet.
    """
    session_id = 0
    for index, cotes in enumerate(blocs):
        event_id = save(
            build(
                "football",
                "Match amical",
                f"Lyon {index}",
                f"Nice {index}",
                "2026-08-04",
                "20:45",
                cotes,
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)
    return session_id


def test_la_borne_haute_appartient_au_palier_suivant(migrated: Settings) -> None:
    """Une cote a 1.70 est FUN, pas SAFE. La convention du prompt, en code."""
    safe, fun = load_tiers(migrated)[:2]

    assert safe.covers(1.69) and not safe.covers(1.70)
    assert fun.covers(1.70)


def test_seuls_les_paliers_atteignables_sont_injectes(migrated: Settings) -> None:
    """Le prompt injectait `0-1 🔴, 0-0 💥` sur un lot dont la cote la plus haute
    valait 3.40, puis exigeait qu'un palier vide soit commente « en nommant ce
    qu'il aurait fallu trouver ». L'analyse produisait une ligne d'excuse pour un
    palier que le lot rendait impossible avant meme qu'elle commence."""
    session_id = _lot_aux_cotes(migrated, "Lyon 0 1.30\nNice 0 1.65")

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Quotas **de ce lot** : 1-1 🟢." in corps
    for emoji in ("🔵", "🟠", "🔴", "💥"):
        assert emoji not in corps.split("Quotas")[1], f"{emoji} n'est pas dans le lot"
    assert "Absents du lot : FUN, ULTRA FUN, GIGA FUN, GIGA+" in corps


def test_une_cote_sur_la_borne_ouvre_le_palier(migrated: Settings) -> None:
    """La borne basse, elle, appartient bien au palier : 5.00 est un GIGA FUN,
    et le lot qui la porte doit le proposer."""
    session_id = _lot_aux_cotes(migrated, "Lyon 0 1.50\nNice 0 5.00")

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "🔴 GIGA FUN" in corps
    assert "Paliers présents dans ce lot : SAFE, GIGA FUN." in corps


def test_l_atteignabilite_se_mesure_sur_les_cotes_et_non_sur_l_intervalle(
    migrated: Settings,
) -> None:
    """Un lot a 1.50 et 3.00 ne porte **aucune** cote entre 1.70 et 2.60.

    Declarer FUN atteignable parce qu'il tombe « entre les deux » ferait chercher
    un prix qui n'existe nulle part : une selection recopie une cote d'un bloc,
    jamais un intervalle.
    """
    session_id = _lot_aux_cotes(migrated, "Lyon 0 1.50\nNice 0 3.00")

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Paliers présents dans ce lot : SAFE, ULTRA FUN." in corps
    assert "Absents du lot : FUN, GIGA FUN, GIGA+" in corps


def test_les_bornes_du_lot_sont_nommees_avec_leur_emplacement(migrated: Settings) -> None:
    """Une borne annoncee sans l'endroit ou elle se lit oblige a relire tous les
    blocs pour la verifier, et personne ne le fait."""
    session_id = _lot_aux_cotes(migrated, "Lyon 0 1.50\nNice 0 3.00")

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Cote max du lot : 3.00 (M1 · Cotes Nice 0)." in corps
    assert "Cote min : 1.50 (M1 · Cotes Lyon 0)." in corps


def test_chaque_bloc_dit_les_paliers_que_ses_cotes_atteignent(migrated: Settings) -> None:
    """Mesure qui l'a fait naitre : sur un lot de quatre quarts de finale, un
    bloc n'offrait aucune cote sous 1.70 — aucune selection SAFE n'en sortirait,
    quel que soit l'angle, et rien ne le disait."""
    session_id = _lot_aux_cotes(
        migrated, "Lyon 0 1.71\nNice 0 2.23", "Lyon 1 1.40\nNul 2.00\nNice 1 3.00"
    )

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Paliers     FUN (cotes du bloc 1.71-2.23 — aucun SAFE ni ULTRA FUN)" in corps
    assert "Paliers     SAFE, FUN, ULTRA FUN\n" in corps, (
        "un bloc qui n'exclut rien de plus que le lot n'explique rien"
    )


def test_un_palier_hors_du_lot_ne_se_commente_pas(migrated: Settings) -> None:
    """La consigne « un palier vide se commente » ne porte plus que sur les
    paliers reellement proposes : ailleurs, elle reclamait une excuse."""
    corps = " ".join(build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body.split())

    assert "seulement parmi ceux listés ci-dessus" in corps
    assert "il n'a jamais été proposé" in corps


def test_le_glossaire_explique_les_deux_fenetres_de_forme_5(migrated: Settings) -> None:
    """`ND (2j) 10-6/5` n'est pas seize buts en deux matchs : les lettres
    viennent de la seule competition, les buts des cinq derniers toutes
    competitions. La ligne le montre, le glossaire doit le dire."""
    gabarit = " ".join((TEMPLATES_DIR / DEFAULT_TEMPLATE).read_text(encoding="utf-8").split())

    assert "chaque moitié porte son propre dénominateur" in gabarit
    assert "n'est pas seize buts en deux matchs" in gabarit
    assert "(après Nj — indicatif) »**, elle sort d'un classement de début de saison" in gabarit
