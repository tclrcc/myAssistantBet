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
from datetime import UTC, datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.apifootball import APIFootballClient
from ..providers.base import ProviderError
from .labels import sort_key
from .matching import Resolution, resolve_team
from .render import UNAVAILABLE

logger = logging.getLogger(__name__)


class _NotCovered(Exception):
    """Le fournisseur declare ne pas couvrir cette donnee pour la competition.

    Distincte d'une erreur : rien n'a echoue, il n'y a simplement rien a
    chercher. La ligne existe quand meme, avec la mention « non disponible ».
    """


H2H_LAST = 5
RECENT_LAST = 5
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

#: Lettres API-Football -> lettres francaises. Attention au piege : « D » cote
#: API signifie Draw (nul), et « L » signifie Loss (defaite).
FORM_LETTERS = {"W": "V", "D": "N", "L": "D"}

KIND_STANDINGS = "standings"
KIND_FORM = "form"
KIND_INJURIES = "injuries"
KIND_H2H = "h2h"
KIND_RECENT = "recent"
KIND_PROFILE = "profile"
KIND_VENUE = "venue"
KIND_MAPPING = "mapping_pending"
KIND_MANUAL_NOTE = "manual_note"


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
    #: Stade du match, tel que le fournisseur le donne. Sans identifiant
    #: exploitable, mais avec sa ville — de quoi voir une delocalisation.
    venue: dict[str, Any] = field(default_factory=dict)


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

    return FixtureMapping(
        fixture_id=int(fixture["fixture"]["id"]),
        league_id=league_id,
        # La saison portee par le match prime — c'est la sienne. Celle de la
        # ligue ne sert que si le fournisseur l'omet.
        season=int((fixture.get("league") or {}).get("season") or season),
        home_id=home.matched.apifootball_id,
        away_id=away.matched.apifootball_id,
        coverage=coverage,
        venue=(fixture.get("fixture") or {}).get("venue") or {},
    )


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
PROFILE_STATS = {
    "Corner Kicks": "corners",
    "Yellow Cards": "yellow",
    "Red Cards": "red",
    "Total Shots": "shots",
    "Shots on Goal": "shots_on",
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
) -> ContextReport:
    """Recupere et persiste tout le contexte disponible pour un evenement.

    `cache` memorise classements et statistiques d'equipe le temps d'un
    enrichissement : deux matchs de la meme ligue ne paient pas deux fois.
    Aucune erreur n'est propagee : ce qui manque est simplement absent du
    rapport, donc rendu comme « donnee non disponible ».
    """
    settings = settings or get_settings()
    cache = cache if cache is not None else {}
    report = ContextReport(event_id=int(event["id"]), label=f"{event['home']} – {event['away']}")

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
            "name": mapping.venue.get("name"),
            "city": mapping.venue.get("city"),
            "usual_name": usual.get("name"),
            "usual_city": usual.get("city"),
            "surface": usual.get("surface"),
        }
        if payload["city"] or payload["surface"]:
            store(report.event_id, KIND_VENUE, payload, settings)
            report.kinds.append(KIND_VENUE)
    except ProviderError as exc:
        report.errors.append(f"lieu : {exc}")

    # Profil corners et cartons, sur les memes matchs recents. Le prompt
    # proposait des lignes de corners sans rien savoir de ce qu'une equipe en
    # produit ou en concede : le marche etait rendu, l'angle sportif absent.
    try:
        profile = {}
        for side, team_id in (("home", mapping.home_id), ("away", mapping.away_id)):
            fixtures = await _memo(
                f"recent:{team_id}", lambda tid=team_id: client.last_fixtures(tid, RECENT_LAST)
            )
            profile[side] = await _fetch_profile(client, cache, fixtures, team_id)
        if any(profile.values()):
            store(report.event_id, KIND_PROFILE, profile, settings)
            report.kinds.append(KIND_PROFILE)
    except ProviderError as exc:
        report.errors.append(f"profil corners/cartons : {exc}")

    # Blesses et suspendus — couverture irreguliere selon les ligues.
    try:
        if not mapping.coverage.get("injuries", True):
            # Sans ce test, une liste vide se rendait « aucun signale » — soit
            # l'affirmation inverse de la verite. Constate en reel sur les
            # qualifications europeennes, ou six absents annonces par la presse
            # etaient rendus « aucun signale » des deux cotes.
            store(report.event_id, KIND_INJURIES, {"available": False}, settings)
            report.kinds.append(KIND_INJURIES)
            raise _NotCovered
        rows = await client.injuries(mapping.fixture_id)
        payload = {"available": True, "home": [], "away": []}
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
        store(report.event_id, KIND_INJURIES, {"available": False}, settings)
        report.errors.append(f"absents : {exc}")

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


