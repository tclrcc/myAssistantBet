"""Constantes et simulations partagees par les tests.

Les deux jeux de routes API-Football vivent **ici** et non dans le fichier de
test qui les a vus naitre : trois fichiers en ont besoin — le contexte, le
dossier, et la mesure du budget de tokens — et trois copies auraient diverge au
premier endpoint ajoute. Le prompt aurait alors ete mesure sur un bloc plus
pauvre que celui qui part vraiment.
"""

from __future__ import annotations

import html
import re
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
        # **Une plage et non une date** : le rapprochement cherche la rencontre
        # sur `FIXTURE_WINDOW_DAYS` jours de part et d'autre, parce que les deux
        # fournisseurs ne datent pas toujours un match du meme jour. Le
        # selecteur suit, sans quoi la route ne repond plus et chaque test qui
        # enrichit tombe sur un appel non simule.
        "fixtures_date": _mock(
            "/fixtures",
            "apifootball_fixtures_date.json",
            params__contains={"from": "2026-08-02", "to": "2026-08-04"},
        ),
        "standings": _mock("/standings", "apifootball_standings.json"),
        # Le pays d'un stade : ni `/fixtures` ni `/teams` ne le servent, et le
        # deduire d'un nom de ville serait une invention. L'appel n'est emis que
        # sur une rencontre effectivement deplacee.
        "venue": _mock("/venues", "apifootball_venue.json"),
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
    """Une session dont les selections franchissent **tous** les seuils du gate.

    `confiances` donne, par cran, le couple `(gagnees, perdues)`. Le defaut
    reproduit la forme reelle de la base — un cran 3 large et legerement sous le
    taux global, un cran 4 plus etroit et au-dessus — parce que c'est cette forme
    que les bandes ont a decrire, et qu'une fixture uniforme rendrait toute cible
    egalement satisfaite.

    L'**etalement** compte autant que le volume : les selections sont reparties
    sur `FEEDBACK_MIN_DAYS` journees d'analyse distinctes, et sur
    `FEEDBACK_MIN_COMPETITIONS` competitions. Un lot nombreux mais concentre
    mesure ces jours-la, et le gate le refuse — ce sont les seuils qu'une fixture
    naive oublie.

    **Le seuil par cellule est abaisse ici, et c'est le seul reglage que ce
    helper touche.** Il vaut 100 par defaut pour une fenetre de 60 : aucune
    cellule ne peut donc jamais l'atteindre, et une fixture qui le laisserait
    en place testerait un bloc vide en croyant tester ses lignes. L'abaisser est
    exactement le role de ce helper — ouvrir les gates — mais il faut savoir que
    le defaut, lui, les ferme.
    """
    from myassistantbet import db
    from myassistantbet.services import board
    from myassistantbet.services.history import (
        FEEDBACK_MIN_COMPETITIONS,
        FEEDBACK_MIN_DAYS,
        add_pick,
        set_result,
    )
    from myassistantbet.services.manual import build, save
    from myassistantbet.services.thresholds import save as save_threshold

    save_threshold("feedback_min_cell", "10", settings)

    # Le total tient **exactement** dans `FEEDBACK_WINDOW` : au-dela, la fenetre
    # glissante tronque, et elle tronque par date — le cran le moins fourni
    # disparaissait alors du rendu sans que la fixture le dise.
    #
    # Taux obtenus : cran 3 a 40 %, crans 4 et 5 a 60 %, global a 50 %. Un cran
    # sous sa cible, un dedans, un legerement dessous : trois etats distincts,
    # ce qu'une repartition uniforme ne donnerait pas.
    repartition = confiances or {3: (12, 18), 4: (12, 8), 5: (6, 4)}
    # **Trois competitions, pas une.** Dix journees sur un seul tournoi passent le
    # compte de journees sans decrire autre chose qu'un contexte : c'est ce que le
    # troisieme seuil du gate refuse, et une fixture a une competition le
    # declencherait a chaque test.
    evenements = [
        save(
            build(
                "football",
                f"Amical {rang + 1}",
                f"Lyon {rang + 1}",
                f"Nice {rang + 1}",
                "2099-01-01",
                "20:45",
                f"Lyon {rang + 1} 2.10\nNice {rang + 1} 3.40",
                "",
                "",
                settings=settings,
            ),
            settings,
        )
        for rang in range(FEEDBACK_MIN_COMPETITIONS)
    ]
    session_id = 0
    for identifiant in evenements:
        session_id = board.toggle_selection(identifiant, True, settings)

    rang = 0
    for cran, (gagnees, perdues) in sorted(repartition.items()):
        for index in range(gagnees + perdues):
            pick_id = add_pick(
                session_id,
                tier="fun",
                market=market,
                selection=f"Over {cran}-{index}",
                event_id=str(evenements[rang % len(evenements)]),
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


def migre_jusqu_a(settings: Any, version: int) -> list[str]:
    """Applique les migrations **jusqu'a** une version, et pas au-dela.

    `run_migrations` ne connait pas de cible : il applique tout ce qui manque.
    On lui donne donc un dossier qui ne contient que les migrations voulues —
    c'est un parametre depuis toujours, et c'est plus honnete qu'un drapeau
    ajoute au moteur pour les seuls tests.

    Sert la **regle de non-regression des populations** : un indicateur de la
    page doit etre identique avant et apres une migration, hors changement
    explicitement demande. Le verifier a l'oeil ne tient pas sur trente cartes.
    """
    import shutil
    import tempfile
    from pathlib import Path

    from myassistantbet import db
    from myassistantbet.backup import TEMP_PREFIX

    source = settings.migrations_dir
    # **Le repertoire se retire apres usage, et il porte le prefixe du projet.**
    # Sans le premier, chaque appel laissait un dossier derriere lui : 208 en
    # quinze jours, 63 Mo, sur un `/tmp` qui est de la memoire vive. Sans le
    # second, aucune purge ne pouvait les reclamer — `tempfile.mkdtemp()` rend un
    # nom que rien ne rattache a ce depot.
    dossier = Path(tempfile.mkdtemp(prefix=TEMP_PREFIX))
    try:
        for chemin in sorted(source.glob("*.sql")):
            if int(chemin.name.split("_", 1)[0]) <= version:
                shutil.copy(chemin, dossier / chemin.name)
        return db.run_migrations(settings, migrations_dir=dossier)
    finally:
        shutil.rmtree(dossier, ignore_errors=True)


#: Le formulaire d'apercu d'import, tel que le gabarit le rend.
_IMPORT_FORM = re.compile(
    r'<form method="post" action="/history/\d+/picks/import">(.*?)</form>', re.S
)


def repost_import_form(page: str) -> dict[str, object]:
    """Le formulaire d'apercu, renvoye comme un navigateur le ferait.

    **Rien n'est fabrique ici** : les champs sont ceux que le gabarit a rendus,
    avec leurs valeurs et leurs cases deja cochees. Un test qui construirait le
    corps a la main testerait sa propre idee du formulaire, et c'est exactement
    ce qui a permis a la porte de se fermer sans que rien ne tombe.

    **Partage plutot que recopie**, et pour la raison que ce projet paie
    partout : deux fichiers de test qui reposteraient chacun leur idee du
    formulaire finiraient par ne plus poster le meme, et le second cesserait
    silencieusement de couvrir ce que le premier garde. Un champ cache ajoute
    au gabarit voyage desormais dans tous les tests qui passent par ici.
    """
    corps = _IMPORT_FORM.search(page)
    assert corps is not None, (
        "aucun formulaire d'import dans l'aperçu — un collage complet doit être "
        "importable, et il ne l'est plus"
    )
    corps = corps.group(1)
    champs: list[tuple[str, str]] = []
    for balise in re.finditer(r"<input\b([^>]*)>", corps):
        attributs = balise.group(1)
        nom = re.search(r'name="([^"]*)"', attributs)
        if nom is None:
            continue
        genre = (re.search(r'type="([^"]*)"', attributs) or [None, "text"])[1]
        if genre == "checkbox" and "checked" not in attributs:
            continue
        valeur = re.search(r"value=(?:\"([^\"]*)\"|'([^']*)')", attributs)
        brut = ""
        if valeur is not None:
            brut = valeur.group(1) if valeur.group(1) is not None else (valeur.group(2) or "")
        champs.append((html.unescape(nom.group(1)), html.unescape(brut)))
    for menu in re.finditer(r'<select\s+name="([^"]*)"[^>]*>(.*?)</select>', corps, re.S):
        options = menu.group(2)
        choisie = re.search(r'<option value="([^"]*)"[^>]*\bselected\b', options) or re.search(
            r'<option value="([^"]*)"', options
        )
        champs.append((html.unescape(menu.group(1)), html.unescape(choisie.group(1))))
    envoi: dict[str, object] = {}
    for nom, valeur in champs:
        if nom in envoi:
            deja = envoi[nom]
            envoi[nom] = (deja if isinstance(deja, list) else [deja]) + [valeur]
        else:
            envoi[nom] = valeur
    return envoi


def alias_de(sql: str, table: str) -> set[str]:
    """Les alias sous lesquels `table` est nommee dans cette requete.

    **Une seule ecriture, deux lecteurs.** `test_plan_requetes` s'en sert pour
    savoir si un `SCAN` porte sur une table chaude ; le controle de coherence du
    critere s'en sert pour rattacher `c.api_active` a `competitions`. Deux
    analyses paralleles du meme SQL auraient fini par ne plus designer les memes
    colonnes — le motif du §8, applique a un outil de test.

    Lu **sur la requete** et non ecrit en dur : `EXPLAIN QUERY PLAN` designe une
    sous-requete par son alias (`SCAN q`) et non par sa table, et un alias
    renomme ferait passer les deux controles sans rien verifier.
    """
    trouves = {table}
    for m in re.finditer(rf"\b{table}\b(?:\s+AS)?\s+(\w+)", sql, re.IGNORECASE):
        mot = m.group(1)
        if mot.upper() not in {"WHERE", "ON", "SET", "VALUES", "GROUP", "ORDER", "AS", "JOIN"}:
            trouves.add(mot)
    return trouves
