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
#: **La marge etait de ~2000 tokens, et cette valeur-la n'a pas tenu.** Elle a
#: fini par arbitrer de nouveau : les deux alarmes etaient a leur limite — 11606
#: pour 11500 ici, 10009 pour 10000 sur le lot mixte — et une convention de
#: marche de cinq lignes les a franchies toutes les deux. Une alarme qui se
#: declenche sur un ajout delibere de 106 tokens ne mesure plus une explosion,
#: elle rationne.
#:
#: **Il faut donc dire ce qu'une alarme de tokens peut encore attraper, et ce
#: qu'elle ne peut plus.** Un preambule rendu en entier sur un lot d'un seul
#: sport coute de 1429 a 2098 tokens (mesure au dossier du projet : 6555 pour
#: les deux sports, 5126 pour le football seul, 4457 pour le tennis seul), et un
#: bloc duplique environ 750. Ces montants sont **du meme ordre que la
#: croissance deliberee qu'on vient d'autoriser** : aucun seuil ne peut separer
#: les deux. Une porte de preambule cassee se garde donc par les tests qui
#: portent sur la porte elle-meme — ceux du garde-fou de sport et de
#: `context_labels` — et non par un compte de tokens.
#:
#: Ce qui reste a l'alarme est le **derapage structurel** : un lot rendu deux
#: fois, une boucle qui repete un bloc, un preambule injecte par match au lieu
#: de l'etre par lot. Elle vaut donc **le double de la mesure**, ce qui nomme
#: exactement cette classe d'accident au lieu d'un nombre choisi pour tenir.
#:
#: Ce qui n'a pas change : la densite reste un objectif de qualite — une ligne
#: sans donnee est omise, un mode d'emploi se garde sur son libelle. Le plafond
#: ne remplace pas ces regles, il ne les faisait deja pas respecter.
PROMPT_BUDGET = 23000


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


