"""Contexte sportif : forme, classement, absents, confrontations directes.

Deux temps nettement separes :

- `fetch_context()` interroge API-Football et **persiste les charges utiles brutes**
  dans la table `context` ;
- `context_lines()` relit la base et produit les lignes du bloc CONTEXTE.

Cette separation permet de regenerer un prompt autant de fois qu'on veut sans
retoucher au reseau, et de rendre explicite ce qui manque plutot que de le taire.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.apifootball import APIFootballClient
from ..providers.base import ProviderError
from ..providers.weather import WeatherClient
from .labels import sort_key
from .matching import Resolution, resolve_team
from .render import UNAVAILABLE
from .thresholds import value_of

logger = logging.getLogger(__name__)


class _NotCovered(Exception):
    """Le fournisseur declare ne pas couvrir cette donnee pour la competition.

    Distincte d'une erreur : rien n'a echoue, il n'y a simplement rien a
    chercher. La ligne existe quand meme, avec la mention « non disponible ».
    """


H2H_LAST = 5
RECENT_LAST = 5

#: Feuilles de match relues pour reconstruire un effectif la ou le fournisseur
#: ne couvre pas les absents. Quatre suffisent a la regle et bornent la depense :
#: c'est **le seul ajout du projet qui coute des appels par equipe**, un par
#: feuille, et il ne part que sur les competitions ou `/injuries` ne repond pas.
#:
#: Mesure qui le justifie : `coverage.injuries` est faux sur **46 des 65**
#: evenements rapproches en base, soit 71 %, quand `coverage.fixtures.lineups`
#: est vrai sur 55. La ligne la plus decisive du bloc est morte sur trois quarts
#: du board, et la matiere premiere pour la reconstruire est servie.
SHEETS_LAST = 4
#: Feuilles consecutives sans le joueur pour qu'il soit signale. Une seule serait
#: une rotation ordinaire.
SHEETS_MISSED = 2
#: Feuilles ou il faut l'avoir vu avant : sans ce seuil, un jeune apparu une fois
#: puis redescendu en reserve passerait pour un absent.
SHEETS_MIN = 2
#: Joueurs listes par equipe. Au-dela, on decrit une reserve, plus une absence.
SHEETS_KEEP = 3
#: Statuts d'un match qui n'a **pas** de feuille. La liste est ecrite dans ce
#: sens : `/fixtures?last=` ne rend que des matchs joues, et le statut y manque
#: parfois — exiger un statut connu ferait tout jeter, alors qu'ecarter ce qui
#: est explicitement non joue ne peut rien couter.
SHEET_SKIP_STATUSES = frozenset({"NS", "TBD", "PST", "CANC", "ABD", "SUSP"})
FORM_LENGTH = 5

#: Matchs profiles pour les corners et les cartons. Un appel par rencontre,
#: donc au plus 2 x PROFILE_LAST par affiche — moins des que les deux equipes
#: se sont croisees. Cinq matchs disent une tendance ; trois diraient un hasard.
PROFILE_LAST = 5

#: Sous ce nombre de matchs effectivement profiles, aucune ligne. La couverture
#: statistique est irreguliere : en debut de saison ou sur une petite
#: competition, un seul des cinq derniers matchs revient renseigne. « 2.0
#: corners pris 9.0 » sur une rencontre se lit comme une tendance alors que
#: c'est une soiree — meme raison que le seuil du retour d'experience.
PROFILE_MIN_MATCHES = 3

#: Sous ce nombre de matchs joues dans la competition, aucune ligne de saison.
#: Meme raison que ci-dessus, appliquee aux fractions : « >1.5 dans 3/3 » se lit
#: comme une tendance alors que c'est un mois d'aout. Le fournisseur repond par
#: ailleurs des zeros partout sur une equipe qui n'a encore rien joue dans la
#: competition — le cas de toute equipe entrant en qualification europeenne.
SEASON_MIN_MATCHES = 5

#: Lignes de buts d'equipe rendues, dans l'ordre. Ce sont celles que le book
#: propose sur `team_totals` : au-dela de 2.5, une equipe seule n'y va presque
#: jamais, et la fraction serait nulle sur toute la ligue.
TEAM_TOTAL_LINES = ("0.5", "1.5", "2.5")

#: Tranches de quinze minutes, telles que le fournisseur les nomme. Deux pieges
#: verifies sur charge utile reelle : une tranche vide vaut `null` et non zero,
#: et il existe une tranche de libelle **vide** — un carton dont la minute n'est
#: pas connue. Elle n'appartient a aucune mi-temps mais compte au total : l'omettre
#: du denominateur surestimerait la part des cartons tardifs.
FIRST_HALF_BANDS = ("0-15", "16-30", "31-45")
LATE_BANDS = ("61-75", "76-90", "91-105")

#: Fenetre des buts tardifs, plus etroite que celle des cartons — et la
#: difference est **mesuree**, pas esthetique. Sur les equipes en base ayant
#: marque ou encaisse au moins vingt buts, les deux fenetres ont le meme ecart
#: absolu entre premier et dernier decile (25 points), mais pas la meme base :
#: mediane de 39 % apres la 60e contre 24 % apres la 75e. Rapporte a sa base, le
#: quart d'heure final discrimine donc deux fois plus — 12 % contre 38 % est un
#: rapport de trois, 28 % contre 53 % n'en fait pas deux. Chaque ligne ecrit sa
#: fenetre dans sa valeur, comme « Cartons tps », pour qu'aucune des deux ne se
#: lise a la place de l'autre.
LATE_GOAL_BANDS = ("76-90", "91-105")

#: Formations rendues au plus. Deux suffisent a dire « une equipe stable » ou
#: « un effectif tournant » ; les sept que le fournisseur peut lister ne
#: diraient rien de plus pour sept fois le cout en tokens.
FORMATIONS_KEEP = 2

#: Lettres API-Football -> lettres francaises. Attention au piege : « D » cote
#: API signifie Draw (nul), et « L » signifie Loss (defaite).
FORM_LETTERS = {"W": "V", "D": "N", "L": "D"}

KIND_STANDINGS = "standings"
KIND_FORM = "form"
KIND_INJURIES = "injuries"
#: Effectif reconstruit des feuilles de match recentes, la ou `/injuries` ne
#: couvre pas. Ce n'est pas une liste d'absents : c'est une liste de joueurs
#: qu'on ne voit plus, ce qui n'est pas la meme chose et se rend comme tel.
KIND_SHEETS = "sheets"
KIND_H2H = "h2h"
KIND_RECENT = "recent"
KIND_PROFILE = "profile"
KIND_VENUE = "venue"
KIND_REFEREE = "referee"

#: Les trois etats d'une liste d'absents, et c'est le motif de toute la serie :
#: « on a regarde, il n'y a rien », « personne n'a regarde », « la source n'a pas
#: repondu ». `donnees non disponibles` les melangeait, si bien qu'une
#: competition non couverte se lisait comme un incident et l'inverse.
#:
#: Les trois appellent des comportements differents : le premier ne demande
#: rien, le deuxieme envoie chercher a la main pour toujours, le troisieme se
#: retente au prochain enrichissement.
INJURIES_SERVED = "served"
INJURIES_NOT_ASKED = "not_asked"
INJURIES_UNREACHABLE = "unreachable"

INJURIES_NOTES = {
    INJURIES_NOT_ASKED: (
        "non interroges — le fournisseur ne couvre pas les absents sur cette "
        "competition, la recherche est le seul chemin"
    ),
    INJURIES_UNREACHABLE: "source injoignable au dernier releve — a retenter ou a chercher",
}
KIND_LINEUPS = "lineups"
KIND_MAPPING = "mapping_pending"
KIND_MANUAL_NOTE = "manual_note"

#: Minutes avant le coup d'envoi en deca desquelles la composition est demandee.
#:
#: Mesure en reel, et c'est elle qui fixe la regle : sur trois matchs a 2h30,
#: 3h30 et 5h45 du coup d'envoi, `/fixtures/lineups` a rendu **zero equipe** ;
#: sur un match a 8 minutes, les deux compositions completes. Les clubs
#: publient environ une heure avant, et le fournisseur ne devine pas.
#:
#: Appeler plus tot depenserait un appel pour une reponse vide, a chaque
#: enrichissement de chaque match. En deca de la fenetre, la ligne n'existe
#: donc pas — et son absence ne dit rien de l'equipe, seulement de l'heure.
LINEUP_WINDOW_MINUTES = 90

#: Identifiants API-Football du match, memorises au rapprochement. Ce n'est pas
#: une ligne de contexte — rien ne le rend — mais le point d'entree de tout ce
#: qui se recupere par equipe : sans lui, le dossier d'equipe devrait refaire le
#: rapprochement de noms, donc repayer `/fixtures` a chaque lecture.
KIND_TEAMS = "teams"


@dataclass
class ContextReport:
    """Ce qui a pu etre recupere pour un evenement, et ce qui a manque."""

    event_id: int
    label: str
    mapping_pending: bool = False
    kinds: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.mapping_pending and not self.errors


# -- Persistance ------------------------------------------------------------


def store(event_id: int, kind: str, payload: Any, settings: Settings | None = None) -> None:
    """Remplace la charge utile d'un type de contexte pour cet evenement."""
    with connect(settings) as conn:
        conn.execute("DELETE FROM context WHERE event_id = ? AND kind = ?", (event_id, kind))
        conn.execute(
            "INSERT INTO context (event_id, kind, payload_json, fetched_at) VALUES (?, ?, ?, ?)",
            (event_id, kind, json.dumps(payload, ensure_ascii=False), utcnow()),
        )


def load(event_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """Tout le contexte connu d'un evenement, indexe par type."""
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT kind, payload_json FROM context WHERE event_id = ?", (event_id,)
        ).fetchall()
    return {row["kind"]: json.loads(row["payload_json"]) for row in rows}


def set_mapping_pending(event_id: int, pending: bool, settings: Settings | None = None) -> None:
    with connect(settings) as conn:
        conn.execute(
            "UPDATE events SET mapping_pending = ? WHERE id = ?", (1 if pending else 0, event_id)
        )


# -- Mapping ----------------------------------------------------------------


def _teams_from_fixtures(fixtures: list[dict[str, Any]]) -> list[tuple[int, str]]:
    teams: dict[int, str] = {}
    for fixture in fixtures:
        for side in ("home", "away"):
            team = (fixture.get("teams") or {}).get(side) or {}
            if team.get("id") and team.get("name"):
                teams[int(team["id"])] = str(team["name"])
    return sorted(teams.items())


def _find_fixture(
    fixtures: list[dict[str, Any]], home_id: int, away_id: int
) -> dict[str, Any] | None:
    for fixture in fixtures:
        teams = fixture.get("teams") or {}
        if (teams.get("home") or {}).get("id") == home_id and (teams.get("away") or {}).get(
            "id"
        ) == away_id:
            return fixture
    return None


@dataclass
class FixtureMapping:
    """Le match API-Football correspondant, et de quoi interroger le reste."""

    fixture_id: int
    league_id: int
    season: int
    home_id: int
    away_id: int
    #: Ce que le fournisseur declare couvrir pour cette competition. Une liste
    #: vide ne dit rien tant qu'on ignore si la donnee existe : `injuries:
    #: false` transforme « aucun absent » en « donnee non disponible ».
    coverage: dict[str, Any] = field(default_factory=dict)
    #: Stade du match, tel que le fournisseur le donne. Il **porte un
    #: identifiant** — verifie le 12/08/2026 sur `/teams` et `/fixtures` — et
    #: c'est lui qui sert a voir une delocalisation, jamais le nom de la ville.
    venue: dict[str, Any] = field(default_factory=dict)
    #: Arbitre designe, **nom seul**. Verifie le 12/08/2026 : `fixture.referee`
    #: est une chaine libre, sans identifiant et sans pays — « M. Oliver ». Vide
    #: quand la designation n'est pas tombee.
    referee: str = ""


async def _memoized(cache: dict[str, Any], key: str, coroutine_factory: Any) -> Any:
    """Appelle `coroutine_factory` une seule fois par cle, le temps d'un cache.

    Partage entre le rapprochement et le contexte : deux matchs d'une meme ligue
    ne paient ni la saison, ni le classement, ni les statistiques deux fois.
    """
    if key not in cache:
        cache[key] = await coroutine_factory()
    return cache[key]


