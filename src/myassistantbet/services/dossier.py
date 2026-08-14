"""Dossier d'equipe : ce qui vaut pour une equipe et non pour une rencontre.

Meme decoupage en deux temps que `services/context.py`, et pour la meme raison :

- `refresh_event()` appelle API-Football et **persiste** dans `team_context` ;
- `dossier_lines()` relit la base et produit les lignes du bloc CONTEXTE.

L'entraineur est stocke brut. Une saison de matchs, non : sa charge utile pese
43 ko pour 41 matchs, soit une base dix fois plus grosse — et des sauvegardes
avec — pour des logos et des drapeaux. `_summarize()` n'en garde que de quoi
tout recalculer, ce qui reste la seule interpretation faite a la collecte.

Ce qui change par rapport au contexte, c'est la cle de memorisation. Le contexte
est indexe par evenement, ce qui convient aux absents d'un match ou a une
confrontation directe. L'entraineur d'une equipe, lui, est le meme dans les deux
affiches ou elle apparait cette semaine, et le meme la semaine prochaine :
memorise par equipe et perime par duree, il ne se paie qu'une fois.

Le garde-fou de quota ne bloque **que** ce module. Le contexte d'un match reste
la fonction premiere de l'outil ; l'interrompre faute de credits pour un bonus
serait le mauvais arbitrage.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.apifootball import CALL_COST, PROVIDER, APIFootballClient
from ..providers.base import ProviderError, last_known_quota
from .context import KIND_SHEETS, KIND_TEAMS
from .context import load as load_context
from .labels import sort_key

logger = logging.getLogger(__name__)

KIND_COACH = "coach"
KIND_SEASON = "season"
#: Meilleurs buteurs, ranges par **competition** et non par equipe : un appel
#: rend les vingt premiers de toute la ligue, et les stocker par equipe les
#: dupliquerait autant de fois qu'elle compte de clubs.
KIND_SCORERS = "scorers"
#: Indisponibilites d'un **joueur**, troisieme echelle du dossier. Un appel par
#: joueur : demande pour les seuls buteurs deja identifies, jamais pour un
#: effectif entier — trente-six joueurs feraient soixante-douze appels par affiche.
KIND_SIDELINED = "sidelined"

#: Peremption par type, en heures. Elle se regle sur la vitesse a laquelle la
#: donnee change, bornee par ce qu'elle coute.
#:
#: L'entraineur ne change presque jamais — mais quand il change, c'est
#: exactement le fait qui decide d'un pari, et un nom perime est affirme comme
#: un fait. Sept jours est le compromis : un limogeage entre dans le bloc dans
#: la semaine, et le rafraichissement coute un appel par equipe, soit une
#: vingtaine par lot analyse. Allonger economiserait une misere et laisserait un
#: entraineur parti sur la fiche ; raccourcir n'apporterait rien de plus, la
#: nomination etant de toute facon cherchee par la recherche web du prompt.
#:
#: L'historique d'une saison en cours change des qu'un match est joue : douze
#: heures suffisent a ce qu'une journee de championnat entre dans les comptes.
#: Les buteurs suivent la meme cadence, pour un appel par ligue et non par
#: equipe. Un effectif ne bouge qu'au mercato : un mois suffit, et le raccourcir
#: paierait un appel par equipe pour reecrire les memes noms.
#: Une indisponibilite se declare et se leve d'un jour a l'autre, et c'est
#: exactement le fait qui decide d'une props : vingt-quatre heures, soit un appel
#: par buteur et par jour, six au plus par affiche.
TTL_HOURS = {
    KIND_COACH: 24 * 7,
    KIND_SEASON: 12,
    KIND_SCORERS: 12,
    KIND_SIDELINED: 24,
}

#: Peremption d'une saison **terminee**. Elle ne changera plus jamais : la
#: rafraichir toutes les douze heures paierait un appel par equipe pour reecrire
#: les memes lignes. Une valeur longue et non infinie laisse une porte a une
#: correction tardive du fournisseur — il en fait.
PAST_SEASON_TTL_HOURS = 24 * 30

#: Sous cette anciennete, l'arrivee est un fait de la saison en cours et pas une
#: ligne d'etat civil : trois mois, soit le delai au-dela duquel une equipe n'est
#: plus « celle du nouvel entraineur ».
COACH_RECENT_DAYS = 90

#: Statuts d'un match dont le score fait foi. Tout le reste — reporte, annule,
#: interrompu, donne sur tapis vert — ne s'est pas joue, et un 3-0 de forfait
#: fausserait autant les buts que la serie en cours.
PLAYED_STATUSES = frozenset({"FT", "AET", "PEN"})

#: Statuts qui disent qu'un match **ne se jouera pas a l'heure annoncee**, et le
#: mot rendu pour chacun. `NS` est le cas ordinaire et ne produit aucune ligne ;
#: les statuts de match en cours ou termine ne sont pas ici, `session.has_started`
#: s'en charge deja et se lit sur l'horloge — une certitude, la ou ceci est un
#: releve de fournisseur.
SCHEDULE_ALERTS = {
    "PST": "reporte",
    "CANC": "annule",
    "ABD": "abandonne",
    "SUSP": "suspendu",
    "AWD": "donne sur tapis vert",
    "WO": "forfait",
    "TBD": "horaire non fixe",
}

#: Amicaux, exclus de tous les comptes. Une victoire 4-3 en preparation ne dit
#: rien de la saison, et en juillet ce sont les seuls matchs joues : les compter
#: donnerait « >2.5 dans 4/4 » a une equipe qui n'a pas encore joue un match
#: officiel. L'identifiant est la regle — 667 releve sur charge utile reelle — et
#: le libelle un filet de securite. Le projet interdit de **classer** d'apres un
#: libelle ; ici il ne classe rien, il rattrape une ligue amicale non listee, et
#: le seul faux positif possible serait une competition officielle nommee
#: « Friendlies ».
FRIENDLY_LEAGUES = frozenset({10, 667})

#: Sous ce nombre de matchs officiels joues, l'historique d'une saison ne dit
#: rien et on se replie sur la precedente. Meme seuil que les statistiques de
#: saison, et pour la meme raison — sauf qu'ici il existe un repli.
SEASON_MIN_MATCHES = 5

#: Une serie de un match n'est pas une serie.
STREAK_MIN = 2

#: Buteurs rendus par equipe. Au-dela de trois, on liste des remplacants a un but
#: dont aucune props n'est jouable.
SCORERS_KEEP = 3

#: Sous ce nombre de buts, un joueur n'est pas rendu. Verifie en reel : en aout,
#: `/players/topscorers` rend une liste **vide** — aucune journee n'a ete jouee —
#: puis, des septembre, vingt joueurs a un ou deux buts. Les lister ferait passer
#: un classement de coincidences pour une hierarchie de buteurs. Trois buts est le
#: premier palier ou l'ordre commence a decrire quelque chose.
SCORERS_MIN_GOALS = 3

#: Au-dela, le prochain match n'est plus un facteur de rotation d'effectif.
NEXT_MATCH_MAX_DAYS = 10


@dataclass
class DossierReport:
    """Ce qui a ete rafraichi pour un evenement, et ce qui a manque."""

    kinds: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    #: Types relus depuis la base parce qu'encore frais. Le dire evite de croire
    #: a un echec la ou il n'y a qu'un cache qui fait son travail.
    cached: list[str] = field(default_factory=list)
    #: Renseigne quand le plancher d'appels a bloque le rafraichissement.
    blocked_reason: str | None = None

    @property
    def ok(self) -> bool:
        return not self.errors and self.blocked_reason is None


# -- Persistance ------------------------------------------------------------


def store(
    team_id: int,
    kind: str,
    payload: Any,
    scope: str = "",
    settings: Settings | None = None,
    now: datetime | None = None,
) -> None:
    """Remplace un releve du dossier d'une equipe. Idempotent sur sa cle naturelle.

    `now` va jusqu'a l'ecriture, comme dans `elo.store` : la peremption compare
    une date de releve a une date de lecture, et les prendre sur deux horloges
    differentes rendrait le calcul faux — donc intestable.
    """
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if now else utcnow()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO team_context (team_id, kind, scope, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (team_id, kind, scope) DO UPDATE SET "
            "payload_json = excluded.payload_json, fetched_at = excluded.fetched_at",
            (team_id, kind, scope, json.dumps(payload, ensure_ascii=False), stamp),
        )


def load(
    team_id: int, kind: str, scope: str = "", settings: Settings | None = None
) -> tuple[Any, str] | None:
    """Charge utile et date de releve, ou None si rien n'a jamais ete recupere."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT payload_json, fetched_at FROM team_context "
            "WHERE team_id = ? AND kind = ? AND scope = ?",
            (team_id, kind, scope),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"]), row["fetched_at"]


