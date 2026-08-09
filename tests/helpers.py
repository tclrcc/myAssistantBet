"""Constantes et simulations partagees par les tests.

Les deux jeux de routes API-Football vivent **ici** et non dans le fichier de
test qui les a vus naitre : trois fichiers en ont besoin — le contexte, le
dossier, et la mesure du budget de tokens — et trois copies auraient diverge au
premier endpoint ajoute. Le prompt aurait alors ete mesure sur un bloc plus
pauvre que celui qui part vraiment.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import httpx
import respx

from myassistantbet.providers.apifootball import BASE_URL

#: Instant de reference des tests. La fenetre de scan par defaut couvre alors
#: le 3 et le 4 aout 2026 (heure de Paris).
NOW = datetime(2026, 8, 3, 10, 0, tzinfo=UTC)

#: Headers de quota renvoyes par The Odds API sur un scan a deux marches.
QUOTA_HEADERS = {
    "x-requests-remaining": "4821",
    "x-requests-used": "179",
    "x-requests-last": "2",
}

#: Headers de quota API-Football pour le bloc CONTEXTE, qu'aucun plancher ne
#: garde : le contexte d'un match est la fonction premiere de l'outil.
RATE_HEADERS = {"x-ratelimit-requests-remaining": "82", "x-ratelimit-requests-limit": "100"}

#: Headers du dossier d'equipe, et la difference n'est **pas** cosmetique : lui
#: s'arrete sous `apifootball_call_floor` (500). Avec les 82 appels restants
#: ci-dessus, tout test de dossier mesurerait le plancher au lieu du dossier —
#: le releve n'est jamais repaye, mais parce qu'il n'est jamais demande.
DOSSIER_RATE_HEADERS = {
    "x-ratelimit-requests-remaining": "4300",
    "x-ratelimit-requests-limit": "7500",
}

#: Ligue par defaut des tests. L'Allsvenskan **n'est pas** une competition a props
#: buteurs : c'est le cas majoritaire, et celui ou la ligne « Buteurs » ne doit
#: rien couter. `PROPS_LEAGUE` sert aux tests qui la veulent.
LEAGUE = 113
PROPS_LEAGUE = 39

#: Identifiants d'equipe auxquels les fixtures de rapprochement aboutissent.
HOME_TEAM = "376"
AWAY_TEAM = "377"


def mock_context_routes(
    load_fixture: Any, headers: dict[str, str] = RATE_HEADERS
) -> dict[str, respx.Route]:
    """Repond a tous les endpoints du bloc CONTEXTE avec les fixtures capturees.

    Rend les routes **par nom** et non par position : les designer par
    `respx.routes[1]` cassait chaque test des qu'un appel etait ajoute, ce qui
    poussait a inserer les nouveaux mocks a la fin pour de mauvaises raisons.

    `headers` existe pour **enchainer contexte et dossier** dans un meme test.
    Le plancher lit `last_known_quota`, donc le dernier releve tous endpoints
    confondus : un contexte simule a 82 appels restants suffit a suspendre le
    dossier qui le suit, et le bloc sort sans entraineur ni historique de saison
    sans qu'aucune erreur ne soit levee. Un enrichissement complet se simule
    donc de bout en bout avec `DOSSIER_RATE_HEADERS`, ce qui est aussi le cas
    reel — on n'enrichit pas un lot avec 82 appels en poche.
    """

    def _mock(chemin: str, fichier: str, **selecteurs: Any) -> respx.Route:
        return respx.get(f"{BASE_URL}{chemin}", **selecteurs).mock(
            return_value=httpx.Response(200, json=load_fixture(fichier), headers=headers)
        )

    return {
        "fixtures_date": _mock(
            "/fixtures",
            "apifootball_fixtures_date.json",
            params__contains={"date": "2026-08-03"},
        ),
        "standings": _mock("/standings", "apifootball_standings.json"),
        "stats_home": _mock(
            "/teams/statistics",
            "apifootball_stats_home.json",
            params__contains={"team": HOME_TEAM},
        ),
        "stats_away": _mock(
            "/teams/statistics",
            "apifootball_stats_away.json",
            params__contains={"team": AWAY_TEAM},
        ),
        "recent_home": _mock(
            "/fixtures",
            "apifootball_recent_home.json",
            params__contains={"team": HOME_TEAM},
        ),
        "recent_away": _mock(
            "/fixtures",
            "apifootball_recent_away.json",
            params__contains={"team": AWAY_TEAM},
        ),
        "injuries": _mock("/injuries", "apifootball_injuries.json"),
        "h2h": _mock("/fixtures/headtohead", "apifootball_h2h.json"),
        "leagues": _mock("/leagues", "apifootball_leagues.json"),
        # Le dossier d'equipe fait partie d'un enrichissement complet depuis
        # qu'il existe : sans ces deux routes, tout test qui enrichit tomberait
        # sur un appel non simule.
        "coachs_home": _mock(
            "/coachs",
            "apifootball_coachs_home.json",
            params__contains={"team": HOME_TEAM},
        ),
        "coachs_away": _mock(
            "/coachs",
            "apifootball_coachs_away.json",
            params__contains={"team": AWAY_TEAM},
        ),
        "fixture_stats": _mock("/fixtures/statistics", "apifootball_fixture_statistics.json"),
        "team": _mock("/teams", "apifootball_team.json"),
    }


def mock_dossier_routes(load_fixture: Any) -> dict[str, respx.Route]:
    """Repond a tout ce que le dossier d'equipe peut demander.

    Les fixtures de saison viennent d'une charge utile reelle : la saison en cours
    ne porte que des amicaux joues et des matchs a venir — la situation reelle
    d'un mois d'aout — et la precedente porte une saison complete. C'est ce qui
    fait du repli sur N-1 le cas normal et non un cas limite.

    Toutes ces routes rendent `DOSSIER_RATE_HEADERS` : sous le plancher, le
    dossier se suspend en silence et les tests mesureraient le plancher.
    """

    def _mock(chemin: str, fichier: str, **selecteurs: Any) -> respx.Route:
        return respx.get(f"{BASE_URL}{chemin}", **selecteurs).mock(
            return_value=httpx.Response(
                200, json=load_fixture(fichier), headers=DOSSIER_RATE_HEADERS
            )
        )

    def _saison(fichier: str, team: str, season: str) -> respx.Route:
        return _mock("/fixtures", fichier, params__contains={"team": team, "season": season})

    return {
        "home": _mock(
            "/coachs", "apifootball_coachs_home.json", params__contains={"team": HOME_TEAM}
        ),
        "away": _mock(
            "/coachs", "apifootball_coachs_away.json", params__contains={"team": AWAY_TEAM}
        ),
        "season_home": _saison("apifootball_fixtures_season_home.json", HOME_TEAM, "2026"),
        "season_away": _saison("apifootball_fixtures_season_away.json", AWAY_TEAM, "2026"),
        "season_home_prev": _saison(
            "apifootball_fixtures_season_home_prev.json", HOME_TEAM, "2025"
        ),
        "season_away_prev": _saison(
            "apifootball_fixtures_season_away_prev.json", AWAY_TEAM, "2025"
        ),
        # Un seul appel pour toute la competition : c'est ce qui fait de cet
        # endpoint le seul dont le cout ne croit pas avec la taille du lot.
        "scorers": _mock(
            "/players/topscorers",
            "apifootball_topscorers.json",
            params__contains={"league": str(PROPS_LEAGUE)},
        ),
        # Un appel par joueur : la route repond vide par defaut, et les tests qui
        # veulent une absence la posent eux-memes.
        "sidelined": respx.get(f"{BASE_URL}/sidelined").mock(
            return_value=httpx.Response(
                200, json={"errors": [], "response": []}, headers=DOSSIER_RATE_HEADERS
            )
        ),
    }