async def resolve_fixture(
    client: APIFootballClient,
    event: dict[str, Any],
    settings: Settings | None = None,
    cache: dict[str, Any] | None = None,
) -> FixtureMapping | None:
    """Etablit la correspondance entre un evenement et un match API-Football.

    En cas de doute sur une equipe, marque l'evenement `mapping_pending` et
    memorise les candidats pour le formulaire de resolution manuelle.
    """
    settings = settings or get_settings()
    cache = cache if cache is not None else {}
    league_id = event["apifootball_league_id"]
    date_iso = event["commence_time"][:10]

    season, coverage = await _memoized(
        cache, f"season:{league_id}", lambda: client.season_coverage(league_id)
    )
    fixtures = await client.fixtures_by_date(date_iso, league_id, season)
    teams = _teams_from_fixtures(fixtures)

    home = resolve_team(event["home"], teams, settings)
    away = resolve_team(event["away"], teams, settings)

    if not home.resolved or not away.resolved:
        _record_pending(event, [home, away], settings)
        return None

    fixture = _find_fixture(fixtures, home.matched.apifootball_id, away.matched.apifootball_id)
    if fixture is None:
        _record_pending(
            event, [home, away], settings, reason="aucun match ne reunit ces deux equipes"
        )
        return None

    set_mapping_pending(int(event["id"]), False, settings)
    with connect(settings) as conn:
        conn.execute(
            "DELETE FROM context WHERE event_id = ? AND kind = ?", (event["id"], KIND_MAPPING)
        )
        conn.execute(
            "UPDATE events SET apifootball_fixture_id = ? WHERE id = ?",
            (int(fixture["fixture"]["id"]), event["id"]),
        )

    mapping = FixtureMapping(
        fixture_id=int(fixture["fixture"]["id"]),
        league_id=league_id,
        # La saison portee par le match prime — c'est la sienne. Celle de la
        # ligue ne sert que si le fournisseur l'omet.
        season=int((fixture.get("league") or {}).get("season") or season),
        home_id=home.matched.apifootball_id,
        away_id=away.matched.apifootball_id,
        coverage=coverage,
        venue=(fixture.get("fixture") or {}).get("venue") or {},
        referee=str((fixture.get("fixture") or {}).get("referee") or "").strip(),
    )
    # Memorise pour tout ce qui se recupere par equipe. Volontairement absent de
    # `report.kinds` : ce n'est pas un contexte recupere, c'est le moyen d'en
    # chercher d'autres, et le compter ferait annoncer un type de plus a l'UI.
    store(
        int(event["id"]),
        KIND_TEAMS,
        {
            "home": mapping.home_id,
            "away": mapping.away_id,
            "league": mapping.league_id,
            "season": mapping.season,
            # La couverture vient du meme appel et repond a « ai-je le droit
            # d'appeler ? ». La memoriser evite au dossier d'equipe de repayer
            # `/leagues` pour une reponse deja obtenue.
            "coverage": mapping.coverage,
        },
        settings,
    )
    return mapping


def _record_pending(
    event: dict[str, Any],
    resolutions: list[Resolution],
    settings: Settings,
    reason: str = "correspondance incertaine",
) -> None:
    payload = {
        "reason": reason,
        "teams": [
            {
                "oddsapi_name": resolution.oddsapi_name,
                "resolved": resolution.resolved,
                "candidates": [
                    {"id": item.apifootball_id, "name": item.apifootball_name, "score": item.score}
                    for item in resolution.candidates
                ],
            }
            for resolution in resolutions
        ],
    }
    store(int(event["id"]), KIND_MAPPING, payload, settings)
    set_mapping_pending(int(event["id"]), True, settings)
    logger.info("Mapping en attente pour %s – %s : %s", event["home"], event["away"], reason)


# -- Recuperation -----------------------------------------------------------


def _standings_entry(standings: list[dict[str, Any]], team_id: int) -> dict[str, Any] | None:
    for league in standings:
        groups = ((league.get("league") or {}).get("standings")) or []
        for group in groups:
            for row in group or []:
                if (row.get("team") or {}).get("id") == team_id:
                    return {
                        "rank": row.get("rank"),
                        "points": row.get("points"),
                        "played": ((row.get("all") or {}).get("played")),
                        # `description` porte l'enjeu tel que le fournisseur le
                        # nomme : « Play-offs », « Relegation Round »,
                        # « Promotion - Champions League ». Il arrivait dans le
                        # meme appel et partait a la poubelle, alors que la
                        # fiche de verification du prompt reclame l'enjeu a
                        # chaque match et que la recherche web devait aller le
                        # chercher.
                        "stake": row.get("description"),
                        "diff": row.get("goalsDiff"),
                    }
    return None


def _recent_summary(fixtures: list[dict[str, Any]], team_id: int) -> dict[str, Any]:
    """Buts marques/encaisses et date du dernier match, sur les derniers matchs."""
    goals_for = goals_against = 0
    last_date: str | None = None
    counted = 0
    for fixture in fixtures:
        teams = fixture.get("teams") or {}
        goals = fixture.get("goals") or {}
        home_id = (teams.get("home") or {}).get("id")
        home_goals, away_goals = goals.get("home"), goals.get("away")
        if home_goals is None or away_goals is None:
            continue
        if home_id == team_id:
            goals_for += home_goals
            goals_against += away_goals
        else:
            goals_for += away_goals
            goals_against += home_goals
        counted += 1
        date = (fixture.get("fixture") or {}).get("date")
        if date and (last_date is None or date > last_date):
            last_date = date
    return {
        "goals_for": goals_for,
        "goals_against": goals_against,
        "matches": counted,
        "last_date": last_date,
    }


#: Libelles du fournisseur -> cles du profil. Rapproches **par libelle**, jamais
#: par position : l'ordre de la liste `statistics` varie d'un match a l'autre.
#: Statistiques de match retenues, par libelle du fournisseur. Le rapprochement
#: se fait **par libelle** et jamais par position : l'ordre de la liste
#: `statistics` varie d'un match a l'autre.
#:
#: Un appel en rend dix-huit ; celles qui ne sont pas ici sont jetees avant la
#: base. En garder une de plus ne coute donc **aucun appel** — seulement de la
#: place. `Fouls` accompagne les cartons, marche que l'etage B achete, et
#: `Ball Possession` dit qui subit, ce qu'aucune autre ligne ne donne.
PROFILE_STATS = {
    "Corner Kicks": "corners",
    "Yellow Cards": "yellow",
    "Red Cards": "red",
    "Total Shots": "shots",
    "Shots on Goal": "shots_on",
    "Fouls": "fouls",
    "Ball Possession": "possession",
    # Couverture inegale : la Super League chinoise la rend `null`, et
    # `_stat_value` l'ecarte alors comme n'importe quelle valeur absente. La
    # ligne n'existe donc que la ou le fournisseur la sert.
    "expected_goals": "xg",
}


def _stat_value(entry: dict[str, Any]) -> float | None:
    """Valeur numerique d'une statistique. `null` et « 52% » sont geres."""
    raw = entry.get("value")
    if raw is None:
        return None
    if isinstance(raw, int | float):
        return float(raw)
    text = str(raw).strip().rstrip("%")
    try:
        return float(text)
    except ValueError:
        return None


def _profile_from_fixtures(
    stats_by_fixture: dict[int, list[dict[str, Any]]], team_id: int
) -> dict[str, Any]:
    """Moyennes de corners, cartons et tirs sur les matchs profiles.

    Le « concede » vient de l'adversaire du meme match : un seul appel par
    rencontre donne les deux cotes. Un match dont la statistique manque n'entre
    pas au denominateur — la moyenne porte sur ce qui a ete observe, pas sur ce
    qu'on aurait voulu observer.
    """
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}

    for entries in stats_by_fixture.values():
        for entry in entries:
            side = "" if (entry.get("team") or {}).get("id") == team_id else "_against"
            if side and len(entries) < 2:
                continue
            for item in entry.get("statistics") or []:
                key = PROFILE_STATS.get(str(item.get("type")))
                value = _stat_value(item) if key else None
                if key is None or value is None:
                    continue
                name = f"{key}{side}"
                totals[name] = totals.get(name, 0.0) + value
                counts[name] = counts.get(name, 0) + 1

    profile = {name: round(total / counts[name], 1) for name, total in totals.items()}
    profile["matches"] = len(stats_by_fixture)
    return profile


def _covers_fixture_statistics(coverage: dict[str, Any]) -> bool:
    """Vrai si le fournisseur declare servir les statistiques de match.

    Le drapeau vit dans un **sous-objet** (`coverage.fixtures.statistics_fixtures`),
    la ou `standings` et `injuries` sont a la racine : le lire comme les autres
    renvoyait toujours l'absence. Un champ manquant vaut couvert, meme regle
    qu'ailleurs — on ne fait pas disparaitre des donnees qui arrivaient hier.
    """
    fixtures = coverage.get("fixtures")
    if not isinstance(fixtures, dict):
        return True
    return bool(fixtures.get("statistics_fixtures", True))


def _covers_lineups(coverage: dict[str, Any]) -> bool:
    """Vrai si le fournisseur declare servir les compositions.

    Meme sous-objet que les statistiques de match, meme piege. Le drapeau vaut
    la peine d'etre lu : sur la Super League chinoise, `injuries` est faux quand
    `lineups` est vrai — la composition est donc la seule facon de savoir qui
    joue, la ou la ligne « Absents » ne peut rien dire.
    """
    fixtures = coverage.get("fixtures")
    if not isinstance(fixtures, dict):
        return True
    return bool(fixtures.get("lineups", True))


def _latest_played(fixtures: list[dict[str, Any]], keep: int) -> list[dict[str, Any]]:
    """Les `keep` derniers matchs **joues**, du plus recent au plus ancien.

    Un match reporte figure dans la liste du fournisseur et n'a evidemment
    aucune feuille : le compter userait une place de la fenetre pour rien.
    """
    played = [
        fixture
        for fixture in fixtures
        if (fixture.get("fixture") or {}).get("date")
        and ((fixture.get("fixture") or {}).get("status") or {}).get("short")
        not in SHEET_SKIP_STATUSES
    ]
    played.sort(key=lambda item: str((item.get("fixture") or {}).get("date")), reverse=True)
    return played[:keep]


def _sheet_names(rows: list[dict[str, Any]], team_id: Any) -> list[str]:
    """Tous les noms d'une feuille de match : titulaires **et** remplacants.

    Le banc compte autant que le onze : un joueur sur la feuille est un joueur
    disponible, et c'est la disponibilite qu'on cherche a lire.
    """
    for row in rows:
        if (row.get("team") or {}).get("id") != team_id:
            continue
        return [
            name
            for groupe in ("startXI", "substitutes")
            for entry in row.get(groupe) or []
            if (name := (entry.get("player") or {}).get("name"))
        ]
    return []


def _missing_players(sheets: list[tuple[Any, list[str]]]) -> list[dict[str, Any]]:
    """Joueurs vus regulierement puis disparus, avec la date de leur derniere feuille.

    La regle est volontairement severe : present sur au moins `SHEETS_MIN`
    feuilles de la fenetre, absent des `SHEETS_MISSED` plus recentes. Une seule
    absence est une rotation ordinaire, et il n'existe aucun moyen de distinguer
    ici un blesse d'un joueur mis au repos ou ecarte — c'est pour ca que la ligne
    se rend comme une **piste datee** et jamais comme une absence.

    Rien n'est rendu tant que la fenetre ne porte pas plus de feuilles que le
    nombre de manquees : sans un « avant », il n'y a pas de disparition.
    """
    if len(sheets) <= SHEETS_MISSED:
        return []
    recents = {name for _, names in sheets[:SHEETS_MISSED] for name in names}
    vus: dict[str, tuple[int, Any]] = {}
    for date, names in sheets:
        for name in names:
            compte, dernier = vus.get(name, (0, None))
            vus[name] = (compte + 1, dernier or date)
    manquants = [
        {"name": name, "last": dernier}
        for name, (compte, dernier) in vus.items()
        if name not in recents and compte >= SHEETS_MIN
    ]
    manquants.sort(key=lambda item: str(item["last"]), reverse=True)
    return manquants[:SHEETS_KEEP]


def _window_of(sheets: list[tuple[Any, list[str]]]) -> dict[str, Any]:
    """Ce que la fenetre a **reellement** porte : combien de feuilles, et de quand.

    Mesure qui l'a fait naitre : un bloc a designe trois joueurs comme « plus vus
    depuis le 23/07 » alors qu'ils figuraient sur les feuilles du 30/07 et du
    06/08 — verifie chez le fournisseur, qui les sert aujourd'hui. Le chemin de
    reconstruction a ete rejoue a l'identique sur les memes equipes et ne
    reproduit **pas** le defaut : la regle etait juste, les feuilles ne l'etaient
    pas encore au moment du releve.

    Contre ce genre de panne il n'y a pas de correctif de regle — seulement de
    quoi la voir. La ligne porte donc sa fenetre, comme `Parcours` porte celle de
    nos scans et comme l'en-tete des marches porte l'heure de son releve.
    """
    dates = sorted(str(date) for date, _ in sheets if date)
    if not dates:
        return {}
    return {"count": len(sheets), "first": dates[0], "last": dates[-1]}