def store_league(
    league_id: int,
    kind: str,
    payload: Any,
    scope: str = "",
    settings: Settings | None = None,
    now: datetime | None = None,
) -> None:
    """Remplace un releve de competition. Meme regle d'idempotence et d'horloge."""
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if now else utcnow()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO league_context (league_id, kind, scope, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (league_id, kind, scope) DO UPDATE SET "
            "payload_json = excluded.payload_json, fetched_at = excluded.fetched_at",
            (league_id, kind, scope, json.dumps(payload, ensure_ascii=False), stamp),
        )


def load_league(
    league_id: int, kind: str, scope: str = "", settings: Settings | None = None
) -> tuple[Any, str] | None:
    """Charge utile et date de releve d'une competition, ou None."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT payload_json, fetched_at FROM league_context "
            "WHERE league_id = ? AND kind = ? AND scope = ?",
            (league_id, kind, scope),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"]), row["fetched_at"]


def store_player(
    player_id: int,
    kind: str,
    payload: Any,
    scope: str = "",
    settings: Settings | None = None,
    now: datetime | None = None,
) -> None:
    """Remplace un releve de joueur. Meme regle d'idempotence et d'horloge."""
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if now else utcnow()
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO player_context (player_id, kind, scope, payload_json, fetched_at) "
            "VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT (player_id, kind, scope) DO UPDATE SET "
            "payload_json = excluded.payload_json, fetched_at = excluded.fetched_at",
            (player_id, kind, scope, json.dumps(payload, ensure_ascii=False), stamp),
        )


def load_player(
    player_id: int, kind: str, scope: str = "", settings: Settings | None = None
) -> tuple[Any, str] | None:
    """Charge utile et date de releve d'un joueur, ou None."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT payload_json, fetched_at FROM player_context "
            "WHERE player_id = ? AND kind = ? AND scope = ?",
            (player_id, kind, scope),
        ).fetchone()
    if row is None:
        return None
    return json.loads(row["payload_json"]), row["fetched_at"]


def _parse(moment: str | None) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def ttl_for(kind: str, scope: str = "", current_season: int | None = None) -> int:
    """Peremption applicable, en heures.

    Une saison terminee ne changera plus : la traiter comme la saison en cours
    paierait un appel par equipe toutes les douze heures pour reecrire les memes
    lignes. C'est le perimetre du releve, et non son type, qui le dit.
    """
    past = kind == KIND_SEASON and scope and current_season is not None
    if past and scope != str(current_season):
        return PAST_SEASON_TTL_HOURS
    return TTL_HOURS.get(kind, 0)


def is_fresh(
    kind: str, fetched_at: str | None, now: datetime | None = None, ttl_hours: int | None = None
) -> bool:
    """Vrai si un releve est encore dans sa duree de validite.

    Une date illisible vaut perime : mieux vaut un appel de trop qu'une donnee
    dont on ne sait plus quand elle a ete prise.
    """
    taken = _parse(fetched_at)
    if taken is None:
        return False
    hours = ttl_hours if ttl_hours is not None else TTL_HOURS.get(kind, 0)
    reference = now or datetime.now(UTC)
    return reference - taken < timedelta(hours=hours)


# -- Recuperation -----------------------------------------------------------


def teams_of(event_id: int, settings: Settings | None = None) -> dict[str, Any]:
    """Identifiants API-Football memorises au rapprochement, ou dictionnaire vide.

    Aucun appel : le rapprochement a deja eu lieu et son resultat est en base.
    Un evenement jamais rapproche — ou dont le rapprochement est reste incertain
    — n'a rien ici, et le dossier ne devine pas.
    """
    payload = load_context(event_id, settings).get(KIND_TEAMS)
    return payload if isinstance(payload, dict) else {}