def _injuries_for(entries: list[dict[str, Any]], team: str) -> str:
    if not entries:
        return f"{team} — aucun signale"
    listed = []
    for entry in entries:
        name = entry.get("name") or "?"
        detail = ", ".join(str(item) for item in (entry.get("type"), entry.get("reason")) if item)
        listed.append(f"{name} ({detail})" if detail else name)
    return f"{team} — {', '.join(listed)}"


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

    venue = data.get(KIND_VENUE) or {}
    if venue:
        # Le lieu n'est rendu que s'il surprend : ecrire « joue chez lui » sous
        # chaque affiche couterait des tokens pour ne rien apprendre.
        moved = _relocated(venue)
        if moved:
            lines.append(("Lieu", moved))
        surface = (venue.get("surface") or "").strip().lower()
        if surface and "grass" not in surface:
            lines.append(("Pelouse", SURFACE_LABELS.get(surface, surface)))

    profile = data.get(KIND_PROFILE) or {}
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

    shots = _pair(
        _shot_fragment(home, profile.get("home")), _shot_fragment(away, profile.get("away"))
    )
    if shots:
        lines.append(("Tirs", shots))

    injuries = data.get(KIND_INJURIES)
    if injuries is not None:
        if not injuries.get("available"):
            lines.append(("Absents", UNAVAILABLE))
        else:
            lines.append(
                (
                    "Absents",
                    _injuries_for(injuries.get("home") or [], home)
                    + "\n"
                    + _injuries_for(injuries.get("away") or [], away),
                )
            )

    h2h = data.get(KIND_H2H)
    if h2h:
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


def _rank_fragment(team: str, entry: dict[str, Any] | None) -> str:
    if not entry or entry.get("rank") is None:
        return ""
    rank = entry["rank"]
    suffix = "er" if rank == 1 else "e"
    detail = ", ".join(
        part
        for part in (
            f"{entry['points']}pts" if entry.get("points") is not None else "",
            f"{entry['played']}j" if entry.get("played") is not None else "",
        )
        if part
    )
    return f"{team} {rank}{suffix} ({detail})" if detail else f"{team} {rank}{suffix}"


#: Surfaces telles que le fournisseur les nomme. Une pelouse naturelle ne
#: produit aucune ligne : c'est le cas ordinaire.
SURFACE_LABELS = {
    "artificial turf": "synthetique",
    "astroturf": "synthetique",
    "hybrid grass": "hybride",
}


def _relocated(venue: dict[str, Any]) -> str:
    """Ligne de lieu quand un match ne se joue pas chez l'equipe qui recoit.

    Le `venue` d'un match n'a pas d'identifiant exploitable : restent son nom et
    sa ville. **Il faut que les deux different**, et voici pourquoi — la ville
    seule se trompe, le nom seul laisse passer :

    - `Veritas Stadion / Turku` contre `Veritas Stadion / Åbo` : meme stade,
      deux noms de la meme ville finlandaise. Idem `Belgrade` et `Beograd`.
    - `Teddy Stadium / Ploiesti` contre `Teddi Malcha Stadium / Jerusalem` :
      le fournisseur garde un nom de stade proche alors que la rencontre est
      bel et bien delocalisee en Roumanie.

    En cas de doute — une seule des deux differences, ou une donnee manquante —
    aucune ligne. On ne remplace pas une inconnue par une supposition, et une
    delocalisation inventee se lit comme un fait.
    """
    city = sort_key((venue.get("city") or "").strip())
    usual_city = sort_key((venue.get("usual_city") or "").strip())
    name = sort_key((venue.get("name") or "").strip())
    usual_name = sort_key((venue.get("usual_name") or "").strip())
    if not (city and usual_city and name and usual_name):
        return ""
    if city == usual_city or name == usual_name:
        return ""
    lieu = f"{(venue.get('name') or '').strip()}, {(venue.get('city') or '').strip()}"
    return f"{lieu} — hors de {(venue.get('usual_city') or '').strip()}"


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
    letters = _form_letters((stats or {}).get("form"))
    if not letters:
        return ""
    if recent and recent.get("matches"):
        return f"{team} {letters} ({recent['goals_for']}-{recent['goals_against']})"
    return f"{team} {letters}"