def _lineup_payload(rows: list[dict[str, Any]], home_id: Any, away_id: Any) -> dict[str, Any]:
    """Charge utile d'une composition, rangee par cote.

    **Ecrite une seule fois** : `fetch_context` et le balayage planifie la
    construisent tous les deux, et deux mises en forme paralleles de la meme
    reponse auraient fini par diverger — le banc collecte d'un cote, oublie de
    l'autre.
    """
    sides = {home_id: "home", away_id: "away"}
    payload: dict[str, Any] = {}
    for row in rows:
        side = sides.get((row.get("team") or {}).get("id"))
        if side is None:
            continue
        payload[side] = {
            "formation": row.get("formation"),
            "starters": [
                name
                for entry in row.get("startXI") or []
                if (name := (entry.get("player") or {}).get("name"))
            ],
            # Le banc est **collecte et jamais rendu dans le prompt** : vingt-
            # quatre noms de plus y couteraient plus qu'ils n'apprennent. Il est
            # garde parce qu'il ne coute aucun appel de plus et qu'il a sa place
            # sur la fiche, ou l'ecran n'a pas de budget de tokens.
            "bench": [
                name
                for entry in row.get("substitutes") or []
                if (name := (entry.get("player") or {}).get("name"))
            ],
        }
    return payload


def _lineup_due(commence_time: str | None, now: datetime | None = None) -> bool:
    """Vrai si le coup d'envoi est proche, et pas encore passe.

    **La fenetre est bornee des deux cotes.** Ouverte vers le passe, elle
    rendait « imminent » un match joue il y a quatre jours, et chaque
    consultation de sa fiche aurait paye un appel pour une composition
    qu'aucun pari avant-match ne peut plus utiliser. C'est la regle que le
    projet applique deja partout : un match commence quitte le prompt.

    Une heure de match illisible fait renoncer : mieux vaut une ligne absente
    qu'un appel tire au hasard a chaque enrichissement.
    """
    if not commence_time:
        return False
    try:
        moment = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except (AttributeError, ValueError):
        return False
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=UTC)
    delay = moment - (now or datetime.now(UTC))
    return timedelta(0) <= delay <= timedelta(minutes=LINEUP_WINDOW_MINUTES)


async def _fetch_profile(
    client: APIFootballClient,
    cache: dict[str, Any],
    fixtures: list[dict[str, Any]],
    team_id: int,
) -> dict[str, Any]:
    """Profil d'une equipe sur ses derniers matchs joues.

    Memorise par match et non par equipe : deux adversaires qui se sont
    rencontres recemment partagent la rencontre, et elle n'est payee qu'une
    fois. Un match dont les statistiques manquent est simplement absent.
    """
    stats_by_fixture: dict[int, list[dict[str, Any]]] = {}
    for fixture in fixtures[:PROFILE_LAST]:
        fixture_id = (fixture.get("fixture") or {}).get("id")
        if not fixture_id:
            continue
        entries = await _memoized(
            cache,
            f"stats:{fixture_id}",
            lambda fid=int(fixture_id): client.fixture_statistics(fid),
        )
        if entries:
            stats_by_fixture[int(fixture_id)] = entries
    if not stats_by_fixture:
        return {}
    return _profile_from_fixtures(stats_by_fixture, team_id)


async def fetch_context(
    client: APIFootballClient,
    event: dict[str, Any],
    settings: Settings | None = None,
    cache: dict[str, Any] | None = None,
    now: datetime | None = None,
    geo_client: WeatherClient | None = None,
) -> ContextReport:
    """Recupere et persiste tout le contexte disponible pour un evenement.

    `cache` memorise classements et statistiques d'equipe le temps d'un
    enrichissement : deux matchs de la meme ligue ne paient pas deux fois.
    Aucune erreur n'est propagee : ce qui manque est simplement absent du
    rapport, donc rendu comme « donnee non disponible ».

    `now` ne sert qu'a la composition, seule donnee dont la disponibilite depend
    de l'heure : elle n'est publiee qu'a l'approche du coup d'envoi.

    `geo_client` situe la ville d'un stade que le fournisseur n'identifie pas.
    Absent, tout le reste marche comme avant et la ligne `Lieu` se rend sans
    pays — c'est un complement gratuit, jamais une dependance.
    """
    settings = settings or get_settings()
    cache = cache if cache is not None else {}
    report = ContextReport(event_id=int(event["id"]), label=f"{event['home']} – {event['away']}")

    if not event.get("apifootball_league_id"):
        # **Sans identifiant de ligue il n'y a rien a demander, et l'appel partait
        # quand meme.** `/leagues` sans `id` rend une erreur applicative en HTTP
        # 200 — « id: The Id field cannot be empty » — donc un credit depense pour
        # un message qui decrit le fournisseur au lieu de decrire le manque.
        # L'enrichissement d'une session, lui, se gardait deja par
        # `context_possible` : c'est le bouton d'un match seul qui tombait
        # dedans, et le rapport disait « rien n'a pu etre recupere » sans dire
        # que la cause se corrige en une saisie.
        report.errors.append(
            "competition non rattachee a une ligue API-Football : aucun contexte n'est "
            "possible, et rien n'a ete appele. Le rattachement se saisit depuis /competitions."
        )
        return report

    try:
        mapping = await resolve_fixture(client, event, settings, cache)
    except ProviderError as exc:
        report.errors.append(str(exc))
        return report

    if mapping is None:
        report.mapping_pending = True
        return report

    async def _memo(key: str, coroutine_factory: Any) -> Any:
        return await _memoized(cache, key, coroutine_factory)

    # Classement — partage par toutes les rencontres de la ligue.
    try:
        if not mapping.coverage.get("standings", True):
            # Le fournisseur annonce qu'il n'a pas de classement ici : ne pas
            # l'appeler, et surtout ne pas laisser la ligne disparaitre en
            # silence — une absence declaree est une information.
            store(report.event_id, KIND_STANDINGS, {"available": False}, settings)
            report.kinds.append(KIND_STANDINGS)
            raise _NotCovered
        standings = await _memo(
            f"standings:{mapping.league_id}:{mapping.season}",
            lambda: client.standings(mapping.league_id, mapping.season),
        )
        payload = {
            "home": _standings_entry(standings, mapping.home_id),
            "away": _standings_entry(standings, mapping.away_id),
        }
        if payload["home"] or payload["away"]:
            store(report.event_id, KIND_STANDINGS, payload, settings)
            report.kinds.append(KIND_STANDINGS)
    except _NotCovered:
        pass
    except ProviderError as exc:
        report.errors.append(f"classement : {exc}")

    # Forme et repartition domicile / exterieur.
    try:
        stats = {}
        for side, team_id in (("home", mapping.home_id), ("away", mapping.away_id)):
            stats[side] = await _memo(
                f"stats:{mapping.league_id}:{mapping.season}:{team_id}",
                lambda tid=team_id: client.team_statistics(mapping.league_id, mapping.season, tid),
            )
        if any(stats.values()):
            store(report.event_id, KIND_FORM, stats, settings)
            report.kinds.append(KIND_FORM)
    except ProviderError as exc:
        report.errors.append(f"forme : {exc}")

    # Derniers matchs : buts sur la periode recente et jours de repos.
    try:
        recent = {}
        for side, team_id in (("home", mapping.home_id), ("away", mapping.away_id)):
            fixtures = await _memo(
                f"recent:{team_id}", lambda tid=team_id: client.last_fixtures(tid, RECENT_LAST)
            )
            recent[side] = _recent_summary(fixtures, team_id)
        store(report.event_id, KIND_RECENT, recent, settings)
        report.kinds.append(KIND_RECENT)
    except ProviderError as exc:
        report.errors.append(f"matchs recents : {exc}")

    # Lieu : une rencontre delocalisee ou un terrain synthetique changent la
    # lecture d'un match, et rien dans le bloc ne les laissait deviner. Quatre
    # « domiciles » d'une soiree de qualifications europeennes se jouaient
    # ailleurs — Kyiv a Lublin, Beitar a Ploiesti, Hapoel a Miskolc.
    try:
        home_team = await _memo(f"team:{mapping.home_id}", lambda: client.team(mapping.home_id))
        usual = ((home_team or {}).get("venue")) or {}
        payload = {
            "venue_id": mapping.venue.get("id"),
            "name": mapping.venue.get("name"),
            "city": mapping.venue.get("city"),
            "usual_id": usual.get("id"),
            "usual_name": usual.get("name"),
            "usual_city": usual.get("city"),
            "surface": usual.get("surface"),
            # Pays du club qui recoit, tel que le fournisseur le declare. C'est
            # a lui que le pays du stade se compare : un club dont la federation
            # n'est pas celle du lieu ne recoit pas, il est heberge.
            "home_country": ((home_team or {}).get("team") or {}).get("country"),
        }
        # **Le pays du stade ne se demande que sur une rencontre deplacee.** Le
        # stade habituel n'a pas besoin d'etre situe — on sait deja que le club
        # y est chez lui — et l'appel coute une requete par match sinon.
        if _moved_venue(payload):
            lieu = await _memo(
                f"venue:{payload['venue_id']}", lambda: client.venue(int(payload["venue_id"]))
            )
            payload["country"] = (lieu or {}).get("country")
        elif (
            geo_client is not None
            and payload["city"]
            and venue_state(payload) == VENUE_UNIDENTIFIED
        ):
            # **Le geocodage prend le relais la ou l'identifiant manque**, donc
            # sur les competitions UEFA — 210 matchs sur 210 sans identifiant de
            # stade, exactement la ou les delocalisations arrivent. Gratuit, sans
            # cle, memorise par ville : un lot de vingt matchs paie au plus vingt
            # appels a un service qui n'a pas de quota.
            try:
                payload["geo_country"] = await _memo(
                    f"geo:{sort_key(payload['city'])}",
                    lambda: _geocoded_country(
                        geo_client, str(payload["city"]), payload["home_country"]
                    ),
                )
            except ProviderError as exc:
                # Le geocodeur est un bonus : injoignable, il ne doit pas emporter
                # le nom du stade, qui est deja la et se lit tres bien seul.
                report.errors.append(f"pays du lieu : {exc}")
        if payload["city"] or payload["surface"]:
            store(report.event_id, KIND_VENUE, payload, settings)
            report.kinds.append(KIND_VENUE)
    except ProviderError as exc:
        report.errors.append(f"lieu : {exc}")

    # L'arbitre : **aucun appel**, le nom vient du match deja resolu. Il ne
    # devient une ligne que parce qu'un marche Cartons est servi sur une partie
    # des blocs sans que rien ne permette de le lire — et parce que chercher
    # « qui arbitre X - Y » coutait une requete avant meme de chercher son
    # historique.
    store(report.event_id, KIND_REFEREE, {"name": mapping.referee}, settings)
    report.kinds.append(KIND_REFEREE)

    # Profil corners et cartons, sur les memes matchs recents. Le prompt
    # proposait des lignes de corners sans rien savoir de ce qu'une equipe en
    # produit ou en concede : le marche etait rendu, l'angle sportif absent.
    try:
        if not _covers_fixture_statistics(mapping.coverage):
            # Meme regle que le classement et les absents, appliquee au sous-objet
            # `coverage.fixtures`. Sans elle, jusqu'a dix appels par match sont
            # payes pour rien : la Primeira Liga 2026 annonce
            # `statistics_fixtures: false`, chaque `/fixtures/statistics` revient
            # vide, et les trois lignes du profil disparaissent sans un mot.
            store(report.event_id, KIND_PROFILE, {"available": False}, settings)
            report.kinds.append(KIND_PROFILE)
            raise _NotCovered
        profile = {}
        for side, team_id in (("home", mapping.home_id), ("away", mapping.away_id)):
            fixtures = await _memo(
                f"recent:{team_id}", lambda tid=team_id: client.last_fixtures(tid, RECENT_LAST)
            )
            profile[side] = await _fetch_profile(client, cache, fixtures, team_id)
        if any(profile.values()):
            store(report.event_id, KIND_PROFILE, profile, settings)
            report.kinds.append(KIND_PROFILE)
    except _NotCovered:
        pass
    except ProviderError as exc:
        report.errors.append(f"profil corners/cartons : {exc}")

    # Compositions — la seule donnee dont la disponibilite depend de l'heure.
    try:
        if not _covers_lineups(mapping.coverage) or not _lineup_due(
            event.get("commence_time"), now
        ):
            # Hors fenetre, aucune ligne et **aucune mention** : contrairement
            # aux absents, une composition qui manque a trois heures du match ne
            # dit rien de l'equipe. L'annoncer « non disponible » ferait chercher
            # un trou de collecte la ou il n'y a qu'une heure trop tot.
            raise _NotCovered
        payload = _lineup_payload(
            await client.lineups(mapping.fixture_id), mapping.home_id, mapping.away_id
        )
        # Une reponse vide n'est **pas** persistee : les compositions sortent au
        # compte-gouttes, et figer « rien » empecherait un second essai dix
        # minutes plus tard de rapporter quelque chose.
        if payload:
            store(report.event_id, KIND_LINEUPS, payload, settings)
            report.kinds.append(KIND_LINEUPS)
    except _NotCovered:
        pass
    except ProviderError as exc:
        report.errors.append(f"compositions : {exc}")

    # Blesses et suspendus — couverture irreguliere selon les ligues.
    try:
        if not mapping.coverage.get("injuries", True):
            # Sans ce test, une liste vide se rendait « aucun signale » — soit
            # l'affirmation inverse de la verite. Constate en reel sur les
            # qualifications europeennes, ou six absents annonces par la presse
            # etaient rendus « aucun signale » des deux cotes.
            store(
                report.event_id,
                KIND_INJURIES,
                {"available": False, "state": INJURIES_NOT_ASKED},
                settings,
            )
            report.kinds.append(KIND_INJURIES)
            raise _NotCovered
        rows = await client.injuries(mapping.fixture_id)
        payload = {"available": True, "state": INJURIES_SERVED, "home": [], "away": []}
        # Le fournisseur renvoie chaque joueur deux fois — constate en reel :
        # 14 lignes pour 7 absents. Sans dedoublonnage la ligne « Absents »
        # liste tout le monde en double, ce qui fait douter de la donnee entiere.
        seen: set[tuple[Any, ...]] = set()
        for row in rows:
            team_id = (row.get("team") or {}).get("id")
            side = (
                "home"
                if team_id == mapping.home_id
                else "away"
                if team_id == mapping.away_id
                else None
            )
            if side is None:
                continue
            player = row.get("player") or {}
            entry = {
                "name": player.get("name"),
                "reason": player.get("reason"),
                "type": player.get("type"),
            }
            marker = (side, entry["name"], entry["type"], entry["reason"])
            if marker in seen:
                continue
            seen.add(marker)
            payload[side].append(entry)
        store(report.event_id, KIND_INJURIES, payload, settings)
        report.kinds.append(KIND_INJURIES)
    except _NotCovered:
        pass
    except ProviderError as exc:
        # **Injoignable n'est pas non couvert.** La premiere se retente au
        # prochain enrichissement, la seconde ne se retentera jamais : les
        # confondre faisait chercher un reglage la ou il n'y avait qu'un incident.
        store(
            report.event_id,
            KIND_INJURIES,
            {"available": False, "state": INJURIES_UNREACHABLE},
            settings,
        )
        report.errors.append(f"absents : {exc}")

    # Effectif recent, **uniquement** la ou les absents ne sont pas couverts.
    # La ou `/injuries` repond, il dit mieux et gratuitement : ce bloc est un
    # substitut, jamais un doublon. Les identifiants des derniers matchs sont
    # deja en main — `recent:{team_id}` a ete memorise plus haut pour la forme —
    # donc seules les feuilles se paient.
    try:
        if mapping.coverage.get("injuries", True) or not mapping.coverage.get("fixtures", {}).get(
            "lineups", True
        ):
            raise _NotCovered
        payload: dict[str, Any] = {"available": True, "home": [], "away": []}
        for side, team_id in (("home", mapping.home_id), ("away", mapping.away_id)):
            fixtures = await _memo(
                f"recent:{team_id}", lambda tid=team_id: client.last_fixtures(tid, RECENT_LAST)
            )
            sheets = []
            for fixture in _latest_played(fixtures, SHEETS_LAST):
                fixture_id = (fixture.get("fixture") or {}).get("id")
                if fixture_id is None:
                    continue
                rows = await _memo(
                    f"lineups:{fixture_id}", lambda fid=fixture_id: client.lineups(fid)
                )
                names = _sheet_names(rows, team_id)
                if names:
                    sheets.append(((fixture.get("fixture") or {}).get("date"), names))
            payload[side] = _missing_players(sheets)
            # **Les bornes de ce qui a ete lu**, et non de ce qui a ete demande :
            # une feuille que le fournisseur n'a pas encore publiee est sautee,
            # si bien que la fenetre reelle est plus courte que `SHEETS_LAST`.
            # Sans elles, « plus vu depuis le 23/07 » ne dit pas sur quoi il
            # repose — c'est ce qui a rendu un faux positif indetectable.
            payload[f"{side}_window"] = _window_of(sheets)
        store(report.event_id, KIND_SHEETS, payload, settings)
        report.kinds.append(KIND_SHEETS)
    except _NotCovered:
        pass
    except ProviderError as exc:
        report.errors.append(f"effectif recent : {exc}")

    # Confrontations directes.
    try:
        h2h = await client.head_to_head(mapping.home_id, mapping.away_id, H2H_LAST)
        matches = []
        for fixture in h2h:
            teams = fixture.get("teams") or {}
            goals = fixture.get("goals") or {}
            if goals.get("home") is None or goals.get("away") is None:
                continue
            matches.append(
                {
                    "home_id": (teams.get("home") or {}).get("id"),
                    "home_goals": goals.get("home"),
                    "away_goals": goals.get("away"),
                    "date": (fixture.get("fixture") or {}).get("date"),
                    # La competition ne servait a rien tant qu'on ne rendait que
                    # la suite des scores. Elle est ce qui distingue un match
                    # aller d'une rencontre de championnat d'il y a deux ans.
                    "league_id": (fixture.get("league") or {}).get("id"),
                    # Le stade de l'aller, **dans le meme appel**. Il portait le
                    # fait decisif d'une manche retour reelle : Dynamo Kyiv avait
                    # recu a Lublin, si bien que personne n'avait joue « a
                    # l'exterieur » et que le scenario se lisait de travers.
                    "venue": (fixture.get("fixture") or {}).get("venue") or {},
                }
            )
        store(report.event_id, KIND_H2H, {"home_id": mapping.home_id, "matches": matches}, settings)
        report.kinds.append(KIND_H2H)
    except ProviderError as exc:
        report.errors.append(f"h2h : {exc}")

    return report