def status_lines(
    event_id: int, commence_time: str, settings: Settings | None = None
) -> list[tuple[str, str]]:
    """`Statut  reporte (fournisseur de contexte)`, ou rien du tout.

    **L'information dormait deja en base.** `_summarize` garde le statut de
    chaque match de la saison, et le match analyse figure forcement dans
    l'historique de sa propre equipe — le meme constat qui obligeait a un garde
    `days >= 1` sur la ligne `Calendrier`. Personne ne le lisait : le bloc a
    servi « Rakow Czestochowa - Zaglebie Lubin » avec ses cotes le jour ou il
    etait reporte **depuis neuf jours**, et seule une recherche exterieure l'a
    rattrape. Aucun appel n'est ajoute ici.

    Le rapprochement se fait sur la **journee** et non sur l'heure exacte : un
    report s'accompagne souvent d'un changement d'horaire, et exiger la minute
    ferait manquer precisement le cas qu'on cherche. Une equipe ne joue pas deux
    fois le meme jour, l'ambiguite n'existe pas.

    Rien n'est rendu quand le rapprochement n'a pas eu lieu, quand la saison
    n'est pas en base, ou quand le statut est celui d'un match normal. **Une
    absence de ligne ne prouve donc pas qu'un match aura lieu** — elle dit
    seulement que rien ne s'y oppose dans ce que nous savons.
    """
    settings = settings or get_settings()
    teams = teams_of(event_id, settings)
    season = teams.get("season")
    moment = _parse(commence_time)
    if not season or moment is None:
        return []

    jour = moment.date()
    for side in ("home", "away"):
        if not teams.get(side):
            continue
        known = load(int(teams[side]), KIND_SEASON, str(season), settings)
        if known is None or not isinstance(known[0], list):
            continue
        for match in known[0]:
            if not isinstance(match, dict):
                continue
            date = _parse(match.get("date") or "")
            if date is None or date.date() != jour:
                continue
            alerte = SCHEDULE_ALERTS.get(match.get("status") or "")
            if alerte:
                return [("Statut", f"{alerte} (fournisseur de contexte)")]
            return []
    return []


def _score_90(fixture: dict[str, Any]) -> tuple[int, int] | None:
    """Score a **90 minutes**, du point de vue domicile. None si non joue.

    `score.fulltime` et non `goals` : sur un match decide en prolongation, `goals`
    porte le total prolongation comprise, alors que le marche O/U d'un bookmaker
    se regle sur les 90 minutes. Compter la prolongation gonflerait la frequence
    des « plus de 2.5 » sur toutes les coupes. Les deux champs sont identiques
    sur un match ordinaire, donc ce choix ne coute rien ailleurs.
    """
    score = (fixture.get("score") or {}).get("fulltime") or {}
    home, away = score.get("home"), score.get("away")
    if home is None or away is None:
        # Repli sur `goals` : certains matchs anciens n'ont que celui-la.
        goals = fixture.get("goals") or {}
        home, away = goals.get("home"), goals.get("away")
    if home is None or away is None:
        return None
    return int(home), int(away)


def _is_friendly(league: dict[str, Any]) -> bool:
    if int(league.get("id") or 0) in FRIENDLY_LEAGUES:
        return True
    return "friendl" in str(league.get("name") or "").casefold()


def _summarize(fixtures: list[dict[str, Any]], team_id: int) -> list[dict[str, Any]]:
    """Reduit une saison de matchs a ce qu'un angle sportif utilise.

    C'est la seule interpretation faite a la collecte, et elle est assumee : la
    charge utile brute pese 43 ko pour 41 matchs — soit une base dix fois plus
    grosse, et des sauvegardes avec, pour des logos et des drapeaux. Ce qui est
    garde permet de tout recalculer : date, competition, cote, score a 90
    minutes, score a la pause, statut.
    """
    summary = []
    for fixture in fixtures:
        info = fixture.get("fixture") or {}
        league = fixture.get("league") or {}
        teams = fixture.get("teams") or {}
        home_id = (teams.get("home") or {}).get("id")
        if not info.get("date") or home_id is None:
            continue
        score = _score_90(fixture)
        halftime = (fixture.get("score") or {}).get("halftime") or {}
        summary.append(
            {
                "date": info["date"],
                "status": ((info.get("status") or {}).get("short")),
                "league_id": league.get("id"),
                "league": league.get("name"),
                "friendly": _is_friendly(league),
                "at_home": int(home_id) == team_id,
                "goals": list(score) if score else None,
                "halftime": [halftime.get("home"), halftime.get("away")]
                if halftime.get("home") is not None
                else None,
            }
        )
    return sorted(summary, key=lambda item: item["date"])


