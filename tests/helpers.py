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
        # Les feuilles des derniers matchs : un enrichissement complet les
        # demande des que la competition ne couvre pas les absents. Sans cette
        # route, tout test qui simule une couverture manquante tombe sur un
        # appel non simule — ce qui est arrive a deux d'entre eux.
        "lineups": _mock("/fixtures/lineups", "apifootball_lineups.json"),
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


# -- Le recul du bloc de retour d'experience --------------------------------
#
# **Le gate est ferme en production**, et il le restera un moment : 4 journees
# d'analyse distinctes sur les 10 requises, pour 60 selections tranchees sur 40.
# Rien de ce qui concerne les bandes de confiance ne se voit donc dans un prompt
# reel aujourd'hui — ni la cible, ni l'ecart, ni la mention « hors bande ».
#
# Tout ce qui touche a ces lignes se valide donc sur une fixture qui **ouvre le
# gate**, et jamais sur un rendu de production. Elle vit ici parce que trois lots
# en dependent : l'etat « pas de cible », l'effectif minimum et les cibles
# relatives. Trois copies auraient diverge au premier seuil deplace.


def lot_avec_recul(
    settings: Any,
    *,
    confiances: dict[int, tuple[int, int]] | None = None,
    market: str = "O/U 2.5",
) -> int:
    """Une session dont les selections franchissent **les deux** seuils du gate.

    `confiances` donne, par cran, le couple `(gagnees, perdues)`. Le defaut
    reproduit la forme reelle de la base — un cran 3 large et legerement sous le
    taux global, un cran 4 plus etroit et au-dessus — parce que c'est cette forme
    que les bandes ont a decrire, et qu'une fixture uniforme rendrait toute cible
    egalement satisfaite.

    L'**etalement** compte autant que le volume : les selections sont reparties
    sur `FEEDBACK_MIN_DAYS` journees d'analyse distinctes. Un lot nombreux mais
    concentre mesure ces jours-la, et le gate le refuse — c'est le seul des deux
    seuils qu'une fixture naive oublie.
    """
    from myassistantbet import db
    from myassistantbet.services import board, coupons
    from myassistantbet.services.history import FEEDBACK_MIN_DAYS, add_pick, set_result
    from myassistantbet.services.manual import build, save

    # Le total tient **exactement** dans `FEEDBACK_WINDOW` : au-dela, la fenetre
    # glissante tronque, et elle tronque par date — le cran le moins fourni
    # disparaissait alors du rendu sans que la fixture le dise.
    #
    # Taux obtenus : cran 3 a 40 %, crans 4 et 5 a 60 %, global a 50 %. Un cran
    # sous sa cible, un dedans, un legerement dessous : trois etats distincts,
    # ce qu'une repartition uniforme ne donnerait pas.
    repartition = confiances or {3: (12, 18), 4: (12, 8), 5: (6, 4)}
    event_id = save(
        build(
            "football",
            "Amical",
            "Lyon",
            "Nice",
            "2026-08-04",
            "20:45",
            "Lyon 2.10\nNice 3.40",
            "",
            "",
            settings=settings,
        ),
        settings,
    )
    session_id = board.toggle_selection(event_id, True, settings)

    rang = 0
    for cran, (gagnees, perdues) in sorted(repartition.items()):
        for index in range(gagnees + perdues):
            pick_id = add_pick(
                session_id,
                tier="fun",
                market=market,
                selection=f"Over {cran}-{index}",
                event_id=str(event_id),
                price="2.10",
                confidence=str(cran),
                # Ces lots montent plusieurs selections sur un meme match par
                # commodite : la note d'independance est fournie d'office, un
                # test dedie verifiant qu'elle est bien exigee.
                independence_note="angles indépendants (fixture)",
                settings=settings,
            )
            set_result(pick_id, "win" if index < gagnees else "loss", settings)
            db.execute(
                "UPDATE picks SET created_at = ? WHERE id = ?",
                (f"2026-07-{1 + rang % FEEDBACK_MIN_DAYS:02d}T12:00:00Z", pick_id),
                settings=settings,
            )
            # « Joue » veut dire pose chez le bookmaker, donc rattache a un
            # coupon. Le marquer a la main ferait passer la fixture sans que le
            # parcours reel fonctionne.
            coupons.create(session_id, [pick_id], settings=settings)
            rang += 1
    return session_id


def pose_bandes(settings: Any, bandes: dict[int, tuple[float | None, float | None]]) -> None:
    """Ecrit les bandes cibles voulues, sans passer par la route ni le formulaire."""
    from myassistantbet import db

    for level, (low, high) in bandes.items():
        db.execute(
            "UPDATE confidence_bands SET low = ?, high = ? WHERE level = ?",
            (low, high, level),
            settings=settings,
        )