# -- Rendu ------------------------------------------------------------------


def _form_letters(form: str | None) -> str:
    if not form:
        return ""
    return "".join(FORM_LETTERS.get(letter, letter) for letter in form[-FORM_LENGTH:])


def _side_record(stats: dict[str, Any] | None, side: str) -> str:
    """`dom 6V-1N-1D 2.1 bpm/8j` a partir des statistiques de la competition.

    Rien n'est rendu tant qu'aucun match n'a ete joue : le fournisseur repond
    alors `0V-0N-0D` et une moyenne de `0.0`, indiscernables d'une equipe qui
    ne gagne ni ne marque. En debut de saison — ou sur une competition ou
    l'equipe entre en qualification — c'est le cas de tout le monde.

    Le nombre de matchs accompagne la ligne pour la meme raison que le compte
    accompagne un taux : `0.0 bpm` sur deux matchs et sur vingt ne disent pas
    la meme chose, et la statistique porte sur cette competition-la, pas sur
    toute la saison de l'equipe.
    """
    if not stats:
        return ""
    fixtures = stats.get("fixtures") or {}
    played = (fixtures.get("played") or {}).get(side)
    if not played:
        return ""
    wins = (fixtures.get("wins") or {}).get(side)
    draws = (fixtures.get("draws") or {}).get(side)
    loses = (fixtures.get("loses") or {}).get(side)
    average = (((stats.get("goals") or {}).get("for") or {}).get("average") or {}).get(side)
    label = "dom" if side == "home" else "ext"
    record = f"{label} {wins or 0}V-{draws or 0}N-{loses or 0}D"
    if average:
        record = f"{record} {average} bpm"
    return f"{record}/{played}j"


def _pair(home: str, away: str) -> str:
    """Assemble deux fragments cote a cote, en omettant celui qui manque."""
    parts = [part for part in (home, away) if part]
    return " | ".join(parts)


def _lineup_fragment(team: str, entry: dict[str, Any] | None) -> str:
    """`Beijing FC (4-4-2) Hou Sen, Bai, …`, ou rien si le onze manque.

    La formation seule ne suffit pas : `Formations` la donne deja pour la
    saison, et c'est justement l'ecart entre l'habitude et le jour meme qui se
    lit ici. Le banc n'y figure pas — voir la collecte.
    """
    if not entry:
        return ""
    starters = entry.get("starters") or []
    if not starters:
        return ""
    formation = entry.get("formation")
    head = f"{team} ({formation})" if formation else team
    return f"{head} {', '.join(starters)}"


# -- Statistiques de saison : deja payees, longtemps jetees -------------------
#
# `/teams/statistics` est appele a chaque enrichissement et sa charge utile est
# persistee entiere. Seuls `form` et le bilan dom/ext en etaient tires : le
# reste — distribution des buts par match, clean sheets, tranches horaires,
# cartons, formations — dormait en base alors que les marches correspondants
# etaient achetes. Ces lignes ne coutent pas un appel.
#
# Toutes rendent des **fractions** et jamais des pourcentages. Une frequence
# observee decrit le passe, ce qui reste permis ; ecrite « 56 % », elle invite a
# la diviser par une cote, et c'est le calcul d'esperance qu'interdit la
# section 9. « 9/16 » porte la meme information et le meme compte que les
# moyennes du profil.


def _played_total(stats: dict[str, Any] | None) -> int:
    """Matchs joues dans la competition, seul denominateur legitime ici."""
    if not stats:
        return 0
    played = ((stats.get("fixtures") or {}).get("played") or {}).get("total")
    return int(played or 0)


def _season_ready(stats: dict[str, Any] | None) -> int:
    """Nombre de matchs joues s'il autorise une ligne de saison, sinon 0."""
    played = _played_total(stats)
    return played if played >= SEASON_MIN_MATCHES else 0


def _band_total(block: dict[str, Any] | None, band: str) -> int:
    """Total d'une tranche horaire. `null` vaut zero, jamais une erreur."""
    if not isinstance(block, dict):
        return 0
    return int(((block.get(band) or {}).get("total")) or 0)


def _bands_total(block: dict[str, Any] | None, bands: tuple[str, ...]) -> int:
    return sum(_band_total(block, band) for band in bands)


def _all_bands_total(block: dict[str, Any] | None) -> int:
    """Total toutes tranches confondues, y compris celle de libelle vide.

    Cette tranche existe : un carton dont la minute n'est pas connue. L'omettre
    du denominateur ferait passer 26 cartons tardifs sur 38 pour 26 sur 37.
    """
    if not isinstance(block, dict):
        return 0
    return sum(int((entry or {}).get("total") or 0) for entry in block.values())


def _team_goals_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken >0.5 14/16 >1.5 9/16 >2.5 4/16` — buts de l'equipe par match.

    Attention au sens : ce sont les buts **de cette equipe**, pas le total du
    match. La distribution sert donc `team_totals`, et deux distributions
    d'equipes ne se somment pas en un total de match — celui-la viendra de
    l'historique des rencontres, pas d'ici.
    """
    played = _season_ready(stats)
    if not played:
        return ""
    return _under_over_fragment(team, stats, "for", played)


def _conceded_goals_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """Le miroir exact de « Buts marq. », et il dormait dans la meme charge utile.

    `goals.for.under_over` etait lu, `goals.against.under_over` jamais : on
    savait dans combien de matchs une equipe avait marque deux buts, pas dans
    combien elle en avait encaisse deux. C'est pourtant l'autre moitie d'un
    total de rencontre, et la seule qui decrive une defense.
    """
    played = _season_ready(stats)
    if not played:
        return ""
    return _under_over_fragment(team, stats, "against", played)


def _under_over_fragment(team: str, stats: dict[str, Any] | None, side: str, played: int) -> str:
    """Fractions de matchs passant chaque ligne de buts, d'un cote ou de l'autre.

    Ecrite une seule fois : les deux cotes se lisent au meme endroit de la
    charge utile, et deux copies auraient diverge au premier seuil ajoute.
    """
    under_over = ((stats or {}).get("goals") or {}).get(side) or {}
    lines = under_over.get("under_over")
    if not isinstance(lines, dict):
        return ""
    fragments = []
    for key in TEAM_TOTAL_LINES:
        entry = lines.get(key)
        if not isinstance(entry, dict) or entry.get("over") is None:
            continue
        fragments.append(f">{key} {entry['over']}/{played}")
    return f"{team} {' '.join(fragments)}" if fragments else ""


def _clean_sheet_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken 5 CS, 2 sans marquer/16` — l'angle des deux equipes marquent."""
    played = _season_ready(stats)
    if not played:
        return ""
    clean = ((stats or {}).get("clean_sheet") or {}).get("total")
    failed = ((stats or {}).get("failed_to_score") or {}).get("total")
    if clean is None and failed is None:
        return ""
    parts = []
    if clean is not None:
        parts.append(f"{clean} CS")
    if failed is not None:
        parts.append(f"{failed} sans marquer")
    return f"{team} {', '.join(parts)}/{played}"