def _summarize_scorers(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Reduit `/players/topscorers` a ce qu'une props buteur utilise.

    Le drapeau `injured` du fournisseur est volontairement **ignore** : sa
    fraicheur est inconnue, alors que `/injuries` — deja appele, et par match —
    fait autorite sur les absents. Deux sources qui se contredisent dans le meme
    bloc valent moins qu'une seule.
    """
    scorers = []
    for row in rows:
        player = row.get("player") or {}
        stats = (row.get("statistics") or [{}])[0]
        team_id = (stats.get("team") or {}).get("id")
        goals = (stats.get("goals") or {}).get("total")
        if not player.get("id") or team_id is None or not goals:
            continue
        scorers.append(
            {
                "id": int(player["id"]),
                "name": player.get("name"),
                "team_id": int(team_id),
                "goals": int(goals),
                "penalties": ((stats.get("penalty") or {}).get("scored")) or 0,
                "played": ((stats.get("games") or {}).get("appearences")) or 0,
            }
        )
    return scorers


def _played(matches: Any) -> list[dict[str, Any]]:
    """Matchs officiels effectivement joues, dans l'ordre chronologique.

    Un amical, un report, une annulation et un forfait sur tapis vert n'ont rien
    a dire d'une equipe : les compter fausserait autant les buts que la serie.
    """
    if not isinstance(matches, list):
        return []
    return [
        match
        for match in matches
        if isinstance(match, dict)
        and not match.get("friendly")
        and match.get("status") in PLAYED_STATUSES
        and match.get("goals")
    ]


def _budget(planned: int, settings: Settings) -> str | None:
    """Motif de blocage si le plancher d'appels ne laisse pas passer, sinon None.

    Un quota inconnu laisse partir : c'est l'etat d'une installation qui n'a
    jamais appele le fournisseur, et le premier appel renseignera le compteur.
    """
    quota = last_known_quota(PROVIDER, settings)
    if not quota or quota["remaining"] is None:
        return None
    remaining = int(quota["remaining"]) - planned
    if remaining >= settings.apifootball_call_floor:
        return None
    return (
        f"dossier d'equipe suspendu : {planned} appel(s) laisseraient {remaining} "
        f"appels API-Football, sous le plancher de {settings.apifootball_call_floor}"
    )


async def refresh_event(
    client: APIFootballClient,
    event_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> DossierReport:
    """Met a jour le dossier des deux equipes d'un evenement.

    Ne recupere que ce qui est perime : deux matchs d'une meme equipe dans la
    semaine ne paient qu'une fois, et regenerer un prompt ne paie jamais.

    L'historique de la saison precedente n'est demande **que** si celle en cours
    ne dit rien encore. En aout, c'est la regle et non l'exception : une equipe y
    a joue quatre matchs, tous amicaux, et sa saison n'existe que dans la
    precedente.
    """
    settings = settings or get_settings()
    report = DossierReport()

    teams = teams_of(event_id, settings)
    team_ids = [int(teams[side]) for side in ("home", "away") if teams.get(side)]
    if not team_ids:
        return report
    season = int(teams["season"]) if teams.get("season") else None

    coverage = teams.get("coverage") if isinstance(teams.get("coverage"), dict) else {}

    todo: list[tuple[int, str, str]] = []
    for team_id in team_ids:
        if not _is_cached(team_id, KIND_COACH, "", report, settings, now, season):
            todo.append((team_id, KIND_COACH, ""))
        if season and not _is_cached(
            team_id, KIND_SEASON, str(season), report, settings, now, season
        ):
            todo.append((team_id, KIND_SEASON, str(season)))

    # Les buteurs se demandent une fois pour toute la competition, et seulement la
    # ou les props sont achetees : ailleurs, la ligne n'aurait aucun marche en face.
    league_id = int(teams["league"]) if teams.get("league") else None
    if (
        league_id
        and season
        and _props_league(event_id, settings)
        and coverage.get("top_scorers", True)
        and not _is_cached(league_id, KIND_SCORERS, str(season), report, settings, now, season)
    ):
        todo.append((league_id, KIND_SCORERS, str(season)))

    if not await _run(client, todo, report, settings, now):
        return report

    # La saison precedente ne se demande qu'une fois la courante connue : c'est
    # son contenu qui dit si elle suffit, et le savoir avant aurait demande
    # l'appel qu'on cherche justement a eviter.
    if season is None:
        return report
    fallback = [
        (team_id, KIND_SEASON, str(season - 1))
        for team_id in team_ids
        if _too_thin(team_id, season, settings)
        and not _is_cached(team_id, KIND_SEASON, str(season - 1), report, settings, now, season)
    ]
    await _run(client, fallback, report, settings, now)

    # Les indisponibilites se demandent **joueur par joueur**. Elles ne sont donc
    # cherchees que pour les buteurs effectivement rendus : au plus trois par
    # equipe, soit six appels par affiche, et jamais un effectif entier — les
    # trente-six joueurs d'une equipe en feraient soixante-douze.
    scorers = _scorers_of(event_id, league_id, season, settings)
    absents = [
        (player_id, KIND_SIDELINED, "")
        for player_id in _rendered_scorer_ids(scorers, team_ids)
        if not _is_cached(player_id, KIND_SIDELINED, "", report, settings, now, season)
    ]
    await _run(client, absents, report, settings, now)
    return report


def _rendered_scorer_ids(scorers: list[Any], team_ids: list[int]) -> list[int]:
    """Identifiants des buteurs que le bloc affiche, et d'eux seuls.

    Payer une indisponibilite pour un joueur qu'on ne nomme pas serait acheter une
    donnee que rien ne lira. Le classement est celui du rendu, une seule fois
    ecrit, pour que les deux ne puissent pas diverger.
    """
    return [
        int(player["id"])
        for team_id in team_ids
        for player in _ranked_scorers(scorers, team_id)
        if player.get("id")
    ]


async def _run(
    client: APIFootballClient,
    todo: list[tuple[int, str, str]],
    report: DossierReport,
    settings: Settings,
    now: datetime | None,
) -> bool:
    """Execute des releves perimes. Faux si le plancher d'appels les a retenus.

    Le plancher se juge sur le total a payer et non type par type : deux moities
    sous le plancher passeraient chacune leur tour et le franchiraient ensemble.
    """
    if not todo:
        return True
    blocked = _budget(len(todo) * CALL_COST, settings)
    if blocked:
        report.blocked_reason = blocked
        logger.warning(blocked)
        return False

    for subject_id, kind, scope in todo:
        try:
            payload = await _fetch_kind(client, subject_id, kind, scope)
        except ProviderError as exc:
            report.errors.append(f"{kind} : {exc}")
            continue
        _store_any(subject_id, kind, payload, scope, settings, now)
        if kind not in report.kinds:
            report.kinds.append(kind)
    return True


#: Types dont le sujet n'est **pas** une equipe. C'est le type qui dit ou le
#: releve se range : porter la distinction dans la liste des taches l'aurait fait
#: oublier a chaque nouvel appelant.
LEAGUE_KINDS = frozenset({KIND_SCORERS})
PLAYER_KINDS = frozenset({KIND_SIDELINED})


def _store_any(
    subject_id: int,
    kind: str,
    payload: Any,
    scope: str,
    settings: Settings,
    now: datetime | None,
) -> None:
    if kind in LEAGUE_KINDS:
        store_league(subject_id, kind, payload, scope, settings, now)
    elif kind in PLAYER_KINDS:
        store_player(subject_id, kind, payload, scope, settings, now)
    else:
        store(subject_id, kind, payload, scope, settings, now)


def _load_any(subject_id: int, kind: str, scope: str, settings: Settings) -> tuple[Any, str] | None:
    if kind in LEAGUE_KINDS:
        return load_league(subject_id, kind, scope, settings)
    if kind in PLAYER_KINDS:
        return load_player(subject_id, kind, scope, settings)
    return load(subject_id, kind, scope, settings)


async def _fetch_kind(client: APIFootballClient, subject_id: int, kind: str, scope: str) -> Any:
    """Recupere un type de releve, et le reduit s'il y a lieu."""
    if kind == KIND_COACH:
        return await client.coachs(subject_id)
    if kind == KIND_SEASON:
        return _summarize(await client.team_fixtures(subject_id, int(scope)), subject_id)
    if kind == KIND_SCORERS:
        return _summarize_scorers(await client.top_scorers(subject_id, int(scope)))
    if kind == KIND_SIDELINED:
        return await client.sidelined(subject_id)
    raise ValueError(f"type de dossier inconnu : {kind}")


def _is_cached(
    subject_id: int,
    kind: str,
    scope: str,
    report: DossierReport,
    settings: Settings,
    now: datetime | None,
    current_season: int | None = None,
) -> bool:
    """Vrai si le releve de ce sujet est encore frais. Le note au rapport."""
    known = _load_any(subject_id, kind, scope, settings)
    if known is None or not is_fresh(kind, known[1], now, ttl_for(kind, scope, current_season)):
        return False
    if kind not in report.cached:
        report.cached.append(kind)
    return True


def _props_league(event_id: int, settings: Settings) -> bool:
    """Vrai si les props buteurs sont achetees sur la competition de cet evenement.

    Ailleurs, aucun bookmaker ne les sert : payer les buteurs y ajouterait une
    ligne sans marche en face, et des tokens a chaque bloc.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT c.oddsapi_key FROM events e "
            "JOIN competitions c ON c.id = e.competition_id WHERE e.id = ?",
            (event_id,),
        ).fetchone()
    key = (row["oddsapi_key"] or "") if row else ""
    return key in settings.player_props_whitelist


def _too_thin(team_id: int, season: int, settings: Settings) -> bool:
    """Vrai si la saison en cours ne porte pas assez de matchs officiels joues."""
    known = load(team_id, KIND_SEASON, str(season), settings)
    if known is None:
        return False
    return len(_played(known[0])) < SEASON_MIN_MATCHES


# -- Rendu ------------------------------------------------------------------


def _current_post(entries: list[dict[str, Any]], team_id: int) -> dict[str, Any] | None:
    """Entraineur en poste dans cette equipe, et la date de sa prise de fonction.

    Le fournisseur peut rendre plusieurs entraineurs pour une equipe : le poste
    en cours est celui dont l'etape de carriere **dans cette equipe** n'a pas de
    date de fin. A defaut de le trouver, aucune ligne — nommer l'entraineur de
    l'an dernier serait pire qu'un silence, parce que ce serait affirme.

    **Ce depart n'a pas la portee qu'on lui prete, et c'est mesure** : sur les
    110 clubs en base, **92 ont plusieurs etapes ouvertes** chez eux. L'etape non
    refermee ne tranche donc que dans 15 % des cas, et le reste du temps c'est le
    depart le plus recent qui decide — heuristique juste plus souvent qu'aucune
    autre, mais heuristique.

    **Aucune regle ne peut rattraper une nomination absente.** Le champ `team` de
    tete est un simple echo de l'equipe interrogee, pas le club courant de
    l'entraineur : il n'existe aucun signal decisif dans la charge utile. Constate
    en reel — le bloc nommait R. Jans a Utrecht, parti depuis, alors que son
    successeur ne figurait **nulle part** dans la reponse. Le releve datait du
    matin meme : raccourcir `TTL_HOURS[KIND_COACH]` n'y changerait rien, et ce
    serait la fausse piste evidente. C'est le preambule qui porte la limite, en
    disant que la ligne est une piste et non un fait.
    """
    best: dict[str, Any] | None = None
    for entry in entries or []:
        if not isinstance(entry, dict) or not entry.get("name"):
            continue
        for step in entry.get("career") or []:
            if (step.get("team") or {}).get("id") != team_id or step.get("end"):
                continue
            candidate = {"name": entry["name"], "start": step.get("start")}
            # Deux postes ouverts sur la meme equipe : le plus recent est celui
            # qui compte, l'autre n'a jamais ete referme par le fournisseur.
            if best is None or str(candidate["start"] or "") > str(best["start"] or ""):
                best = candidate
    return best


def _tenure(start: str | None, reference: datetime | None) -> str:
    """`depuis 07/2024 (2 ans)` — la duree se lit d'un coup d'oeil, la date situe.

    Sans duree, il faudrait comparer mentalement a la date du jour ; sans date,
    « 3 mois » ne se verifierait pas. Une arrivee posterieure au match ne rend
    aucune duree : ce serait un nombre negatif presente comme une anciennete.
    """
    taken = _parse(start)
    if taken is None:
        return ""
    label = f"depuis {taken.strftime('%m/%Y')}"
    if reference is None:
        return label
    days = (reference.date() - taken.date()).days
    if days < 0:
        return label
    if days < COACH_RECENT_DAYS:
        months = max(days // 30, 0)
        return f"{label}, {months} mois" if months else f"{label}, ce mois-ci"
    years = days // 365
    if years >= 1:
        return f"{label}, {years} an" if years == 1 else f"{label}, {years} ans"
    return f"{label}, {days // 30} mois"


def _coach_fragment(
    team: str,
    team_id: int | None,
    reference: datetime | None,
    settings: Settings,
    observed: dict[str, Any] | None = None,
    sheets_read: bool = False,
) -> str:
    """`Estoril I. Cathro (depuis 07/2024, 2 ans — vu sur la feuille du 09/08)`.

    **Deux sources, et la ligne les montre toutes les deux.** La fiche `/coachs`
    dit qui est en poste, et le fournisseur ne referme pas ses etapes de
    carriere : mesure du 13/08/2026, **4 blocs faux sur 12**. La feuille de
    match, elle, dit qui etait sur le banc tel jour — c'est un releve, pas une
    fiche, et il est date.

    Elles ne sont donc pas arbitrees en silence. Un systeme qui « corrigerait »
    sans montrer aurait affirme Celades a Pafos avec une autorite imméritee : la
    feuille y dit Celades comme la fiche, et la realite est Sa Pinto. Ce que la
    ligne peut promettre est **la date du dernier releve**, jamais la verite.

    **Et leur accord n'est pas une corroboration.** `/coachs` et
    `/fixtures/lineups` sont deux endpoints, deux dates, deux chemins de
    collecte — et **un seul editeur**. Quand ils se trompent, ils se trompent
    ensemble, et c'est tout le cas Pafos. C'est la meme regle que celle qui
    gouverne les facteurs independants de la table de confiance : il faut deux
    origines qui puissent se tromper separement, et deux releves du meme
    fournisseur n'en font qu'une.

    **La mention ne parait que la ou les feuilles ont ete lues**, et c'est la
    meme discipline que les trois etats d'`Absents` : ces feuilles ne se
    telechargent que quand `/injuries` ne couvre pas la competition. Ecrire
    « non confirme » partout ailleurs le ferait paraitre sur chaque bloc bien
    couvert, ou la mention cesserait d'etre un signal pour devenir un decor —
    exactement le defaut des deux seuils egaux corrige au dossier du projet.

    **Angle mort a connaitre, et il est structurel** : cette condition est aussi
    celle qui rend le controle **inoperant sur les competitions bien couvertes**,
    donc sur les grands championnats, qui sont l'essentiel des lots hors coupes
    d'Europe. Le taux de 4 fiches fausses sur 12 est donc mesure sur un terrain
    non representatif — il dit ce qui se passe en qualification europeenne, pas
    en Ligue 1. Rien ne suggere que `/coachs` y soit plus fiable, c'est le meme
    endpoint ; rien ne suggere l'inverse non plus.

    Telecharger des feuilles pour ce seul champ serait un mauvais echange — deux
    appels par match pour verifier un nom. Le chiffre viendra gratuitement d'un
    lot ou les feuilles sont lues pour une autre raison.

    Quatre etats, dont trois n'existent que si l'on a regarde :

    - feuilles non lues : la fiche seule, comme avant, sans mention ;
    - lues et concordantes : la date de la feuille, **jamais le mot
      « confirme » — sur Pafos les deux sources disent Celades et la realite
      est Sa Pinto, donc l'accord de deux fiches perimees se presenterait
      comme une verification. Un fait date, et le lecteur juge son age ;
    - lues et divergentes : les deux noms, la feuille d'abord parce qu'elle est
      datee et plus recente ;
    - lues sans entraineur : la fiche seule, annoncee comme non confirmee.

    Et un cinquieme, hors de cette echelle : la fiche manque et la feuille non,
    auquel cas la feuille seule est rendue avec sa date — un releve date vaut
    mieux qu'un silence.
    """
    if not team_id:
        return ""
    known = load(int(team_id), KIND_COACH, settings=settings)
    post = (
        _current_post(known[0] if isinstance(known[0], list) else [], int(team_id))
        if known is not None
        else None
    )
    vu = (observed or {}).get("name")
    quand = _day(observed.get("seen")) if observed else ""

    if post is None:
        # La feuille seule vaut mieux que rien : c'est un releve date, la ou la
        # fiche absente ne dit rien du tout.
        return f"{team} {vu} (feuille du {quand})" if vu and quand else ""

    tenure = _tenure(post.get("start"), reference)
    fiche = f"{post['name']} ({tenure})" if tenure else str(post["name"])
    if not vu or not quand:
        return f"{team} {fiche} — non confirme" if sheets_read else f"{team} {fiche}"
    # **Sur l'identifiant, jamais sur le nom** : « D. McInnes » et
    # « Derek McInnes » sont deux libelles du meme homme, et les deux figurent
    # dans la fiche de Hearts. Un rapprochement par libelle aurait invente une
    # divergence la ou il n'y en a pas.
    etat = _coach_match(post, observed)
    if etat == COACH_SAME:
        return f"{team} {fiche} — vu sur la feuille du {quand}"
    if etat == COACH_INITIAL:
        # **Le nom de la feuille, parce qu'il est le plus complet** : elle donne
        # le prenom entier la ou la fiche l'abrege, et c'est ce prenom qui rend
        # une recherche possible. L'anciennete, elle, ne vient que de la fiche.
        complet = f"{vu} ({tenure})" if tenure else str(vu)
        return f"{team} {complet} — feuille du {quand}, apparié sur l'initiale du prénom"
    return f"{team} feuille du {quand} : {vu} | fiche : {fiche} — divergence"


#: Les deux sources nomment le meme homme, et c'est etabli — identifiant, ou
#: libelles identiques une fois casse et accents replies.
COACH_SAME = "same"
#: Elles sont **compatibles** sans qu'on puisse conclure : meme nom de famille,
#: prenom reduit a une initiale d'un cote. C'est le cas ordinaire, et le nommer
#: est tout l'objet du correctif — mais deux prenoms partageant l'initiale et le
#: nom sont deux hommes differents, et les fratries existent au football.
COACH_INITIAL = "initial"
#: Deux noms qui ne se rejoignent pas.
COACH_DIFFERENT = "different"


def _initials_match(fiche: str, vu: str) -> bool:
    """Un libelle abrege et un libelle complet designent-ils le meme nom ?

    « A. Blessin » et « Alexander Blessin » : meme nom de famille, et l'initiale
    du prenom concorde. Le rapprochement se fait sur les libelles **deja replies**
    — casse et accents — des deux cotes, sans quoi « N. Usaï » et « Nicolas Usai »
    resteraient deux hommes pour un trema.

    Le nom de famille se compare en **suffixe** et non token a token : « J.
    Machado Sacramento » et « João Pedro Machado Sacramento » portent deux
    prenoms d'un cote et un seul de l'autre, et exiger la meme longueur y
    inventerait une divergence.
    """
    for abrege, complet in ((fiche, vu), (vu, fiche)):
        tete, *reste = abrege.split()
        initiale = tete.rstrip(".")
        if len(initiale) != 1 or not reste:
            continue
        prenom, *nom = complet.split()
        if not nom or prenom.rstrip(".") == initiale:
            # Deux abreges, ou un complet sans nom de famille : rien a trancher
            # ici, l'egalite stricte l'a deja fait ou le fera pas.
            continue
        if prenom.startswith(initiale) and nom[-len(reste) :] == reste:
            return True
    return False


def _coach_match(post: dict[str, Any], observed: dict[str, Any] | None) -> str:
    """Ce que les deux sources permettent de dire du meme homme.

    L'identifiant tranche quand les deux le portent. La fiche `/coachs` n'expose
    pas le sien au niveau de l'etape de carriere retenue, d'ou le repli sur le
    libelle normalise — qui ne sert qu'a **eviter d'annoncer une divergence**,
    jamais a en affirmer une.

    **Trois etats, parce que le troisieme se produit tout le temps.** Mesure du
    14/08/2026 sur le lot du jour : 20 paires annoncees « divergence », dont
    **10 sont le meme homme** sous deux ecritures — « Laurent Guyot » contre
    « L. Guyot ». La comparaison stricte les declarait differents, et la ligne la
    plus decisive du dossier d'equipe se noyait dans son propre bruit.

    Mais un appariement sur une initiale **ne conclut pas** : deux prenoms
    partageant l'initiale et le nom sont deux hommes, et les fratries existent au
    football. La ligne dit alors ce sur quoi elle repose, au lieu de trancher —
    meme regle que partout, le cas indecidable se nomme.
    """
    identifiant, vu = post.get("id"), (observed or {}).get("id")
    if identifiant is not None and vu is not None:
        return COACH_SAME if int(identifiant) == int(vu) else COACH_DIFFERENT
    fiche = sort_key(str(post.get("name") or ""))
    observe = sort_key(str((observed or {}).get("name") or ""))
    if not fiche or not observe:
        return COACH_DIFFERENT
    if fiche == observe:
        return COACH_SAME
    return COACH_INITIAL if _initials_match(fiche, observe) else COACH_DIFFERENT


def _day(moment: Any) -> str:
    """`09/08` — la date d'une feuille, sans son heure ni son fuseau."""
    parsed = _parse(str(moment)) if moment else None
    return parsed.strftime("%d/%m") if parsed else ""


def _sheets_side(sheets: dict[str, Any], side: str) -> tuple[dict[str, Any] | None, bool]:
    """L'entraineur observe de ce cote, et **si l'on a seulement regarde**.

    Les deux se rendent ensemble parce qu'ils repondent a deux questions
    differentes : qui la feuille nomme, et s'il y a eu une feuille. Un releve
    absent parce que la competition est bien couverte par `/injuries` ne dit
    rien de l'entraineur ; un releve absent alors qu'on a lu quatre feuilles,
    si.
    """
    return sheets.get(f"{side}_coach"), bool(sheets)


def _history(
    team_id: int | None, season: int | None, settings: Settings
) -> tuple[list[dict[str, Any]], int] | None:
    """Historique retenu pour une equipe : ses matchs joues et la saison d'ou ils
    viennent.

    La saison en cours prime, la precedente sert de repli quand elle ne dit rien
    encore — le cas de tout le mois d'aout. La saison est rendue avec les matchs
    parce qu'elle doit etre **ecrite** : « 18/34 » sur la saison passee et sur la
    saison en cours ne se lisent pas pareil, et taire laquelle c'est laisser
    croire a la seconde.
    """
    if not team_id or season is None:
        return None
    for candidate in (season, season - 1):
        known = load(int(team_id), KIND_SEASON, str(candidate), settings)
        if known is None:
            continue
        matches = _played(known[0])
        if len(matches) >= SEASON_MIN_MATCHES:
            return matches, candidate
    return None


def _outcome(match: dict[str, Any]) -> str:
    """`V`, `N` ou `D` a 90 minutes, du point de vue de l'equipe du dossier."""
    home, away = match["goals"]
    ours, theirs = (home, away) if match.get("at_home") else (away, home)
    return "V" if ours > theirs else "D" if ours < theirs else "N"


def _goals_fragment(
    team: str, history: tuple[list[dict[str, Any]], int] | None, season: int
) -> str:
    """`Estoril >2.5 18/34, BTTS 20/34` — les deux plus gros marches achetes.

    Ce sont ici les buts **du match**, les deux equipes confondues, contrairement
    a la ligne « Buts marq. » qui ne compte que ceux de l'equipe. Deux lignes
    voisines et deux grandeurs differentes : le libelle doit les separer, et le
    template le dit.
    """
    if history is None:
        return ""
    matches, from_season = history
    over = sum(1 for match in matches if sum(match["goals"]) > 2.5)
    btts = sum(1 for match in matches if min(match["goals"]) >= 1)
    total = len(matches)
    fragment = f"{team} >2.5 {over}/{total}, BTTS {btts}/{total}"
    return fragment if from_season == season else f"{fragment} ({from_season})"


def _streak_fragment(
    team: str, history: tuple[list[dict[str, Any]], int] | None, season: int
) -> str:
    """`Estoril 3V` — la serie **en cours**, et non le record de la saison.

    `biggest.streak` de `/teams/satistics` donne le record, ce qui se lit comme la
    serie en cours et dit l'inverse : une equipe qui a gagne quatre fois en mars
    et perd depuis un mois y afficherait « 4 ».

    **Rien sur un repli de saison, et c'est ce qui la distingue de « Total
    buts ».** Cette derniere marque l'annee et reste lisible : une frequence sur
    trente-six matchs decrit encore un profil d'equipe. Une serie « en cours »,
    elle, est par definition une affirmation sur maintenant — datee de la saison
    passee, elle ne serait pas seulement perimee, elle serait fausse. Constate en
    reel : le bloc donnait « Cracovia Krakow 5N » quand la ligne « Forme 5 » juste
    au-dessus montrait un nul puis une **defaite** dans la nouvelle saison. Le
    repli ignore ces matchs-la — il se declenche justement parce qu'ils sont
    moins de `SEASON_MIN_MATCHES` — donc la serie qu'il decrit est demontrablement
    rompue.
    """
    if history is None:
        return ""
    matches, from_season = history
    if not matches or from_season != season:
        return ""
    last = _outcome(matches[-1])
    length = 0
    for match in reversed(matches):
        if _outcome(match) != last:
            break
        length += 1
    return f"{team} {length}{last}" if length >= STREAK_MIN else ""


def _next_fragment(
    team: str,
    team_id: int | None,
    season: int | None,
    league_id: int | None,
    commence: datetime | None,
    settings: Settings,
) -> str:
    """`Estoril dans 3j (Taca de Portugal)` — le prochain match, donc la rotation.

    C'est une des verifications que le prompt demande et que l'analyse allait
    chercher a la main, match par match. La competition n'est nommee que si elle
    differe de celle du jour : c'est le cas interessant — une coupe entre deux
    journees de championnat — et la repeter partout couterait des tokens pour ne
    rien apprendre.
    """
    if not team_id or season is None or commence is None:
        return ""
    known = load(int(team_id), KIND_SEASON, str(season), settings)
    if known is None or not isinstance(known[0], list):
        return ""
    later = sorted(
        (
            match
            for match in known[0]
            if isinstance(match, dict)
            and not match.get("friendly")
            # Un match reporte ou annule n'est pas une echeance a preparer, et un
            # match deja joue n'a rien a faire ici.
            and match.get("status") == "NS"
            and (_parse(match.get("date")) or commence) > commence
        ),
        key=lambda match: match["date"],
    )
    if not later:
        return ""
    upcoming = later[0]
    when = _parse(upcoming["date"])
    if when is None:
        return ""
    days = (when.date() - commence.date()).days
    # Le match analyse figure dans l'historique de sa propre equipe : sans ce
    # test il devient son propre « prochain match », annonce « dans 0j ».
    # Constate en reel sur une qualification europeenne, ou l'heure stockee par
    # le fournisseur etait posterieure de peu a celle de l'evenement. Aucune
    # equipe ne joue deux fois le meme jour : un ecart nul est toujours ce
    # doublon, jamais une echeance.
    if days < 1 or days > NEXT_MATCH_MAX_DAYS:
        return ""
    detail = ""
    if upcoming.get("league_id") and league_id and int(upcoming["league_id"]) != int(league_id):
        detail = f" ({upcoming.get('league') or ''})".replace(" ()", "")
    return f"{team} dans {days}j{detail}"


def _ranked_scorers(scorers: list[Any], team_id: int | None) -> list[dict[str, Any]]:
    """Buteurs d'une equipe retenus pour le rendu, du meilleur au moins bon.

    Ecrit une seule fois : le rendu et la recherche d'indisponibilite s'appuient
    dessus, et deux classements paralleles auraient fini par diverger — on aurait
    paye l'absence d'un joueur que le bloc ne nomme pas.
    """
    if not team_id:
        return []
    return sorted(
        (
            item
            for item in scorers
            if isinstance(item, dict)
            and int(item.get("team_id") or 0) == int(team_id)
            and int(item.get("goals") or 0) >= SCORERS_MIN_GOALS
        ),
        key=lambda item: int(item.get("goals") or 0),
        reverse=True,
    )[:SCORERS_KEEP]


def _sidelined_fragment(
    team: str,
    team_id: int | None,
    scorers: list[Any],
    commence: datetime | None,
    settings: Settings,
) -> str:
    """`BK Hacken L. Suárez absent depuis 12/07` — un buteur indisponible.

    **La date accompagne toujours l'absence, et jamais l'inverse.** Ce que le
    fournisseur publie est un historique de carriere : une periode sans date de
    fin dit qu'il ne l'a pas refermee, ce qui n'est pas tout a fait une absence
    en cours. Datee, la ligne se verifie — « depuis 12/07 » se recoupe en une
    recherche ; seche, « absent » serait une affirmation qu'on ne peut pas gager.

    Une periode refermee avant le match ne produit rien : le joueur est revenu.
    """
    if not team_id or commence is None:
        return ""
    absents = []
    for player in _ranked_scorers(scorers, team_id):
        known = load_player(int(player["id"]), KIND_SIDELINED, settings=settings)
        if known is None or not isinstance(known[0], list):
            continue
        started = _current_absence(known[0], commence)
        if started:
            absents.append(f"{player.get('name') or '?'} absent depuis {started}")
    return f"{team} {', '.join(absents)}" if absents else ""


def _current_absence(entries: list[Any], commence: datetime) -> str:
    """Date de debut de l'indisponibilite en cours au moment du match, sinon vide.

    En cours veut dire commencee avant le match et non refermee avant lui. La plus
    recente prime : un historique de carriere en compte des dizaines.
    """
    best: datetime | None = None
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        start = _parse(entry.get("start"))
        if start is None or start.date() > commence.date():
            continue
        end = _parse(entry.get("end"))
        if end is not None and end.date() < commence.date():
            continue
        if best is None or start > best:
            best = start
    return best.strftime("%d/%m") if best else ""


def _scorers_fragment(
    team: str,
    team_id: int | None,
    scorers: list[dict[str, Any]],
) -> str:
    """`BK Hacken Larsson 12b (3 pen), Nilsson 7b` — de quoi juger une props.

    La part de penaltys est dite parce qu'elle change la nature du pari : un
    attaquant a douze buts dont sept sur penalty ne marque pas de la meme facon
    que celui qui en met douze dans le jeu.

    Une equipe dont aucun joueur n'est dans les vingt meilleurs de la competition
    ne produit **aucune ligne** — et c'est une limite de l'endpoint, pas un
    silence sur l'equipe : la nommer sans buteur ferait croire qu'elle n'en a pas.
    Meme silence sous `SCORERS_MIN_GOALS` buts, ou l'ordre du classement ne
    decrit rien encore.
    """
    if not team_id:
        return ""
    ranked = _ranked_scorers(scorers, team_id)
    if not ranked:
        return ""
    listed = []
    for player in ranked:
        penalties = int(player.get("penalties") or 0)
        tail = f" ({penalties} pen)" if penalties else ""
        listed.append(f"{player.get('name') or '?'} {player['goals']}b{tail}")
    return f"{team} {', '.join(listed)}"


def _scorers_of(event_id: int, league_id: Any, season: int | None, settings: Settings) -> list[Any]:
    """Buteurs memorises pour la competition de cet evenement, s'ils s'y appliquent."""
    if not league_id or season is None or not _props_league(event_id, settings):
        return []
    known = load_league(int(league_id), KIND_SCORERS, str(season), settings)
    if known is None or not isinstance(known[0], list):
        return []
    return known[0]


def dossier_lines(
    event_id: int,
    home: str,
    away: str,
    commence_time: str,
    settings: Settings | None = None,
) -> list[tuple[str, str]]:
    """Lignes du dossier d'equipe, pretes pour `render_event`.

    Relues en base, sans aucun appel reseau : regenerer un prompt ne coute rien.
    Une equipe dont le dossier est vide ne produit aucune ligne — jamais un
    « inconnu », qui se lirait comme un fait sur l'equipe.
    """
    settings = settings or get_settings()
    teams = teams_of(event_id, settings)
    if not teams:
        return []

    reference = _parse(commence_time)
    season = int(teams["season"]) if teams.get("season") else None
    league_id = teams.get("league")
    home_id, away_id = teams.get("home"), teams.get("away")
    home_history = _history(home_id, season, settings)
    away_history = _history(away_id, season, settings)
    scorers = _scorers_of(event_id, league_id, season, settings)
    # Les feuilles de match sont rangees par evenement, la fiche d'entraineur
    # par equipe : c'est ici qu'elles se rejoignent, et nulle part ailleurs.
    feuilles = load_context(event_id, settings).get(KIND_SHEETS) or {}

    lines: list[tuple[str, str]] = []
    for label, fragments in (
        (
            "Entraineur",
            (
                _coach_fragment(
                    home, home_id, reference, settings, *_sheets_side(feuilles, "home")
                ),
                _coach_fragment(
                    away, away_id, reference, settings, *_sheets_side(feuilles, "away")
                ),
            ),
        ),
        (
            "Total buts",
            (
                _goals_fragment(home, home_history, season or 0),
                _goals_fragment(away, away_history, season or 0),
            ),
        ),
        (
            "Serie",
            (
                _streak_fragment(home, home_history, season or 0),
                _streak_fragment(away, away_history, season or 0),
            ),
        ),
        (
            "Buteurs",
            (
                _scorers_fragment(home, home_id, scorers),
                _scorers_fragment(away, away_id, scorers),
            ),
        ),
        (
            "Buteur abs.",
            (
                _sidelined_fragment(home, home_id, scorers, reference, settings),
                _sidelined_fragment(away, away_id, scorers, reference, settings),
            ),
        ),
        (
            "Calendrier",
            (
                _next_fragment(home, home_id, season, league_id, reference, settings),
                _next_fragment(away, away_id, season, league_id, reference, settings),
            ),
        ),
    ):
        rendered = " | ".join(fragment for fragment in fragments if fragment)
        if rendered:
            lines.append((label, rendered))
    return lines