def test_le_comptage_des_vainqueurs_ne_se_demande_plus_a_l_analyse(
    migrated: Settings,
) -> None:
    """Le prompt faisait s'auto-auditer l'analyse : « compte tes lignes avant de
    rendre, si plus de la moitie du tableau porte sur le vainqueur, relis-les
    avec leur colonne Type ». Or les deux colonnes sont **en base** — l'angle
    depuis la migration 026, la famille du marche depuis la 027 — et le conflit
    se detecte en une requete.

    Une regle deterministe laissee au modele coute des tokens, se refait a chaque
    session et ne se mesure jamais. Ce qui reste dans le prompt, c'est la
    **consigne de fond** : le mot qui choisit le marche, et le rappel qu'un
    angle sur une maniere se traduit mieux ailleurs.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body.split())

    assert "Compte tes lignes avant de rendre" not in corps
    assert "plus de la moitié du tableau porte sur le marché du vainqueur" not in corps
    assert "sa **nature en un mot — « issue » ou « manière »**" in corps
    assert "le plus grossier des débouchés d'une analyse" in corps
    assert "reprend le mot de la section B" in corps


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
    """Le lot est etendu au seuil du combine : depuis que le prompt n'en demande
    plus du tout sous `combo_solo_min_lot`, la consigne de jambe tombe avec la
    demande — et c'est exactement ce qu'on veut, pas ce qu'on teste ici."""
    session_id = await _session_enrichie(odds_client, migrated, load_fixture)
    for index in range(threshold_value("combo_solo_min_lot", migrated)):
        event_id = save(
            build(
                "football",
                "Match amical",
                f"Lyon {index}",
                f"Nice {index}",
                "2026-08-04",
                "20:45",
                f"Lyon {index} 2.10\nNice {index} 3.20",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        board_service.toggle_selection(event_id, True, migrated)

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


# -- L'echelle des sources classe par editeur --------------------------------


def test_l_echelle_des_sources_classe_par_editeur(migrated: Settings) -> None:
    """L'ancienne echelle plaçait « ATP/WTA, site du tournoi » en niveau 1 et
    « feuilles de match » en niveau 3. La recherche que ce prompt designe
    lui-meme comme la plus rentable du lot — les statistiques de service
    derriere l'onglet Stats d'atptour.com — est **les deux a la fois** : selon
    la lecture retenue elle valait 1, donc confiance 4-5 accessible, ou 3, donc
    plafonnee a confiance 2. C'est exactement le « repondre au petit bonheur »
    que ce prompt denonce par ailleurs.

    Le critere est donc l'**editeur**, jamais la nature du contenu.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "**L'échelle classe par éditeur, jamais par nature du contenu**" in corps
    assert "L'organisateur ou l'instance" in corps
    assert "un ordre du jeu publiés là sont un **niveau 1**" in corps
    assert "Statistique **tierce**" in corps
    assert "feuilles de match, ordre du jeu" not in corps, "le niveau 3 ne les revendique plus"


def test_le_niveau_1_prime_sur_la_nature_de_la_statistique(migrated: Settings) -> None:
    """La colonne Source de la section C portait la contradiction : elle
    definissait le niveau 3 comme « une feuille de match, un ordre du jeu, un
    releve officiel de la competition », soit trois choses que l'echelle range
    desormais en niveau 1. Corriger l'echelle sans corriger sa consigne aurait
    laisse la meme ambiguite deux sections plus bas."""
    corps = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "rapportée d'un **éditeur tiers**" in corps
    assert "est un `1`, même quand c'est une statistique" in corps


def test_l_exemple_de_l_echelle_suit_le_sport_du_lot(migrated: Settings) -> None:
    """Un lot de football ne paie pas l'exemple d'atptour.com, et n'ecope pas
    d'exemples de sources tennis dans son niveau 3. Meme regle que partout : ce
    qui n'a pas de donnee est omis."""
    football = build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body

    assert "atptour.com" not in football
    assert "Tennis Abstract" not in football
    assert "la feuille de match publiée par le club est un **niveau 1**" in " ".join(
        football.split()
    )


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


def test_le_chapitre_de_lecture_a_maigri_sans_perdre_ses_conventions(migrated: Settings) -> None:
    """Apres les lots 1, 3 et 4, plusieurs passages decrivaient des calculs que
    l'application fait desormais. Le chapitre a ete resserre — mesure sur un lot
    de six matchs de football, 2517 tokens contre 1834 — mais **aucune
    convention de lecture n'a bouge**, a commencer par le signe du handicap
    jeux, qui suit la position dans le titre et jamais le favori.

    Le garde-fou est ecrit ici parce que la perte serait silencieuse : une
    convention retiree ne casse rien, elle fait lire une cote a l'envers.
    """
    gabarit = " ".join((TEMPLATES_DIR / DEFAULT_TEMPLATE).read_text(encoding="utf-8").split())

    assert "Le signe ne suit jamais le favori : il suit la position dans le titre." in gabarit
    assert "Over d'abord, Under ensuite" in gabarit
    assert "toujours compté depuis la fin" in gabarit
    assert "le seul pourcentage du bloc" in gabarit
    assert "les buts **du match, les deux équipes réunies**" in gabarit
    assert "**journées de tournoi** et non en dates civiles" in gabarit


# -- Lot 8 : corrections courtes ---------------------------------------------


def test_la_meme_cause_ne_se_declenche_plus_sur_le_tournoi_partage(migrated: Settings) -> None:
    """« Meme tournoi, memes conditions, meme type de scenario » se declenche sur
    presque toutes les paires d'un lot de quatre quarts de finale du meme
    tournoi, joues la meme soiree : le critere ne discrimine plus rien, et la
    ligne qu'il reclame devient une formalite.

    La meme cause, c'est desormais le meme protagoniste, ou un facteur nomme
    comme moteur des deux angles.

    Le lot fait six matchs : la section D ne parle de combines — donc de causes
    partagees entre jambes — qu'au-dessus de `combo_solo_min_lot`."""
    corps = " ".join(build_prompt(_lot_de(migrated, 6), settings=migrated, now=NOW).body.split())

    assert "un facteur nommément désigné comme moteur des deux angles" in corps
    assert "Partager le tournoi, la surface ou la soirée **ne suffit pas**" in corps
    assert "même tournoi, mêmes conditions, même type de scénario" not in corps
    assert "au sens strict défini en section C" in corps, "la section D suit la meme regle"


def test_les_faits_declencheurs_dependent_du_sport(migrated: Settings) -> None:
    """« Une absence, un retour de blessure, une surface qui ne convient pas, un
    enjeu asymetrique, une charge anormale » est taillee pour le football. Au
    tennis une absence signifie qu'il n'y a **pas de match**, et l'enjeu
    asymetrique n'existe pas en quart d'un Masters 1000 : la liste ne pouvait
    donc rien declencher sur la moitie des lots.

    Le football garde la sienne mot pour mot — une regle rendue dependante du
    sport ne doit pas le faire regresser."""
    foot = " ".join(build_prompt(_lot_de(migrated, 3), settings=migrated, now=NOW).body.split())

    assert "Au football : une absence, un retour de blessure, une surface qui ne" in foot
    assert "un enjeu asymétrique, une charge anormale" in foot
    assert "double engagé sur place" not in foot, "un lot de football ne paie pas la liste tennis"


def test_les_rappels_de_cote_de_reference_sont_generes(migrated: Settings) -> None:
    """La section F est plafonnee a **trois lignes** et doit porter les marches
    manquants. Avec deux ou trois selections assises sur une cote de reference,
    elle etait pleine avant d'avoir rien dit d'utile — sur le lot du 10/08, les
    quatre blocs portaient « A relever : Hand. jeux, Jeux O/U ».

    L'application sait quels marches sont en reference : elle les ecrit, et F
    redevient ce qu'elle doit etre.
    """
    event_id = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "20:45",
            "Moutet 1.85\nBergs 1.95",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)
    db.execute(
        "UPDATE odds SET bookmaker = 'pinnacle' WHERE event_id = ?", (event_id,), settings=migrated
    )

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "**Prix à relever avant de miser.**" in corps
    assert "M1 Moutet – Bergs — tout le bloc [Pinnacle (ref.)]" in corps
    # Et la section F cesse de les reclamer : elle est reservee aux echecs de
    # recherche, pas a une liste que l'application connait deja.
    assert "Signale de même toute sélection assise sur une cote de référence" not in corps
    assert "elles sont déjà listées sous le tableau C" in corps


def test_aucun_rappel_sans_cote_de_reference(migrated: Settings) -> None:
    """Une ligne sans donnee est omise, jamais rendue vide : un lot entierement
    servi par le book principal ne paie pas ce paragraphe."""
    corps = build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body

    assert "Prix à relever avant de miser" not in corps


def test_un_lot_trop_court_ne_demande_aucun_combine(migrated: Settings) -> None:
    """Le seuil des deux combines avait son symetrique manquant. Sur un lot de
    4 matchs et un taux de selection median de 36 %, l'esperance tourne autour
    de 1.4 selection quand la section D en reclame trois independantes :
    reclamer, puis faire ecrire que c'etait impossible, coute deux fois."""
    corps = build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body

    assert "**Aucun combiné sur ce lot.**" in corps
    assert "la question n'est pas posée" in corps
    assert "Un seul combiné" not in corps
    assert "N'ajoute\njamais une jambe" not in corps, "la consigne de jambe tombe avec la demande"
    assert "maillon le plus fragile" not in corps


def test_le_seuil_d_un_combine_se_regle(migrated: Settings) -> None:
    """Un seuil est une decision de l'utilisateur : le coder en dur obligerait
    a redeployer pour changer d'avis."""
    assert threshold_value("combo_solo_min_lot", migrated) == 5

    save_threshold("combo_solo_min_lot", "3", migrated)
    corps = build_prompt(_lot_de(migrated, 4), settings=migrated, now=NOW).body

    assert "**Un seul combiné**" in corps
    assert "Aucun combiné sur ce lot" not in corps


def test_le_score_exact_en_sets_survit_a_l_absence_de_combine(migrated: Settings) -> None:
    """La section D porte deux demandes distinctes : les combines et, au tennis,
    le score en sets. Retirer la premiere ne doit pas emporter la seconde — elle
    ne depend d'aucun combine, et le prompt interdit meme de l'y mettre."""
    save_threshold("combo_solo_min_lot", "20", migrated)
    event_id = save(
        build(
            "tennis",
            "ATP 250 Gstaad",
            "Moutet",
            "Bergs",
            "2026-08-04",
            "20:45",
            "Moutet 1.85\nBergs 1.95",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board_service.toggle_selection(event_id, True, migrated)

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Aucun combiné sur ce lot" in corps
    assert "**Score exact en sets**" in corps
    assert "ne le mets dans aucun combiné" in corps


# -- Coherence des combines aux seuils releves -------------------------------
#
# Les seuils passent a 9 (un combine) et 20 (deux combines). Sur des lots tennis
# de 4 a 8 matchs, la consequence assumee est qu'**aucun combine n'est plus
# demande** : la branche doit donc s'ecrire sans laisser de section vide, de
# titre orphelin ni de double saut de ligne.
#
# Les valeurs sont posees **par le test** et jamais en base : ce sont des
# saisies de l'utilisateur, et les figer en dur ferait passer le test sur une
# configuration que personne n'a choisie.

SEUIL_UN_COMBINE = 9
SEUIL_DEUX_COMBINES = 20


@pytest.mark.parametrize(
    ("taille", "attendu"),
    [
        (4, "aucun"),
        (8, "aucun"),
        (9, "un"),
        (14, "un"),
        (15, "un"),
        (19, "un"),
        (20, "deux"),
    ],
)
def test_la_section_des_combines_se_rend_proprement(
    migrated: Settings, taille: int, attendu: str
) -> None:
    """Aux bornes et de part et d'autre. Le rendu doit rester propre a chaque
    taille : c'est la seule section du prompt dont le contenu disparait
    entierement, et une section vide se remarque moins qu'un paragraphe faux."""
    save_threshold("combo_solo_min_lot", str(SEUIL_UN_COMBINE), migrated)
    save_threshold("combo_min_lot", str(SEUIL_DEUX_COMBINES), migrated)

    corps = build_prompt(_lot_de(migrated, taille), settings=migrated, now=NOW).body
    section = corps.split("### D. Combinés")[1].split("### E.")[0]
    plat = " ".join(section.split())

    assert plat, "jamais un titre orphelin : la section dit toujours quelque chose"
    assert "\n\n\n" not in section, "aucun double saut de ligne"
    assert section.strip(), "aucune section vide"

    if attendu == "aucun":
        assert "**Aucun combiné sur ce lot.**" in plat
        assert "la question n'est pas posée" in plat
        # Les trois paragraphes qui n'ont plus d'objet tombent avec la demande.
        assert "une seule sélection par match dans un combiné" not in plat
        assert "cotes cibles sont indicatives" not in plat
        assert "maillon le plus fragile" not in plat
    elif attendu == "un":
        assert "**Un seul combiné**" in plat
        assert "Contrainte : une seule sélection par match dans un combiné." in plat
        assert "Les cotes cibles sont indicatives" in plat
        assert "maillon le plus fragile" in plat
        assert "Les deux combinés doivent être" not in plat
    else:
        assert "combiné « solide »" in plat and "combiné « frisson »" in plat
        assert "Les deux combinés doivent être **réellement différents**" in plat


def test_le_score_en_sets_survit_a_toutes_les_tailles(migrated: Settings) -> None:
    """La section D porte deux demandes distinctes : les combines et, au tennis,
    le score en sets. Retirer la premiere ne doit jamais emporter la seconde."""
    save_threshold("combo_solo_min_lot", str(SEUIL_UN_COMBINE), migrated)
    save_threshold("combo_min_lot", str(SEUIL_DEUX_COMBINES), migrated)
    session_id = 0
    for index in range(4):
        event_id = save(
            build(
                "tennis",
                "ATP 250 Gstaad",
                f"Moutet {index}",
                f"Bergs {index}",
                "2026-08-04",
                "20:45",
                f"Moutet {index} 1.85\nBergs {index} 1.95",
                "",
                "",
                settings=migrated,
            ),
            migrated,
        )
        session_id = board_service.toggle_selection(event_id, True, migrated)

    corps = build_prompt(session_id, settings=migrated, now=NOW).body

    assert "Aucun combiné sur ce lot" in corps
    assert "**Score exact en sets**" in corps


# -- Le signe du handicap ----------------------------------------------------


def _cotes(settings: Settings, event_id: int, lignes: list[tuple[str, str, float | None, float]]):
    for marche, nom, point, prix in lignes:
        db.execute(
            "INSERT INTO odds (event_id, bookmaker, market_key, outcome_name, point, price, "
            "fetched_at) VALUES (?, 'superbet', ?, ?, ?, ?, ?)",
            (event_id, marche, nom, point, prix, db.utcnow()),
            settings=settings,
        )


def _lot_avec_handicap(settings: Settings, meme_signe: bool) -> int:
    """Un lot d'un match, son 1N2 et un palier de handicap. `meme_signe` rejoue
    le defaut : les deux moities du palier portent le meme nombre."""
    session_id = _lot_de(settings, 1)
    event = db.query_one("SELECT id, home, away FROM events", settings=settings)
    event_id, home, away = int(event["id"]), event["home"], event["away"]
    _cotes(
        settings,
        event_id,
        [
            ("h2h", home, None, 1.73),
            ("h2h", "Draw", None, 3.90),
            ("h2h", away, None, 4.60),
            ("spreads", home, -0.5, 1.70),
            ("spreads", away, -0.5 if meme_signe else 0.5, 2.12),
        ],
    )
    return session_id


def test_le_mode_d_emploi_de_l_alerte_ne_se_paie_que_sur_un_lot_qui_en_porte(
    migrated: Settings,
) -> None:
    """Meme regle que les libelles de contexte, un cran plus loin : cette ligne
    est faite pour ne **jamais** servir, et son explication ne doit pas peser sur
    toutes les sessions ou tout va bien."""
    sain = build_prompt(_lot_avec_handicap(migrated, meme_signe=False), settings=migrated, now=NOW)

    assert "Une ligne « Alerte » suspend cette autorité" not in sain.body
    assert "Alerte" not in sain.body


def test_un_handicap_incoherent_est_annonce_et_explique(migrated: Settings) -> None:
    """Le prompt affirme que les cotes du bloc font autorite. Une ligne dont le
    signe contredit le 1N2 du meme bloc est la seule exception, et il faut donc
    qu'elle soit dite **et** que sa consequence le soit : ne pas selectionner ce
    handicap."""
    corps = build_prompt(
        _lot_avec_handicap(migrated, meme_signe=True), settings=migrated, now=NOW
    ).body

    assert "Une ligne « Alerte » suspend cette autorité" in corps
    assert "ne sélectionne pas ce handicap" in corps
    assert "coté comme le pari inverse" in corps


def test_la_convention_du_handicap_football_est_documentee(migrated: Settings) -> None:
    """Un signe lu a l'envers est l'erreur la plus couteuse que ce bloc puisse
    produire, et la convention du tennis etait ecrite quand celle du football ne
    l'etait pas."""
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "**« Handicap »** porte **une seule ligne, ses deux moitiés**" in corps
    assert "toujours opposés" in corps


def test_la_convention_over_under_est_documentee(migrated: Settings) -> None:
    """L'ordre des deux prix d'un total n'etait ecrit **nulle part** au football,
    quand le handicap et le total de jeux du tennis avaient chacun le leur.

    Il a donc fallu le deduire par recoupement, sur un `0.5: 1.05/9.50` ou seule
    l'invraisemblance du second prix disait lequel etait le Over. Une inversion
    n'aurait rien casse : elle enregistre la selection au mauvais prix **et**
    dans le mauvais palier, donc elle corrompt a la fois le suivi par bande de
    cote et le taux de reussite — la classe d'erreur exacte que la convention du
    handicap previent deja.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "**le Over d'abord, le Under ensuite**" in corps
    assert "`2.5: 1.70/2.10`" in corps
    # Les trois marches qui passent par le meme rendu, et le seul qui n'y passe
    # pas : `Eq. buts` nomme son cote et ne porte qu'un prix.
    assert "**« MT O/U »**, **« Corners »** et **« Cartons »**" in corps
    assert "ne porte qu'un seul côté" in corps


def test_le_preambule_dit_ce_que_l_ecart_au_coup_d_envoi_traverse(migrated: Settings) -> None:
    """Une journee entiere a ete analysee sur des cotes relevees huit heures
    avant le coup d'envoi sans que rien ne le signale. L'en-tete le dit
    desormais, et le preambule dit quoi en faire — signaler, pas renoncer."""
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "ce que cet écart traverse" in corps
    assert "avant les compositions" in corps
    assert "il te dit que ta recherche peut légitimement décrire un marché" in corps


# -- Lot 13 : le programme qui glisse ----------------------------------------


def test_l_alerte_meteo_et_le_report_ne_se_disent_qu_ensemble(migrated: Settings) -> None:
    """**Les deux informations existaient separement, et personne ne les
    rapprochait.** Le soir des orages de Cincinnati, l'alerte du NWS etait en
    base et les horaires avaient bouge de cinq heures : chacune avait sa ligne,
    et leur conjonction — qui dit que le programme peut ceder encore — n'etait
    ecrite nulle part.

    Rien ne s'ecrit si une seule des deux tient : chacune a deja sa ligne."""
    from datetime import UTC, datetime

    from myassistantbet.services.prompt import schedule_notice
    from myassistantbet.services.render import RenderableEvent

    def _event(alerte: bool, deplace: bool) -> RenderableEvent:
        return RenderableEvent(
            index=1,
            sport_key="tennis",
            competition="ATP Cincinnati Open",
            home="A",
            away="B",
            commence_local=datetime(2026, 8, 12, 23, 0, tzinfo=UTC),
            context_lines=[("Meteo", "ALERTE Flood Watch — NWS Wilmington OH")] if alerte else [],
            previous_local=datetime(2026, 8, 12, 17, 55, tzinfo=UTC) if deplace else None,
        )

    assert schedule_notice([_event(alerte=True, deplace=False)]) == ""
    assert schedule_notice([_event(alerte=False, deplace=True)]) == ""
    ensemble = schedule_notice([_event(alerte=True, deplace=True)])
    assert "alerte meteo en vigueur" in ensemble
    assert "jusqu'a +5h05" in ensemble
    assert "peut glisser encore" in ensemble


def test_le_lot_annonce_le_programme_qui_glisse(
    migrated: Settings,
) -> None:
    """Le croisement arrive **en tete de la section MATCHS**, avant les blocs :
    il decrit le lot, pas un match."""
    from myassistantbet import db
    from myassistantbet.services import board
    from myassistantbet.services.context import store
    from myassistantbet.services.manual import build, save
    from myassistantbet.services.weather import KIND_WEATHER

    event_id = save(
        build(
            "tennis",
            "ATP Cincinnati Open",
            "Shevchenko",
            "O'Connell",
            "2099-01-01",
            "23:00",
            "Shevchenko 1.99\nO'Connell 1.87",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    session_id = board.toggle_selection(event_id, True, migrated)
    store(
        event_id,
        KIND_WEATHER,
        {
            "alerts": [{"event": "Flood Watch", "sender": "NWS Wilmington OH"}],
            "alerts_checked": True,
            "alert_source": "NWS",
            "fetched_at": "2099-01-01T20:00:00Z",
        },
        migrated,
    )
    db.execute(
        "UPDATE events SET previous_commence_time = '2098-12-31T17:55:00Z', "
        "commence_shifted_at = '2098-12-31T20:14:00Z' WHERE id = ?",
        (event_id,),
        settings=migrated,
    )

    corps = build_prompt(session_id, settings=migrated).body

    assert "une alerte meteo en vigueur" in corps
    assert corps.index("une alerte meteo en vigueur") < corps.index("### M1")


def test_les_facteurs_independants_se_comptent_par_editeur(migrated: Settings) -> None:
    """**Le mot « independant » servait deux fois, et n'etait defini que pour
    l'autre.**

    En section C il designe deux selections qui tombent ensemble ; dans la table
    de confiance, deux faits qui se confirment. Par contagion, deux absences du
    meme effectif se lisaient comme un seul facteur, et le cran 5 devenait
    inatteignable — les blocs ne comptant jamais comme source, il aurait fallu
    deux faits de recherche portant sur deux clubs differents.

    Le critere est **l'editeur**, pas la source, ni la date, ni la page. Le
    defaut a d'ailleurs ete rencontre dans le code avant le prompt : `/coachs` et
    `/fixtures/lineups` sont deux endpoints, deux dates, **un seul editeur** —
    leur accord sur Pafos n'etait pas une corroboration mais la meme erreur lue
    deux fois.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "**deux éditeurs distincts**" in corps
    assert "sont **un seul facteur**" in corps
    assert "l'éditeur d'origine est le club" in corps
    assert "se tromper séparément" in corps
    # Et la distinction avec la section D est dite, sans quoi le mot recouvrirait
    # de nouveau les deux notions.
    assert "pas la corrélation des issues" in corps


def test_la_section_f_porte_trois_rubriques_nommees(migrated: Settings) -> None:
    """Trois demandes heterogenes se disputaient un budget de trois lignes.

    Les marches manquants, les informations non trouvees et les dossiers non
    ouverts repondent a trois questions differentes ; sous un plafond commun, la
    premiere rubrique remplie evincait les autres. Sur le lot du 13/08 la
    troisieme etait vide — tous les marches demandes apparaissaient — et le
    format ne permettait pas de dire « aucun » sans consommer le budget.
    """
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "**Dossiers non ouverts**" in corps
    assert "**Informations non trouvées**" in corps
    assert "**Marchés manquants**" in corps
    assert "« aucun » est une réponse" in corps


def test_le_plafond_de_la_section_b_est_asymetrique(migrated: Settings) -> None:
    """Un plafond unique rationnait un dossier riche et laissait s'etaler un
    renoncement. Un PASSE bien argumente est court par nature ; s'il s'allonge,
    c'est qu'il hesite."""
    corps = " ".join(build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split())

    assert "**12 lignes** sur un dossier qui porte au moins un fait nommé et daté" in corps
    assert "**4 lignes** sur un PASSE" in corps
    assert "8 lignes maximum chacune" not in corps, "l'ancien plafond uniforme a disparu"


def _lot_de_coupe(migrated: Settings) -> int:
    """Un lot d'un match, rattache a une competition a agregats domestiques."""
    event_id = save(
        build(
            "football",
            "DFB-Pokal",
            "Hansa Rostock",
            "VfB Stuttgart",
            "2026-08-04",
            "20:45",
            "Hansa Rostock 4.10\nNul 3.80\nVfB Stuttgart 1.75",
            "",
            "",
            settings=migrated,
        ),
        migrated,
    )
    db.execute(
        "UPDATE competitions SET oddsapi_key = 'soccer_germany_dfb_pokal' "
        "WHERE id = (SELECT competition_id FROM events WHERE id = ?)",
        (event_id,),
        settings=migrated,
    )
    return board_service.toggle_selection(event_id, True, migrated)


#: La phrase que la porte du preambule commande. Ecrite une fois : les deux
#: tests qui l'encadrent doivent viser exactement le meme texte, sinon l'un des
#: deux passerait sur une reformulation.
AGREGATS_DE_COUPE = "les agrégats de saison viennent du championnat domestique"


def test_le_mode_d_emploi_des_agregats_de_coupe_parait_sur_une_coupe(
    migrated: Settings,
) -> None:
    """Sans lui, « 1er » contre « 8e » se lit comme un match equilibre."""
    coupe = " ".join(build_prompt(_lot_de_coupe(migrated), settings=migrated, now=NOW).body.split())

    assert AGREGATS_DE_COUPE in coupe
    # Le fait de la rencontre est l'ecart de division, pas la difference de rangs.
    assert "Les deux tables ne se comparent donc pas" in coupe


def test_le_mode_d_emploi_des_agregats_de_coupe_ne_se_paie_pas_ailleurs(
    migrated: Settings,
) -> None:
    """Meme regle que les libelles de contexte, un cran plus loin.

    Une soiree de championnat n'a aucune lecture croisee a expliquer : lui
    facturer le paragraphe reviendrait a payer le mode d'emploi d'une ligne
    qu'aucun bloc ne porte — le defaut que les portes du preambule corrigent.

    **Le lot se monte dans une base neuve, et c'est le piege du test** : les
    deux `toggle_selection` d'une meme journee alimentent la **meme** session,
    si bien qu'un lot de championnat monte a la suite d'un lot de coupe porte
    encore le match de coupe — et la porte s'ouvre pour la bonne raison.
    """
    championnat = " ".join(
        build_prompt(_lot_de(migrated, 2), settings=migrated, now=NOW).body.split()
    )

    assert AGREGATS_DE_COUPE not in championnat