def _half_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken 12/28 marq. 8/20 pris` — part des buts tombes en 1re mi-temps.

    Le denominateur est le nombre de buts, pas le nombre de matchs : c'est une
    repartition dans le temps. Une equipe qui n'a ni marque ni encaisse ne
    produit aucune ligne — `0/0` ne dit rien.
    """
    if not _season_ready(stats):
        return ""
    goals = (stats or {}).get("goals") or {}
    parts = []
    for side, label in (("for", "marq."), ("against", "pris")):
        block = (goals.get(side) or {}).get("minute")
        total = _all_bands_total(block)
        if not total:
            continue
        parts.append(f"{_bands_total(block, FIRST_HALF_BANDS)}/{total} {label}")
    return f"{team} {' '.join(parts)}" if parts else ""


def _late_goals_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken 5/28 marq. 9/20 pris apres 75e` — la fin de match.

    Miroir tardif de « 1re MT », dans la meme charge utile et pour aucun appel
    de plus : `_half_fragment` lisait `goals.*.minute` et n'en prenait que les
    trois premieres tranches. C'est le **seul signal de maniere** du bloc
    football — `xG` et `Tirs` disent le volume produit, jamais le moment ou il
    tombe — et c'est ce que la section B reclame pour sortir du 1N2.

    Mesure sur la base : KFUM Oslo n'a rien marque apres la 75e en dix-neuf
    buts quand SJK en met huit sur vingt-trois, et Sichuan Jiuniu encaisse
    seize de ses trente-neuf buts dans ce quart d'heure.
    """
    if not _season_ready(stats):
        return ""
    goals = (stats or {}).get("goals") or {}
    parts = []
    for side, label in (("for", "marq."), ("against", "pris")):
        block = (goals.get(side) or {}).get("minute")
        total = _all_bands_total(block)
        if not total:
            continue
        parts.append(f"{_bands_total(block, LATE_GOAL_BANDS)}/{total} {label}")
    return f"{team} {' '.join(parts)} apres 75e" if parts else ""


def _card_timing_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken 19/34 apres 60e` — les cartons tardifs sont un angle a eux.

    Complete la ligne « Cartons », qui donne une moyenne par match sur les cinq
    derniers : celle-ci dit *quand* ils tombent, sur toute la competition.
    """
    if not _season_ready(stats):
        return ""
    yellow = ((stats or {}).get("cards") or {}).get("yellow")
    total = _all_bands_total(yellow)
    if not total:
        return ""
    return f"{team} {_bands_total(yellow, LATE_BANDS)}/{total} apres 60e"


def _formation_fragment(team: str, stats: dict[str, Any] | None) -> str:
    """`BK Hacken 4-2-3-1 (11), 4-3-3 (5)/16` — le compte dit la stabilite.

    Sans lui, « 4-3-3 » se lirait comme la formation habituelle alors qu'elle
    peut ne couvrir que trois matchs sur quinze : c'est alors un effectif
    tournant, ce qui est l'information inverse.
    """
    played = _season_ready(stats)
    if not played:
        return ""
    lineups = (stats or {}).get("lineups")
    if not isinstance(lineups, list) or not lineups:
        return ""
    ranked = sorted(
        (item for item in lineups if isinstance(item, dict) and item.get("formation")),
        key=lambda item: int(item.get("played") or 0),
        reverse=True,
    )[:FORMATIONS_KEEP]
    if not ranked:
        return ""
    listed = ", ".join(f"{item['formation']} ({int(item.get('played') or 0)})" for item in ranked)
    return f"{team} {listed}/{played}"


def _injuries_for(entries: list[dict[str, Any]], team: str) -> str:
    if not entries:
        return f"{team} — aucun signale"
    listed = []
    for entry in entries:
        name = entry.get("name") or "?"
        detail = ", ".join(str(item) for item in (entry.get("type"), entry.get("reason")) if item)
        listed.append(f"{name} ({detail})" if detail else name)
    return f"{team} — {', '.join(listed)}"


def _sheets_for(
    entries: list[dict[str, Any]], team: str, window: dict[str, Any] | None = None
) -> str:
    """`Cracovia — Knap plus vu depuis le 26/07, Baumgartner depuis le 02/08`.

    Rien quand personne ne manque : ecrire « aucun » affirmerait un effectif au
    complet, ce que des feuilles de match ne peuvent pas prouver — un joueur
    peut n'avoir jamais figure sur la fenetre lue.
    """
    listed = []
    for index, entry in enumerate(entries):
        date = _moment(str(entry.get("last") or ""))
        if date is None or not entry.get("name"):
            continue
        # « plus vu » une fois par equipe : sur la seconde, la date suffit.
        prefixe = "plus vu depuis le" if not listed and index == 0 else "depuis le"
        listed.append(f"{entry['name']} {prefixe} {date.strftime('%d/%m')}")
    if not listed:
        return ""
    return f"{team} — {', '.join(listed)}{_window_note(window)}"


def _window_note(window: dict[str, Any] | None) -> str:
    """` (fenetre lue : 3 feuilles, du 23/07 au 06/08, toutes competitions)`.

    « Toutes competitions » est ecrit parce que c'est vrai et parce que ca a ete
    doute : la fenetre sort de `/fixtures?team=&last=`, qui ne filtre sur aucune
    competition — verifie le 12/08/2026, une coupe nationale et une coupe
    d'Europe figuraient dans la meme fenetre.
    """
    if not window or not window.get("first"):
        return ""
    debut, fin = _moment(str(window["first"])), _moment(str(window["last"]))
    if debut is None or fin is None:
        return ""
    feuilles = window.get("count") or 0
    return (
        f" (fenetre lue : {feuilles} feuille(s), du {debut.strftime('%d/%m')} "
        f"au {fin.strftime('%d/%m')}, toutes competitions)"
    )


def _h2h_line(payload: dict[str, Any], settings: Settings) -> str:
    """`1-1 · 0-2 D · 2-2`, toujours du point de vue de l'equipe a domicile."""
    matches = payload.get("matches") or []
    home_id = payload.get("home_id")
    fragments = []
    for match in sorted(matches, key=lambda item: item.get("date") or "", reverse=True):
        if match.get("home_id") == home_id:
            ours, theirs = match["home_goals"], match["away_goals"]
        else:
            ours, theirs = match["away_goals"], match["home_goals"]
        marker = " V" if ours > theirs else " D" if ours < theirs else ""
        fragments.append(f"{ours}-{theirs}{marker}")
    return " · ".join(fragments)


#: Au-dela, deux rencontres entre les memes equipes ne forment plus une double
#: confrontation. Trois semaines couvrent large : un tour europeen se joue a
#: sept jours, un huitieme de finale a quatorze ou vingt et un.
RETURN_LEG_DAYS = 21


def _return_leg(
    payload: dict[str, Any], league_id: Any, commence_time: str
) -> dict[str, Any] | None:
    """L'aller d'une double confrontation, ou `None`.

    **Ecrite une seule fois**, parce que deux lignes en dependent : le fait
    (`Aller`) et son arithmetique (`Scenario`). Deux detections paralleles
    auraient fini par diverger, et le bloc aurait annonce un scenario sur une
    rencontre que l'autre ligne ne reconnaissait plus comme un aller.

    Trois conditions, et il faut les trois : **meme competition**, **terrain
    inverse** — celui qui recoit aujourd'hui se deplacait — et moins de
    `RETURN_LEG_DAYS` jours. Le terrain inverse est le discriminant fort ; sans
    lui, deux journees de championnat rapprochees passeraient pour une double
    confrontation.
    """
    matches = payload.get("matches") or []
    home_id = payload.get("home_id")
    if not matches or home_id is None or league_id is None:
        return None
    start = _moment(commence_time)
    recent = max(matches, key=lambda item: str(item.get("date") or ""))
    played = _moment(str(recent.get("date") or ""))
    if start is None or played is None:
        return None
    if recent.get("league_id") != league_id or recent.get("home_id") == home_id:
        return None
    if not 0 <= (start - played).days <= RETURN_LEG_DAYS:
        return None
    return dict(recent)


@dataclass(frozen=True)
class TieState:
    """L'etat d'une double confrontation avant la manche retour.

    Ecrit une fois et lu deux fois : la ligne `Scenario` le rend, et la fiche de
    recherche s'en sert pour classer les dossiers — un tie a un but d'ecart est
    ce qu'une recherche peut le plus changer, un tie a trois est mort. Deux
    calculs paralleles auraient fini par ne plus dire la meme chose du meme
    match.
    """

    #: Buts de l'equipe qui recoit aujourd'hui, puis de celle qui se deplace.
    #: Meme convention que `Aller` et `H2H`.
    home_goals: int
    away_goals: int

    @property
    def gap(self) -> int:
        """Ecart au cumul, toujours positif. Zero = rien n'est fait."""
        return abs(self.home_goals - self.away_goals)

    @property
    def trailing_at_home(self) -> bool:
        """Vrai quand l'equipe **menee** recoit la manche retour.

        C'est la configuration ou l'obligation est la plus exploitable : celui
        qui doit marquer a le terrain, donc il s'ouvrira.
        """
        return self.home_goals < self.away_goals


def tie_state(
    event_id: int, commence_time: str, settings: Settings | None = None
) -> TieState | None:
    """L'etat de la double confrontation de cet evenement, ou `None`.

    Relu en base, sans aucun appel : `fetch_context` a persiste la charge utile
    du `/fixtures/headtohead`, et la manche aller y figure.
    """
    settings = settings or get_settings()
    data = load(event_id, settings)
    h2h = data.get(KIND_H2H)
    if not h2h:
        return None
    league = (data.get(KIND_TEAMS) or {}).get("league")
    aller = _return_leg(h2h, league, commence_time)
    if aller is None:
        return None
    pour, contre = aller.get("away_goals"), aller.get("home_goals")
    if not isinstance(pour, int) or not isinstance(contre, int):
        return None
    return TieState(home_goals=pour, away_goals=contre)


def _scenario_line(
    payload: dict[str, Any],
    league_id: Any,
    home: str,
    away: str,
    when: str,
    neutral: bool = False,
) -> str:
    """`cumul 0-2 — Hapoel qualifie en l'etat ; GKS doit gagner de 2…`

    **C'est un calcul, et un calcul ne se delegue pas au modele.** Sur une
    semaine de tours preliminaires, vingt-quatre manches retour ont demande le
    meme raisonnement refait a la main : cumul, qui mene, combien il faut a
    celui qui est mene. Il est deterministe et tient en trois soustractions.

    **Deux seuils, et il faut les deux.** Egaliser envoie en prolongation,
    passer gagne le tour dans le temps reglementaire : les deux ne produisent
    pas la meme fin de match, et c'est le second qui decide si l'equipe s'ouvre
    encore a la 80e. Un cumul seul laisse ce travail a faire.

    **Le camp oblige est nomme, et c'est le mot « doit » qui declenche l'angle**
    — une obligation de marquer se traduit en total, en handicap ou en marche
    d'equipe, la ou un cumul ne se traduit en rien. On dit aussi lequel des deux
    recoit : une obligation a domicile et la meme a l'exterieur ne produisent
    pas le meme scenario.

    Ce que la ligne **ne dit pas** : la regle des buts a l'exterieur, la
    prolongation, les tirs au but. Ce sont des regles de competition, pas de
    l'arithmetique — le preambule les enonce une fois pour le lot, et la fiche
    de la competition prime sur lui. Les affirmer par match, c'est se porter
    garant d'un reglement qu'on n'a pas lu.
    """
    aller = _return_leg(payload, league_id, when)
    if aller is None:
        return ""
    # Meme convention que « Aller » et « H2H » : du point de vue de l'equipe qui
    # recoit **aujourd'hui**, laquelle se deplacait a l'aller.
    pour, contre = aller.get("away_goals"), aller.get("home_goals")
    if not isinstance(pour, int) or not isinstance(contre, int):
        return ""

    cumul = f"cumul {pour}-{contre}"
    if pour == contre:
        # Rien n'est fait : sans regle des buts a l'exterieur, le vainqueur du
        # match passe et un nul prolonge. C'est le cas ou la lecture se trompe
        # le plus souvent, parce qu'un 2-2 a l'aller **n'avantage personne**.
        return f"{cumul} — rien n'est fait, le vainqueur de ce match passe"

    menant, mene = (home, away) if pour > contre else (away, home)
    ecart = abs(pour - contre)
    lieu = "a domicile" if mene == home else "a l'exterieur"
    # **« a domicile » suppose un avantage, et cette supposition se verifie.**
    # Sur un lot reel de manches retour, trois auraient rendu la mention fausse :
    # Vitebsk « recevait » en Hongrie, Minsk en Bulgarie. Le mot inverse alors le
    # sens de la phrase, et c'est le drapeau de terrain neutre qui le rattrape.
    if mene == home and neutral:
        lieu = "nominalement a domicile, terrain neutre"
    return (
        f"{cumul} — {menant} qualifie en l'etat ; "
        f"{mene} ({lieu}) doit gagner de {ecart} pour egaliser, de {ecart + 1} pour passer"
    )


def _return_leg_line(payload: dict[str, Any], league_id: Any, away: str, commence_time: str) -> str:
    """`0-0 le 06/08, Hammarby recevait` — l'aller d'une double confrontation.

    La fiche de verification appelle ca **« le premier determinant du
    scenario »** et rien ne le servait : le resume H2H gardait les scores et
    jetait la competition, si bien qu'on ne pouvait pas distinguer l'aller du
    tour en cours d'un match de championnat d'il y a deux ans. Le champ est
    garde depuis, sans un appel de plus — c'est le meme `/fixtures/headtohead`.

    Trois conditions, et il faut les trois : **meme competition**, **terrain
    inverse** — celui qui recoit aujourd'hui se deplacait — et moins de
    `RETURN_LEG_DAYS` jours. Le terrain inverse est le discriminant fort ; sans
    lui, deux journees de championnat rapprochees passeraient pour une double
    confrontation.

    La ligne **enonce un fait et s'arrete la** : ces deux equipes se sont
    rencontrees tel jour, chez l'autre, dans cette competition. Qu'il s'agisse
    d'une double confrontation est une deduction, tres sure sur un tour europeen
    et moins ailleurs — c'est au lecteur de la faire.

    Un releve d'avant ce champ n'a pas de `league_id` : aucune ligne, jusqu'au
    prochain enrichissement.
    """
    recent = _return_leg(payload, league_id, commence_time)
    played = _moment(str((recent or {}).get("date") or ""))
    if recent is None or played is None:
        return ""
    # Le score se lit du point de vue de l'equipe qui recoit aujourd'hui, comme
    # la ligne H2H : deux conventions dans le meme bloc se liraient a l'envers.
    ours, theirs = recent["away_goals"], recent["home_goals"]
    ligne = f"{ours}-{theirs} le {played.strftime('%d/%m')}, {away} recevait"
    return ligne + _venue_suffix(recent.get("venue") or {})


def _venue_suffix(venue: dict[str, Any]) -> str:
    """` a l'Arena Lublin, Lublin` — ou rien, quand le stade n'est pas nomme.

    **Le fait brut, et rien de plus.** Dire si ce stade etait neutre demanderait
    le stade habituel de l'equipe qui recevait ce jour-la, donc un appel de plus
    par match : arbitrage rendu, et c'est non. Le nom du lieu suffit a faire
    sauter l'anomalie aux yeux — un club ukrainien qui « recoit » a Lublin se
    voit sans qu'aucun drapeau soit calcule.

    C'est la meme logique que la requete de recherche de la fiche : quand
    l'application ne peut pas conclure a cout raisonnable, elle expose le fait et
    laisse conclure. Ce qu'elle ne peut pas faire, c'est deviner un stade qui
    n'est pas ecrit.
    """
    nom = (venue.get("name") or "").strip()
    ville = (venue.get("city") or "").strip()
    lieu = ", ".join(part for part in (nom, ville) if part)
    return f" a {lieu}" if lieu else ""


def _moment(value: str) -> datetime | None:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None


def _rest_days(summary: dict[str, Any] | None, commence_time: str) -> str:
    if not summary or not summary.get("last_date"):
        return ""
    try:
        last = datetime.fromisoformat(str(summary["last_date"]).replace("Z", "+00:00"))
        start = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except ValueError:
        return ""
    if last.tzinfo is None:
        last = last.replace(tzinfo=UTC)
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    # Ecart en jours calendaires : un match le 28 au soir et un match le 3 en
    # debut d'apres-midi font « 6j » de repos, pas 5 tranches de 24 heures.
    days = (start.date() - last.date()).days
    return f"{days}j" if days >= 0 else ""


@dataclass
class LineupSweep:
    """Ce qu'une passe de rafraichissement des compositions a fait."""

    checked: int = 0
    fetched: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)


async def refresh_due_lineups(
    client: APIFootballClient,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> LineupSweep:
    """Recupere les compositions des matchs dont le coup d'envoi approche.

    **Ciblee, pas un contexte complet.** `fetch_context` fait une dizaine
    d'appels ; ici il en faut un par match, et seulement pour ce qui manque.
    Tout ce dont elle a besoin est deja en base : `apifootball_fixture_id` sur
    l'evenement, et la couverture memorisee au rapprochement (`KIND_TEAMS`).

    Le perimetre est la **shortlist** : ce sont les matchs qui iront dans un
    prompt. Un match jamais coche n'a pas besoin de sa composition, et
    l'appeler pour tout le board depenserait le quota sur des rencontres que
    personne n'analysera.

    Un match dont la composition est deja en base n'est pas redemande : elle ne
    change plus une fois publiee.
    """
    settings = settings or get_settings()
    sweep = LineupSweep()
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT e.id, e.home, e.away, e.commence_time, e.apifootball_fixture_id "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "WHERE s.key = 'football' AND e.apifootball_fixture_id IS NOT NULL "
            "  AND NOT EXISTS (SELECT 1 FROM context c "
            "                  WHERE c.event_id = e.id AND c.kind = ?) "
            "ORDER BY e.commence_time",
            (KIND_LINEUPS,),
        ).fetchall()

    for row in rows:
        if not _lineup_due(row["commence_time"], now):
            continue
        data = load(int(row["id"]), settings)
        teams = data.get(KIND_TEAMS) or {}
        if not teams or not _covers_lineups(teams.get("coverage") or {}):
            # Sans rapprochement memorise, il faudrait le refaire — donc payer
            # `/fixtures`. Ce balayage doit rester a un appel par match.
            continue
        sweep.checked += 1
        label = f"{row['home']} – {row['away']}"
        try:
            payload = _lineup_payload(
                await client.lineups(int(row["apifootball_fixture_id"])),
                teams.get("home"),
                teams.get("away"),
            )
        except ProviderError as exc:
            sweep.errors.append(f"{label} : {exc}")
            continue
        if payload:
            store(int(row["id"]), KIND_LINEUPS, payload, settings)
            sweep.fetched.append(label)
    return sweep


def context_lines(
    event_id: int,
    home: str,
    away: str,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Lignes du bloc CONTEXTE, pretes pour `render_event`.

    Une donnee absente produit une ligne omise ; une donnee dont on sait qu'elle
    n'est pas couverte produit une ligne explicite.
    """
    settings = settings or get_settings()
    data = load(event_id, settings)
    lines: list[tuple[str, str]] = []

    # Saisie manuelle : profil d'etape, startlist et references d'abord, car
    # c'est tout ce dont dispose un evenement qu'aucune API ne couvre.
    manual = data.get(KIND_MANUAL_NOTE) or {}
    for label, key in (("Profil", "profile"), ("Startlist", "startlist"), ("Infos", "notes")):
        value = (manual.get(key) or "").strip()
        if value:
            lines.append((label, value))
    if manual.get("links"):
        lines.append(("References", " ".join(manual["links"])))

    standings = data.get(KIND_STANDINGS) or {}
    if standings and not standings.get("available", True):
        lines.append(("Classement", UNAVAILABLE))
        standings = {}
    ranked = _pair(
        _rank_fragment(home, standings.get("home")), _rank_fragment(away, standings.get("away"))
    )
    if ranked:
        lines.append(("Classement", ranked))

    stakes = _pair(
        _stake_fragment(home, standings.get("home")), _stake_fragment(away, standings.get("away"))
    )
    if stakes:
        lines.append(("Enjeu", stakes))

    form = data.get(KIND_FORM) or {}
    recent = data.get(KIND_RECENT) or {}
    forms = _pair(
        _form_fragment(home, form.get("home"), recent.get("home")),
        _form_fragment(away, form.get("away"), recent.get("away")),
    )
    if forms:
        lines.append(("Forme 5", forms))

    sides = _pair(
        _prefix(home, _side_record(form.get("home"), "home")),
        _prefix(away, _side_record(form.get("away"), "away")),
    )
    if sides:
        lines.append(("Dom/Ext", sides))

    # Statistiques de saison, deja payees avec la forme. Chacune sert un marche
    # que l'etage B achete : buts d'equipe, BTTS, premiere mi-temps, cartons.
    for label, fragment in (
        ("Buts marq.", _team_goals_fragment),
        ("Buts pris", _conceded_goals_fragment),
        ("Clean sheet", _clean_sheet_fragment),
        ("1re MT", _half_fragment),
        # Meme charge utile, meme denominateur, fenetre opposee : les deux se
        # lisent ensemble, et ce qui manque entre les deux est le milieu.
        ("Buts tard.", _late_goals_fragment),
        ("Formations", _formation_fragment),
    ):
        rendered = _pair(fragment(home, form.get("home")), fragment(away, form.get("away")))
        if rendered:
            lines.append((label, rendered))

    venue = data.get(KIND_VENUE) or {}
    # Lu avant le bloc des confrontations : « Scenario » s'en sert pour ne pas
    # promettre un avantage du terrain a une equipe qui joue a l'etranger.
    neutre = venue_state(venue) == VENUE_NEUTRAL if venue else False
    if venue:
        # **Systematique, et les trois etats sont ecrits.** Ne rendre que la
        # surprise revenait a faire passer pour un domicile ordinaire un match
        # dont le lieu n'avait pas ete recupere — un domicile suppose qui n'en
        # est pas coute plus qu'une ligne de plus par bloc.
        lines.append(("Lieu", _venue_line(venue, home)))
        surface = (venue.get("surface") or "").strip().lower()
        if surface and "grass" not in surface:
            lines.append(("Pelouse", SURFACE_LABELS.get(surface, surface)))

    arbitre = data.get(KIND_REFEREE)
    if arbitre is not None:
        lines.append(("Arbitre", _referee_line(arbitre)))

    profile = data.get(KIND_PROFILE) or {}
    if profile and not profile.get("available", True):
        # Le fournisseur ne sert pas les statistiques de match sur cette
        # competition. Une seule ligne le dit, plutot que trois absences
        # (corners, cartons, tirs) qu'on chercherait a expliquer une par une.
        lines.append(("Stats match", UNAVAILABLE))
        profile = {}
    corners = _pair(
        _corner_fragment(home, profile.get("home")), _corner_fragment(away, profile.get("away"))
    )
    if corners:
        lines.append(("Corners", corners))

    cards = _pair(
        _card_fragment(home, profile.get("home")), _card_fragment(away, profile.get("away"))
    )
    if cards:
        lines.append(("Cartons", cards))

    # Juste apres la moyenne par match : meme sujet, autre grandeur.
    card_timing = _pair(
        _card_timing_fragment(home, form.get("home")),
        _card_timing_fragment(away, form.get("away")),
    )
    if card_timing:
        lines.append(("Cartons tps", card_timing))

    fouls = _pair(
        _fouls_fragment(home, profile.get("home")), _fouls_fragment(away, profile.get("away"))
    )
    if fouls:
        lines.append(("Fautes", fouls))

    shots = _pair(
        _shot_fragment(home, profile.get("home")), _shot_fragment(away, profile.get("away"))
    )
    if shots:
        lines.append(("Tirs", shots))

    xg = _pair(_xg_fragment(home, profile.get("home")), _xg_fragment(away, profile.get("away")))
    if xg:
        lines.append(("xG", xg))

    possession = _pair(
        _possession_fragment(home, profile.get("home")),
        _possession_fragment(away, profile.get("away")),
    )
    if possession:
        lines.append(("Possession", possession))

    # Avant les absents, qu'elle complete et parfois remplace : sur une
    # competition ou `injuries` est faux, la composition est la seule facon de
    # savoir qui joue.
    lineups = data.get(KIND_LINEUPS) or {}
    composed = _pair(
        _lineup_fragment(home, lineups.get("home")), _lineup_fragment(away, lineups.get("away"))
    )
    if composed:
        lines.append(("Compos", composed))

    injuries = data.get(KIND_INJURIES)
    if injuries is not None:
        if not injuries.get("available"):
            # Un etat absent vaut « non interroge » : c'est le cas des releves
            # anterieurs a cette distinction, et le plus prudent des deux — il
            # envoie chercher au lieu d'affirmer qu'on a regarde.
            etat = injuries.get("state") or INJURIES_NOT_ASKED
            lines.append(("Absents", INJURIES_NOTES.get(etat, INJURIES_NOTES[INJURIES_NOT_ASKED])))
        else:
            lines.append(
                (
                    "Absents",
                    _injuries_for(injuries.get("home") or [], home)
                    + "\n"
                    + _injuries_for(injuries.get("away") or [], away),
                )
            )

    # Effectif reconstruit : il ne parait que la ou « Absents » est muet, et il
    # ne pretend pas le remplacer — voir `_missing_players`.
    sheets = data.get(KIND_SHEETS) or {}
    if sheets.get("available"):
        rendered = _pair(
            _sheets_for(sheets.get("home") or [], home, sheets.get("home_window")),
            _sheets_for(sheets.get("away") or [], away, sheets.get("away_window")),
        )
        if rendered:
            lines.append(("Effectif", rendered))

    h2h = data.get(KIND_H2H)
    if h2h:
        # L'aller precede la suite des scores : la premiere entree de « H2H »
        # est le meme match, et le lecteur doit savoir a quoi elle correspond
        # avant de la lire comme un antecedent parmi d'autres.
        league = (data.get(KIND_TEAMS) or {}).get("league")
        aller = _return_leg_line(h2h, league, away, commence_time)
        if aller:
            lines.append(("Aller", aller))
        # L'arithmetique de la double confrontation, juste sous le fait dont
        # elle se deduit : cumul, qui est qualifie en l'etat, ce qu'il faut a
        # l'autre. Vingt-quatre manches retour en une semaine ont demande ce
        # meme calcul refait a la main, et il est deterministe.
        scenario = _scenario_line(h2h, league, home, away, commence_time, neutre)
        if scenario:
            lines.append(("Scenario", scenario))
        rendered = _h2h_line(h2h, settings)
        if rendered:
            lines.append((f"H2H ({len(h2h.get('matches') or [])})", rendered))

    rest = _pair(
        _prefix(home, _rest_days(recent.get("home"), commence_time)),
        _prefix(away, _rest_days(recent.get("away"), commence_time)),
    )
    if rest:
        lines.append(("Repos", rest))

    return lines


def _prefix(team: str, value: str) -> str:
    return f"{team} {value}" if value else ""


def _standing_played(entry: dict[str, Any] | None) -> bool:
    """Vrai si le classement porte sur au moins un match joue.

    A zero match, le fournisseur classe quand meme tout le monde : l'Eredivisie
    ouvrait sa saison avec « FC Zwolle 7e (0pts, 0j, +0) » et « Ajax 8e (0pts,
    0j, +0) ». Ce rang ne classe rien — il vient de la saison passee ou de
    l'ordre alphabetique — et l'« Enjeu » qui s'en deduit non plus : le meme
    bloc annoncait « Promotion - Eredivisie (Conference League - Play Offs) »
    avant le premier coup d'envoi du championnat.

    Toutes les statistiques de saison se taisaient deja sur ces deux matchs
    (`SEASON_MIN_MATCHES`), et `Dom/Ext` pour la meme raison : ces deux lignes
    la etaient les seules a passer au travers.

    Le seuil est **un** match et non cinq : des la premiere journee le rang
    decrit un resultat reel, et la ligne porte deja son compte (`0pts, 1j`) —
    au lecteur de juger de ce que vaut un classement de debut de saison.
    Absente, la donnee ne prouve rien : on ne retient que le zero constate.
    """
    return (entry or {}).get("played") != 0


def _rank_fragment(team: str, entry: dict[str, Any] | None) -> str:
    if not entry or entry.get("rank") is None or not _standing_played(entry):
        return ""
    rank = entry["rank"]
    suffix = "er" if rank == 1 else "e"
    detail = ", ".join(
        part
        for part in (
            f"{entry['points']}pts" if entry.get("points") is not None else "",
            f"{entry['played']}j" if entry.get("played") is not None else "",
            # La difference de buts separe deux equipes a egalite de points,
            # ce que le rang seul ne dit pas. Signee, pour qu'un negatif se voie.
            f"{entry['diff']:+d}" if isinstance(entry.get("diff"), int) else "",
        )
        if part
    )
    return f"{team} {rank}{suffix} ({detail})" if detail else f"{team} {rank}{suffix}"


def _stake_fragment(team: str, entry: dict[str, Any] | None) -> str:
    """`Estoril Play-offs` — l'enjeu, tel que le fournisseur le nomme.

    Il arrivait dans l'appel de classement et partait a la poubelle. C'est
    pourtant l'« enjeu reel » que la fiche de verification du prompt reclame a
    chaque match, et que la recherche web devait sinon deviner du rang.

    Le libelle est **recopie tel quel**, jamais traduit ni interprete : il vient
    du fournisseur, qui le tient de la competition. « Relegation Round » veut
    dire ce qu'il dit, et le reecrire serait s'en porter garant.

    Meme garde que le rang, et pour la meme raison : a zero match joue, l'enjeu
    se deduit d'un classement qui ne classe rien.
    """
    stake = (entry or {}).get("stake")
    if not stake or not _standing_played(entry):
        return ""
    # A la 3e journee sur 32, « Relegation Playoffs » est un artefact du
    # classement : il decrit l'ordre alphabetique autant que le niveau. Le
    # prompt ordonne pourtant de recopier cette ligne comme l'enjeu reel, sans
    # recherche. Elle est donc **datee** plutot que supprimee — l'information
    # reste, sa portee est dite. Le nombre de journees jouees suffit : le total
    # de la saison ne se deduit pas du nombre d'equipes, une Superliga danoise
    # jouant 32 journees a douze equipes.
    played = (entry or {}).get("played") or 0
    if played < value_of("enjeu_min_journees"):
        return f"{team} {stake} (après {played}j — indicatif)"
    return f"{team} {stake}"


#: Surfaces telles que le fournisseur les nomme. Une pelouse naturelle ne
#: produit aucune ligne : c'est le cas ordinaire.
SURFACE_LABELS = {
    "artificial turf": "synthetique",
    "astroturf": "synthetique",
    "hybrid grass": "hybride",
}


#: Ce que la ligne `Lieu` peut dire d'un match. Trois etats et non un booleen :
#: un domicile **suppose** qui n'en est pas serait pire qu'un « non renseigne »
#: franc — meme regle que le fuseau du lieu, et pour la meme raison.
#: Marqueur rendu dans la ligne `Lieu`. Constante et non litteral : la fiche de
#: recherche le relit pour classer, et deux ecritures auraient diverge.
NEUTRAL_MARK = "TERRAIN NEUTRE"

VENUE_HOME = "home"
VENUE_NEUTRAL = "neutral"
#: Le lieu est connu, mais **aucun identifiant ne permet de le comparer** au
#: stade habituel. Mesure du 12/08/2026 : `fixture.venue.id` est nul sur
#: **210 matchs sur 210** d'une saison de Conference League, et servi sur
#: **380 sur 380** d'une saison de Premier League. Le drapeau de terrain neutre
#: est donc structurellement muet sur les competitions UEFA — exactement la ou
#: les delocalisations arrivent.
#:
#: Rendre « donnees non disponibles » y jetait le nom du stade et sa ville, que
#: le fournisseur sert pourtant. Or c'est cela qui fait sauter l'anomalie aux
#: yeux : un club israelien qui « recoit » a Miskolc se lit sans qu'aucun
#: drapeau soit calcule. L'etat dit donc le lieu **et** pourquoi la comparaison
#: manque.
VENUE_UNIDENTIFIED = "unidentified"
VENUE_UNKNOWN = "unknown"

#: Trois lettres du pays, comme le fournisseur les publie en toutes lettres. La
#: table ne couvre que ce qu'on a vu : un pays absent se rend tel quel, ce qui
#: est lisible et n'invente rien. Les libelles viennent de **deux** vocabulaires
#: — API-Football pour le pays d'un club ou d'un stade identifie, Open-Meteo pour
#: celui d'une ville geocodee — d'ou les deux orthographes des Pays-Bas.
COUNTRY_CODES = {
    "andorra": "AND",
    "armenia": "ARM",
    "austria": "AUT",
    "azerbaijan": "AZE",
    "belarus": "BLR",
    "belgium": "BEL",
    "bosnia and herzegovina": "BIH",
    "brazil": "BRA",
    "bulgaria": "BGR",
    "china": "CHN",
    "croatia": "HRV",
    "czechia": "CZE",
    "denmark": "DNK",
    "estonia": "EST",
    "finland": "FIN",
    "france": "FRA",
    "georgia": "GEO",
    "germany": "DEU",
    "greece": "GRC",
    "hungary": "HUN",
    "ireland": "IRL",
    "israel": "ISR",
    "kazakhstan": "KAZ",
    "kosovo": "XKX",
    "latvia": "LVA",
    "liechtenstein": "LIE",
    "lithuania": "LTU",
    "moldova": "MDA",
    "netherlands": "NLD",
    "north macedonia": "MKD",
    "norway": "NOR",
    "poland": "POL",
    "portugal": "PRT",
    "romania": "ROU",
    "russia": "RUS",
    "san marino": "SMR",
    "serbia": "SRB",
    "slovakia": "SVK",
    "slovenia": "SVN",
    "sweden": "SWE",
    "switzerland": "CHE",
    "the netherlands": "NLD",
    "ukraine": "UKR",
    "united kingdom": "GBR",
}

#: Les quatre nations britanniques, et ce n'est pas une commodite d'affichage :
#: API-Football donne « Scotland » au club, Open-Meteo donne « United Kingdom »
#: a la ville. Sans ce rapprochement, Dundee et Motherwell n'ont **aucun**
#: candidat dans le pays de leur club — mesure du 12/08/2026 — et un match a
#: domicile passerait par la branche des delocalisations.
HOME_NATIONS = {"england", "scotland", "wales", "northern ireland"}

#: Population minimale pour qu'une ville **hors du pays du club** soit retenue,
#: et rapport minimal avec le meilleur homonyme d'un autre pays.
#:
#: Les deux gardent la **branche extraordinaire** : dire qu'un club joue hors de
#: chez lui sur la foi d'un nom de ville. Mesure sur les villes de stade reelles
#: (12/08/2026) : les cinq delocalisations connues visent Miskolc 154 521,
#: Salzburg 157 245, Ploiesti 180 540, Stara Zagora 121 582 et Lublin 336 339 —
#: toutes au-dessus de cent mille, et toutes seules ou 660 fois plus peuplees que
#: leur premier homonyme. Le faux positif que le geocodage produisait, lui, tient
#: en un village : « Brügge », 1 019 habitants en Allemagne, la ou le Club Bruges
#: est belge. Deux ordres de grandeur separent les deux cas de chaque seuil.
VENUE_ABROAD_MIN_POPULATION = 20_000
VENUE_ABROAD_MIN_RATIO = 10


def _country_tag(country: str | None) -> str:
    """`(BGR)`, ou le nom entier quand il n'est pas dans la table, ou rien."""
    name = (country or "").strip()
    if not name:
        return ""
    return f" ({COUNTRY_CODES.get(name.casefold(), name)})"


def _moved_venue(venue: dict[str, Any]) -> bool:
    """Le match se joue-t-il ailleurs que dans le stade habituel du receveur ?

    **Sur les identifiants, jamais sur les libelles.** La comparaison de chaines
    a produit exactement le bruit qu'elle devait supprimer : « Parken Stadium,
    Copenhagen — hors de København » annoncait une delocalisation entre deux
    orthographes de la meme ville. La ligne a fini par etre ignoree, c'est-a-dire
    l'inverse de son but.

    Le `venue` d'un match **a** un identifiant — le commentaire qui disait le
    contraire datait d'une lecture trop rapide de la charge utile. Deux
    identifiants connus et differents sont un fait ; tout le reste est une
    inconnue.
    """
    ici, habituel = venue.get("venue_id"), venue.get("usual_id")
    return bool(ici and habituel and int(ici) != int(habituel))


def _country_key(country: str | None) -> str:
    """Cle de comparaison de deux pays ecrits par deux fournisseurs.

    Trois divergences constatees, et aucune n'est une faute de frappe : l'article
    des Pays-Bas, la casse, et les quatre nations britanniques que l'un compte
    pour des pays et l'autre pour des regions.
    """
    cle = sort_key(country).strip().removeprefix("the ")
    return "united kingdom" if cle in HOME_NATIONS else cle


async def _geocoded_country(
    geo_client: WeatherClient, city: str, home_country: str | None
) -> str | None:
    """Pays d'une ville de stade, par le geocodage, ou `None` en cas de doute.

    **C'est la moitie manquante du drapeau de terrain neutre.** Le pays d'un
    stade ne se demande a API-Football qu'avec un identifiant de stade, et il est
    nul sur 210 matchs sur 210 d'une saison de Conference League — donc absent
    exactement la ou les delocalisations arrivent. Le geocodeur, lui, ne coute
    rien et n'a pas besoin d'identifiant : il a besoin d'un garde-fou.

    **Deux temps, et l'ordre porte toute la regle.**

    1. Un homonyme dans le pays du club emporte la decision. C'est le cas
       ordinaire — un club joue chez lui — et c'est aussi ce qui rattrape les
       villes que le geocodeur classe mal : « Ried » rend l'Allemagne (2 987
       habitants) avant l'Autriche, et le SV Ried est autrichien. Mesure du
       12/08/2026 : **aucune** des cinq delocalisations connues n'a d'homonyme
       dans le pays de son club, donc cette preference n'en cache aucune.
    2. Sinon on affirme que le match se joue a l'etranger, ce qui est une
       affirmation forte : elle demande une ville d'une taille plausible et un
       homonyme decisif, faute de quoi **aucun pays n'est rendu**.

    Ce que la regle ne peut pas rattraper, et il faut le savoir : un libelle de
    ville faux chez le fournisseur. ML Vitebsk recevait a Mezokovesd, en Hongrie,
    sous un `city` qui dit « Vitebsk » — le pays rendu sera donc le Belarus. La
    ligne garde pour cela sa mention « terrain neutre non verifiable » : c'est
    elle, et non le pays, qui dit de ne pas conclure.
    """
    rows = await geo_client.places(city)
    exacts = [row for row in rows if sort_key(row.get("name")) == sort_key(city)]
    if not exacts:
        return None

    chez_le_club = _country_key(home_country)
    if chez_le_club:
        for row in exacts:
            if _country_key(row.get("country")) == chez_le_club:
                return str(row.get("country"))

    peuplees: dict[str, int] = {}
    for row in exacts:
        pays = str(row.get("country") or "").strip()
        if pays:
            peuplees[pays] = max(peuplees.get(pays, 0), int(row.get("population") or 0))
    classement = sorted(peuplees.items(), key=lambda entry: -entry[1])
    if not classement or classement[0][1] < VENUE_ABROAD_MIN_POPULATION:
        return None
    suivant = classement[1][1] if len(classement) > 1 else 0
    if suivant * VENUE_ABROAD_MIN_RATIO > classement[0][1]:
        return None
    return classement[0][0]


def venue_state(venue: dict[str, Any]) -> str:
    """Lequel des trois etats decrit ce match.

    Neutre veut dire **hors du pays du club qui recoit**, et pas seulement hors
    de son stade : un match deplace pour travaux ou sanction reste chez lui, le
    public suit. C'est la difference entre une contrainte logistique et une
    contrainte politique ou securitaire, et seule la seconde change la lecture.
    """
    if not venue.get("venue_id") or not venue.get("usual_id"):
        # Sans identifiant des deux cotes, la comparaison est hors de portee. Le
        # lieu, lui, peut etre connu : le dire vaut mieux que le taire.
        nomme = (venue.get("name") or "").strip() or (venue.get("city") or "").strip()
        return VENUE_UNIDENTIFIED if nomme else VENUE_UNKNOWN
    if not _moved_venue(venue):
        return VENUE_HOME
    pays, chez_lui = venue.get("country"), venue.get("home_country")
    if not pays or not chez_lui:
        return VENUE_UNKNOWN
    return VENUE_NEUTRAL if sort_key(pays) != sort_key(chez_lui) else VENUE_HOME


def _venue_halves(venue: dict[str, Any]) -> tuple[str, str, str]:
    """Le stade et la ville **du match**, et le nom de la moitie qui manque.

    **Les deux moities viennent du meme releve, ou l'absente se dit.** Les
    remplir chacune de son cote — le nom du match, la ville a defaut celle du
    stade habituel — ne produit pas une information incomplete mais **un lieu
    qui n'existe pas**. Mesure du 13/08/2026 : le fournisseur ne servait aucun
    nom de stade pour KI Klaksvik, seulement la ville, et le bloc a rendu
    `Injector Arena, Torshavn` — Injector Arena est le terrain de KI, a
    Klaksvik, quand la rencontre se jouait au stade national de Torshavn.

    C'est une autre classe de defaut que l'omission, et c'est pour ca qu'il
    passe : une omission se voit, un fait fabrique se cite. Celui-la sortait
    meme sous une mention rassurante — « pas d'identifiant de stade ici, terrain
    neutre non verifiable » — qui invite a lire l'anomalie sans dire qu'elle a
    ete composee. Il a fallu une recherche pour la defaire, sur le seul match du
    lot ou quelqu'un a regarde.

    Trois formes sur les douze matchs du lot : les deux moities servies (9), la
    ville seule (KI Klaksvik), le stade seul (Egnatia Rrogozhinë). Le cas ou
    rien n'est servi est deja traite en amont par `VENUE_UNKNOWN`.
    """
    nom = (venue.get("name") or "").strip()
    ville = (venue.get("city") or "").strip()
    if nom and ville:
        return nom, ville, ""
    return nom, ville, "stade non precise" if not nom else "ville non precisee"


def _venue_line(venue: dict[str, Any], home: str) -> str:
    """`Stadion Beroe, Stara Zagora (BGR) — TERRAIN NEUTRE, X recoit hors de son pays`.

    La ligne est **systematique** et non plus reservee a la surprise. Elle
    l'etait pour epargner des tokens, et le calcul etait faux dans l'autre
    sens : son absence ne se distinguait pas d'un domicile ordinaire, si bien
    qu'un match delocalise dont le lieu n'avait pas ete recupere passait pour un
    match a domicile. Trois etats, tous ecrits.
    """
    etat = venue_state(venue)
    if etat == VENUE_UNKNOWN:
        return UNAVAILABLE
    nom, ville, manquant = _venue_halves(venue)
    lieu = ", ".join(part for part in (nom, ville) if part)
    if manquant:
        # La moitie absente est **nommee**, et le stade habituel arrive derriere
        # son propre libelle : c'est le seul endroit ou il peut paraitre sans se
        # faire passer pour le lieu du match.
        habituel = ", ".join(
            part
            for part in (
                (venue.get("usual_name") or "").strip(),
                (venue.get("usual_city") or "").strip(),
            )
            if part
        )
        rappel = f" (habituel : {habituel})" if habituel else ""
        return f"{lieu} — {manquant}{rappel}, terrain neutre non verifiable"
    if etat == VENUE_UNIDENTIFIED:
        # **Le pays vient du geocodage de la ville**, pas du club : ecrire celui
        # du club a cote d'un stade qui n'est peut-etre pas le sien affirmerait
        # justement ce qu'on ne sait pas. Un club israelien qui « recoit » a
        # Miskolc (HUN) se lit alors sans qu'aucun drapeau soit calcule, et la
        # mention qui suit dit que la comparaison, elle, reste hors de portee.
        return (
            f"{lieu}{_country_tag(venue.get('geo_country'))}"
            " — pas d'identifiant de stade ici, terrain neutre non verifiable"
        )
    if etat == VENUE_HOME:
        return f"{lieu}{_country_tag(venue.get('country') or venue.get('home_country'))}"
    return (
        f"{lieu}{_country_tag(venue.get('country'))} — {NEUTRAL_MARK}, "
        f"{home} recoit hors de son pays"
    )


def _referee_line(payload: dict[str, Any]) -> str:
    """`M. Oliver`, ou `non encore designe`. Deux etats, et pas trois.

    **Le nom seul, parce que c'est tout ce que le fournisseur sert.** Verifie le
    12/08/2026 : `fixture.referee` est une chaine libre — pas d'identifiant, pas
    de pays, et pas de format stable (64 des 183 arbitres d'une saison de
    Conference League s'ecrivent « X. Nom », les autres non).

    **Un historique de cartons n'est donc pas reconstructible a cout
    raisonnable** : il faudrait agreger sur le libelle — « M. Oliver » et
    « Michael Oliver » seraient deux arbitres — puis un appel de statistiques
    **par match passe**. Et le compte de matchs diriges, lui, serait du decor :
    sur une saison de Conference League, **157 arbitres sur 183 n'en ont qu'un**,
    donc la ligne dirait « premier match » sur presque tous les blocs.

    Ce qui reste vaut quand meme le coup, et c'est mesure : sans cette ligne, il
    fallait une requete pour savoir **qui** arbitre avant d'en chercher une
    seconde sur son historique. Le nom en supprime une sur deux.

    Le troisieme etat — « aucun historique dans cette confederation » — n'est
    pas rendu ici parce qu'il ne se constate pas d'ici : c'est un **resultat de
    recherche**, et le preambule dit que c'en est un valable.
    """
    nom = (payload.get("name") or "").strip()
    return nom or "non encore designe"


def _profile_suffix(profile: dict[str, Any]) -> str:
    """« /5 » : sur combien de matchs la moyenne porte.

    Le compte accompagne toujours la moyenne, comme le taux accompagne le
    nombre de paris dans les statistiques : « 6.0 corners » sur deux matchs et
    sur cinq ne disent pas la meme chose.
    """
    matches = profile.get("matches")
    return f"/{matches}" if matches else ""


def _profiled(profile: dict[str, Any] | None, key: str) -> bool:
    """Vrai si la moyenne repose sur assez de matchs pour etre publiee."""
    if not profile or profile.get(key) is None:
        return False
    return int(profile.get("matches") or 0) >= PROFILE_MIN_MATCHES


def _corner_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 5.2 pris 6.4/5` — corners tires, puis concedes."""
    if not _profiled(profile, "corners"):
        return ""
    against = profile.get("corners_against")
    tail = f" pris {against}" if against is not None else ""
    return f"{team} {profile['corners']}{tail}{_profile_suffix(profile)}"


def _fouls_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 12.4 subies 10.8/5` — fautes commises, puis subies.

    Elle accompagne « Cartons » : un arbitre ne sort un carton que sur une
    faute, et une equipe qui en commet quatorze par match n'aborde pas le
    marche des cartons comme celle qui en commet huit.
    """
    if not _profiled(profile, "fouls"):
        return ""
    against = profile.get("fouls_against")
    tail = f" subies {against}" if against is not None else ""
    return f"{team} {profile['fouls']}{tail}{_profile_suffix(profile)}"


def _possession_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 54 %/5` — part du ballon, en pourcentage.

    **Le seul pourcentage autorise du bloc, et il ne contredit pas la regle.**
    L'interdit vise les *frequences d'issues* — « BTTS 56 % » invite a diviser
    par une cote, ce qui est un calcul d'esperance. Une part de ballon n'est
    pas une frequence d'issue : elle ne se rapporte a aucun marche, rien ne se
    divise par elle, et sa seule unite naturelle est le pourcentage.
    """
    if not _profiled(profile, "possession"):
        return ""
    return f"{team} {profile['possession']:.0f} %{_profile_suffix(profile)}"


def _xg_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 1.85 concede 0.92/5` — buts attendus produits, puis concedes.

    C'est la seule ligne du bloc qui ne soit pas un fait observe mais une
    **sortie de modele**, et elle est rendue en le sachant : elle dit si les
    buts d'une equipe viennent d'occasions repetees ou d'une frappe heureuse,
    ce qu'aucun compte de tirs ne separe.

    Elle porte donc la meme interdiction que l'Elo, et pour la meme raison : la
    convertir en probabilite puis la rapprocher d'une cote serait le calcul
    d'esperance de la section 9. Le template le dit noir sur blanc, un test le
    verifie.
    """
    if not _profiled(profile, "xg"):
        return ""
    against = profile.get("xg_against")
    tail = f" concédé {against}" if against is not None else ""
    return f"{team} {profile['xg']}{tail}{_profile_suffix(profile)}"


def _card_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 2.4j 0.2r/5` — jaunes et rouges par match."""
    if not _profiled(profile, "yellow"):
        return ""
    red = profile.get("red")
    tail = f" {red}r" if red else ""
    return f"{team} {profile['yellow']}j{tail}{_profile_suffix(profile)}"


def _shot_fragment(team: str, profile: dict[str, Any] | None) -> str:
    """`Estoril 12.4 dont 4.6 cadres/5`."""
    if not _profiled(profile, "shots"):
        return ""
    on_target = profile.get("shots_on")
    tail = f" dont {on_target} cadres" if on_target is not None else ""
    return f"{team} {profile['shots']}{tail}{_profile_suffix(profile)}"


def _form_fragment(team: str, stats: dict[str, Any] | None, recent: dict[str, Any] | None) -> str:
    """`Celtic V (6-8/5)` — les lettres, puis les buts et leur compte.

    **Les deux moities ne portent pas sur la meme fenetre**, et c'est ce que le
    compte rend enfin visible. Les lettres viennent de `/teams/statistics`,
    donc de la **seule competition du jour** ; les buts viennent des
    `RECENT_LAST` derniers matchs **toutes competitions**. Les deux coincident
    des qu'une equipe a joue cinq matchs dans la competition — soit partout,
    sauf en debut de saison, ou l'ecart devient absurde : « Celtic V (6-8) » se
    lisait « une victoire, six buts marques, huit encaisses », et « Slask
    Wroclaw DV (12-4) » douze buts en deux matchs.

    **Chaque moitie porte donc son propre denominateur** : `Silkeborg ND (2j)
    10-6/5` se lit « deux matchs dans cette competition, dix buts marques et six
    encaisses sur les cinq derniers toutes competitions ». Un seul compte ne
    suffisait pas : `ND (10-6/5)` laissait croire a seize buts en deux matchs,
    et rien ne disait que les lettres venaient d'ailleurs.

    Les deux comptes s'ecrivent **meme quand ils coincident**, ce qui est le cas
    ordinaire. Ne les ecrire qu'en cas d'ecart rendrait une ligne sans annotation
    ambigue : coincidence, ou verification jamais faite ?
    """
    letters = _form_letters((stats or {}).get("form"))
    if not letters:
        return ""
    # La longueur des lettres **est** la fenetre : `_form_letters` garde les
    # `FORM_LENGTH` dernieres, donc une equipe a deux matchs joues en porte deux.
    played = f"({len(letters)}j)"
    if recent and recent.get("matches"):
        goals = f"{recent['goals_for']}-{recent['goals_against']}/{recent['matches']}"
        return f"{team} {letters} {played} {goals}"
    return f"{team} {letters} {played}"
