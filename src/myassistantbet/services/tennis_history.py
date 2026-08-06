"""Historique des matchs de tennis : collecte, rapprochement, rendu.

Meme decoupage en deux temps que le contexte football et que l'Elo :
`refresh()` telecharge et persiste, `lines()` relit la base. Regenerer un prompt
ne declenche aucun telechargement.

Le tennis n'avait aucune source de resultats. `tennis_load.py` sait dater les
apparitions d'un joueur dans un tournoi — a partir de nos propres scans — mais la
base ne stockait ni vainqueur, ni score, ni surface. On ne pouvait donc pas dire
si deux joueurs s'etaient deja affrontes, ni ce qu'un joueur vaut sur terre.

Le point dur n'est pas la collecte, c'est le **rapprochement des noms**. Le
fichier publie « Etcheverry T. M. » la ou The Odds API dit « Tomas Martin
Etcheverry » : ni le prenom entier, ni le decoupage prenom / nom ne sont donnes.
La regle retenue ne devine rien — elle essaie tous les decoupages et n'accepte
qu'une identite unique. Mesure sur 31 290 apparitions reelles et 143 joueurs de
la base : 141 rapproches, 2 refuses parce que reellement absents du fichier, et
**aucune attribution erronee**.
"""

from __future__ import annotations

import logging
import unicodedata
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError
from ..providers.tennisdata import TOURS, RawMatch, TennisDataClient

logger = logging.getLogger(__name__)

#: Saisons collectees, en partant de la plus recente. Trois suffisent : au-dela,
#: un resultat dit de moins en moins de l'etat actuel d'un joueur, et le premier
#: chargement double de volume pour un palmares qui n'eclaire plus le match.
SEASONS_KEPT = 3

#: Le fichier de la saison en cours est mis a jour une fois par semaine. Une
#: saison terminee ne changera plus : elle n'est jamais retelechargee.
CURRENT_SEASON_TTL_HOURS = 24 * 7

#: Suffixes de filiation, ecartes des deux cotes : le fichier ecrit « Damm M. »
#: la ou The Odds API dit « Martin Damm Jr. ».
NAME_SUFFIXES = frozenset({"jr", "jr.", "sr", "ii", "iii"})

#: Matchs retenus pour la ligne de forme.
FORM_LAST = 10

#: Fenetre du bilan par surface, en jours. Douze mois couvrent un cycle complet
#: de la saison : la terre battue ne revient qu'une fois par an.
SURFACE_DAYS = 365

#: Fenetre des abandons. Au-dela, un abandon ne dit plus rien de l'etat du jour.
RETIRED_DAYS = 180

#: Confrontations directes detaillees. Au-dela, la ligne devient un historique et
#: non un rapport de forces.
H2H_DETAIL = 3

#: Un match donne sur tapis vert n'a pas ete joue : il ne compte ni dans la
#: forme, ni dans un bilan de surface, ni dans une confrontation directe. Il
#: reste une information sur la disponibilite, portee par sa propre ligne.
WALKOVER = "walkover"
RETIRED = "retired"

#: Profondeur d'un tour, pour savoir jusqu'ou un joueur est alle. « Round Robin »
#: est la phase de poules du Masters : elle precede les demi-finales sans etre un
#: huitieme, d'ou le meme rang et un libelle distinct.
ROUND_RANKS = {
    "1st round": 1,
    "2nd round": 2,
    "3rd round": 3,
    "4th round": 4,
    "round robin": 4,
    "quarterfinals": 5,
    "semifinals": 6,
    "the final": 7,
}

#: Libelles francais des tours. La finale en a un — une confrontation directe se
#: situe dans le tableau — mais le palmares ne s'en sert pas : il rend
#: « vainqueur » ou « finaliste » selon l'issue, ce qu'un nom de tour ne dit pas.
ROUND_LABELS = {
    "1st round": "1er tour",
    "2nd round": "2e tour",
    "3rd round": "3e tour",
    "4th round": "1/8",
    "round robin": "poules",
    "quarterfinals": "1/4",
    "semifinals": "1/2",
    "the final": "finale",
}
FINAL = "the final"


@dataclass
class HistoryReport:
    """Ce qui a ete telecharge, et ce qui a manque."""

    seasons: list[str] = field(default_factory=list)
    cached: list[str] = field(default_factory=list)
    matches: int = 0
    errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.errors


# -- Identite d'un joueur ----------------------------------------------------


def _flat(text: str | None) -> str:
    """Sans accent ni casse, comme `labels.sort_key`."""
    decomposed = unicodedata.normalize("NFKD", text or "")
    return "".join(char for char in decomposed if not unicodedata.combining(char)).casefold()


def _tokens(name: str) -> list[str]:
    return [token for token in (name or "").split() if _flat(token.strip(".")) not in NAME_SUFFIXES]


def published_key(name: str) -> str:
    """Identite d'un nom **tel que le fichier le publie** : `etcheverry|tm`.

    Les jetons courts termines par un point sont les initiales, le reste est le
    nom de famille — qui peut en compter plusieurs (« Bautista Agut R. »).
    """
    initials: list[str] = []
    family: list[str] = []
    for token in _tokens((name or "").replace(".", ". ")):
        bare = token.strip(".")
        if len(bare) <= 2 and token.endswith("."):
            initials.append(bare)
        else:
            family.append(bare)
    return f"{_flat(' '.join(family))}|{_flat(''.join(initials))}"


def _candidates(full_name: str) -> list[tuple[str, str]]:
    """Tous les decoupages prenom(s) / nom d'un nom complet, sans en preferer un.

    « Alex de Minaur » a pour nom « de Minaur », « Juan Manuel Cerundolo » a pour
    prenoms « Juan Manuel » : rien dans la chaine ne dit lequel des deux cas on
    lit. Les essayer tous et n'accepter qu'un resultat unique evite de trancher a
    l'aveugle — c'est la meme prudence que `matching.py` applique aux clubs.
    """
    tokens = _tokens(full_name)
    candidates = []
    for cut in range(1, len(tokens)):
        initials = "".join(
            part[0] for given in tokens[:cut] for part in given.replace("-", " ").split() if part
        )
        candidates.append((_flat(" ".join(tokens[cut:])), _flat(initials)))
    return candidates


def known_keys(settings: Settings | None = None) -> dict[str, set[str]]:
    """Identites presentes en base, indexees par nom de famille.

    Une seule requete, quelques centaines de lignes : moins couteux que d'inventer
    une recherche par prefixe sur une cle composee.
    """
    index: dict[str, set[str]] = defaultdict(set)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT DISTINCT winner_key AS key FROM tennis_matches "
            "UNION SELECT DISTINCT loser_key AS key FROM tennis_matches"
        ).fetchall()
    for row in rows:
        family = str(row["key"]).split("|", 1)[0]
        index[family].add(str(row["key"]))
    return index


def resolve(full_name: str, index: dict[str, set[str]]) -> tuple[str, ...]:
    """Identite d'un joueur en base : une ou plusieurs cles du **meme** joueur.

    Plusieurs cles, parce que le fichier orthographie parfois le meme joueur de
    deux facons — « Etcheverry T. » et « Etcheverry T. M. » sont la meme personne,
    et neuf paires de ce genre existent dans les donnees reelles. Elles sont
    reunies quand le nom de famille est identique **et** que les initiales
    forment une chaine de prefixes.

    Vide des que le moindre doute subsiste : deux noms de famille differents, ou
    des initiales qui divergent (`a` et `m` pour les freres Zverev). Attribuer a
    un joueur l'historique d'un autre serait bien pire qu'une ligne absente, et
    il n'existe ici aucune resolution manuelle pour rattraper — meme regle que
    l'Elo.
    """
    found: set[str] = set()
    for family, initials in _candidates(full_name):
        for key in index.get(family, ()):
            candidate_initials = key.split("|", 1)[1]
            if candidate_initials.startswith(initials) or initials.startswith(candidate_initials):
                found.add(key)
    if not found:
        return ()
    families = {key.split("|", 1)[0] for key in found}
    if len(families) > 1:
        return ()
    chain = sorted((key.split("|", 1)[1] for key in found), key=len)
    if not all(longer.startswith(chain[0]) for longer in chain):
        return ()
    return tuple(sorted(found))


# -- Collecte ---------------------------------------------------------------


def seasons_for(reference: datetime | None = None) -> list[int]:
    """Saisons a collecter, la plus recente d'abord."""
    year = (reference or datetime.now(UTC)).year
    return [year - offset for offset in range(SEASONS_KEPT)]


def is_stale(
    tour: str, season: int, settings: Settings | None = None, now: datetime | None = None
) -> bool:
    """Vrai si une saison doit etre (re)telechargee.

    Une saison **terminee** ne change plus : une fois collectee, elle n'est jamais
    redemandee. Seule la saison en cours se rafraichit, a la cadence de mise a
    jour du fichier.

    La date lue est celle de la **collecte** et non celle des donnees. Deduire la
    peremption du `MAX(fetched_at)` des matchs tombe des qu'une saison n'en ramene
    aucun : sans ligne, pas de date, donc « jamais telecharge », donc redemandee a
    chaque enrichissement — sans fin. En janvier, le fichier de la saison qui
    commence est justement vide.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT fetched_at AS at FROM tennis_history_state WHERE tour = ? AND season = ?",
            (tour, season),
        ).fetchone()
    stamp = row["at"] if row else None
    if not stamp:
        return True
    reference = now or datetime.now(UTC)
    if season < reference.year:
        return False
    try:
        taken = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    except ValueError:
        return True
    if taken.tzinfo is None:
        taken = taken.replace(tzinfo=UTC)
    return reference - taken >= timedelta(hours=CURRENT_SEASON_TTL_HOURS)


def store(
    tour: str,
    season: int,
    matches: list[RawMatch],
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Ecrit une saison. Idempotent sur sa cle naturelle."""
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if now else utcnow()
    rows = [
        (
            tour,
            season,
            match.played_on,
            match.tournament,
            match.location,
            match.series,
            match.court,
            match.surface,
            match.round,
            match.winner,
            match.loser,
            published_key(match.winner),
            published_key(match.loser),
            match.score,
            match.comment,
            stamp,
        )
        for match in matches
    ]
    with connect(settings) as conn:
        conn.executemany(
            "INSERT INTO tennis_matches (tour, season, played_on, tournament, location, series, "
            "court, surface, round, winner, loser, winner_key, loser_key, score, comment, "
            "fetched_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (tour, season, played_on, winner_key, loser_key) DO UPDATE SET "
            "tournament = excluded.tournament, location = excluded.location, "
            "series = excluded.series, court = excluded.court, surface = excluded.surface, "
            "round = excluded.round, winner = excluded.winner, loser = excluded.loser, "
            "score = excluded.score, comment = excluded.comment, fetched_at = excluded.fetched_at",
            rows,
        )
        # La collecte est datee meme quand elle ne ramene rien : c'est la seule
        # chose qui repond a « faut-il redemander ? ».
        conn.execute(
            "INSERT INTO tennis_history_state (tour, season, matches, fetched_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT (tour, season) DO UPDATE SET "
            "matches = excluded.matches, fetched_at = excluded.fetched_at",
            (tour, season, len(rows), stamp),
        )
    return len(rows)


async def refresh(
    client: TennisDataClient,
    settings: Settings | None = None,
    now: datetime | None = None,
    force: bool = False,
) -> HistoryReport:
    """Telecharge les saisons manquantes ou perimees des deux circuits.

    Gratuit et sans quota : aucun garde-fou de credit ne s'y applique. Un echec
    sur un circuit n'empeche pas l'autre — le bloc perdra une ligne, comme avant.
    """
    settings = settings or get_settings()
    report = HistoryReport()
    for tour in TOURS:
        for season in seasons_for(now):
            label = f"{tour} {season}"
            if not force and not is_stale(tour, season, settings, now):
                report.cached.append(label)
                continue
            try:
                matches = await client.season(tour, season)
            except ProviderError as exc:
                report.errors.append(f"{label} : {exc}")
                logger.warning("Historique tennis indisponible pour %s : %s", label, exc)
                continue
            report.matches += store(tour, season, matches, settings, now)
            report.seasons.append(label)
    return report


# -- Lecture ----------------------------------------------------------------


@dataclass
class Match:
    """Un match joue, du point de vue du joueur interroge."""

    played_on: str
    tournament: str
    surface: str
    round: str
    won: bool
    score: str
    comment: str
    opponent: str

    @property
    def walkover(self) -> bool:
        return self.comment.casefold() == WALKOVER

    @property
    def retired(self) -> bool:
        return self.comment.casefold() == RETIRED


def _matches_of(keys: tuple[str, ...], since: str, until: str, settings: Settings) -> list[Match]:
    """Matchs d'un joueur sur une periode, du plus ancien au plus recent."""
    if not keys:
        return []
    marks = ", ".join("?" for _ in keys)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT played_on, tournament, surface, round, score, comment, winner_key, "
            "       winner, loser "
            f"FROM tennis_matches WHERE (winner_key IN ({marks}) OR loser_key IN ({marks})) "
            "AND played_on >= ? AND played_on < ? ORDER BY played_on",
            (*keys, *keys, since, until),
        ).fetchall()
    matches = []
    for row in rows:
        won = row["winner_key"] in keys
        matches.append(
            Match(
                played_on=row["played_on"],
                tournament=row["tournament"] or "",
                surface=row["surface"] or "",
                round=row["round"] or "",
                won=won,
                score=row["score"] or "",
                comment=row["comment"] or "",
                opponent=(row["loser"] if won else row["winner"]) or "",
            )
        )
    return matches


def _meetings(
    home_keys: tuple[str, ...], away_keys: tuple[str, ...], until: str, settings: Settings
) -> list[Match]:
    """Confrontations directes, du plus ancien au plus recent, vues du joueur A."""
    if not home_keys or not away_keys:
        return []
    home_marks = ", ".join("?" for _ in home_keys)
    away_marks = ", ".join("?" for _ in away_keys)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT played_on, tournament, surface, round, score, comment, winner_key, "
            "       winner, loser FROM tennis_matches WHERE played_on < ? AND ("
            f"(winner_key IN ({home_marks}) AND loser_key IN ({away_marks})) OR "
            f"(winner_key IN ({away_marks}) AND loser_key IN ({home_marks}))) ORDER BY played_on",
            (until, *home_keys, *away_keys, *away_keys, *home_keys),
        ).fetchall()
    return [
        Match(
            played_on=row["played_on"],
            tournament=row["tournament"] or "",
            surface=row["surface"] or "",
            round=row["round"] or "",
            won=row["winner_key"] in home_keys,
            score=row["score"] or "",
            comment=row["comment"] or "",
            opponent=(row["loser"] if row["winner_key"] in home_keys else row["winner"]) or "",
        )
        for row in rows
    ]


def _iso(moment: datetime) -> str:
    return moment.date().isoformat()


def _played(matches: list[Match]) -> list[Match]:
    """Matchs reellement joues : un tapis vert n'en est pas un."""
    return [match for match in matches if not match.walkover]


def _form_fragment(player: str, matches: list[Match]) -> str:
    """`Sinner VVVDVVVVDV/10` — du plus ancien au plus recent, comme au football.

    Le tennis n'avait aucune ligne de forme, la ou le football a « Forme 5 ». Le
    sens de lecture est celui de « Forme 5 » : la derniere lettre est le dernier
    match. L'inverser sur un seul des deux sports serait un piege a coup sur.
    """
    recent = _played(matches)[-FORM_LAST:]
    if not recent:
        return ""
    letters = "".join("V" if match.won else "D" for match in recent)
    return f"{player} {letters}/{len(recent)}"


def _surface_fragment(player: str, matches: list[Match], surface: str) -> str:
    """`Sinner dur 24V-4D/12m` — bilan sur la surface du tournoi.

    Rien n'est rendu sans surface renseignee sur la competition : la deduire du
    libelle d'un tournoi serait une invention, meme regle que pour l'Elo.
    """
    if not surface:
        return ""
    wanted = _flat(surface)
    kept = [match for match in _played(matches) if _flat(match.surface) == wanted]
    if not kept:
        return ""
    wins = sum(1 for match in kept if match.won)
    # La fenetre est ecrite : « 50V-5D » sur un an et sur trois ans ne disent pas
    # la meme chose, meme regle que le compte a cote d'une moyenne.
    months = SURFACE_DAYS // 30
    return f"{player} {SURFACE_LABELS.get(wanted, surface)} {wins}V-{len(kept) - wins}D/{months}m"


#: Surfaces telles que le fichier les nomme.
SURFACE_LABELS = {"hard": "dur", "clay": "terre", "grass": "gazon", "carpet": "moquette"}


def _retired_fragment(player: str, matches: list[Match]) -> str:
    """`Sinner 2 abandons (12/07, 03/06)` — l'etat physique que rien d'autre ne dit.

    Un abandon subi par l'adversaire n'en est pas un pour ce joueur : seul compte
    celui qui a abandonne, donc le perdant d'un match marque « Retired ». Un
    forfait est compte a part : le joueur n'est pas entre sur le court.
    """
    quit_dates = [match.played_on for match in matches if match.retired and not match.won]
    walkovers = [match.played_on for match in matches if match.walkover and not match.won]
    parts = []
    if quit_dates:
        dates = ", ".join(_short(day) for day in quit_dates[-3:])
        parts.append(f"{len(quit_dates)} abandon{'s' if len(quit_dates) > 1 else ''} ({dates})")
    if walkovers:
        parts.append(f"{len(walkovers)} forfait{'s' if len(walkovers) > 1 else ''}")
    return f"{player} {', '.join(parts)}" if parts else ""


def _short(day: str) -> str:
    """`2026-07-12` -> `12/07`. Pour ce qui est recent : le jour compte."""
    try:
        return datetime.fromisoformat(day).strftime("%d/%m")
    except ValueError:
        return day


def _month(day: str) -> str:
    """`2026-07-12` -> `07/26`. Pour une confrontation directe : l'annee compte.

    Deliberement different de `_short` : sur trois saisons, « 12/04 » et « 16/11 »
    ne se situent pas — deux rencontres a six mois d'ecart ou a dix-huit mois se
    lisent pareil. Le jour, lui, n'apprend rien sur un match d'il y a deux ans.
    """
    try:
        return datetime.fromisoformat(day).strftime("%m/%y")
    except ValueError:
        return day


def _h2h_line(home: str, away: str, meetings: list[Match]) -> tuple[str, str] | None:
    """`H2H (4)  Sinner 3-1 · 12/07 gazon 6-4 6-3 · …`, du plus recent d'abord.

    Le score en sets accompagne chaque rencontre : un 3-1 dont les trois victoires
    tiennent en trois sets serres ne decrit pas le meme rapport de forces qu'un
    3-1 en deux sets secs.
    """
    played = _played(meetings)
    if not played:
        return None
    wins = sum(1 for match in played if match.won)
    # `3V-7D` et non `3-7` : c'est la forme du reste du projet — `6V-1N-1D`,
    # `50V-5D` — et « 3-7 » se lirait comme un score de sets.
    fragments = [f"{home} {wins}V-{len(played) - wins}D"]
    for match in reversed(played[-H2H_DETAIL:]):
        surface = SURFACE_LABELS.get(_flat(match.surface), match.surface)
        score = f"{match.score} ab." if match.retired and match.score else match.score
        detail = " ".join(part for part in (_month(match.played_on), surface, score) if part)
        winner = home if match.won else away
        fragments.append(f"{detail} ({winner})")
    return (f"H2H ({len(played)})", " · ".join(fragments))


def lines(
    home: str,
    away: str,
    surface: str | None,
    commence_time: str,
    settings: Settings | None = None,
    competition_id: int | None = None,
) -> list[tuple[str, str]]:
    """Lignes d'historique tennis, pretes pour `render_event`.

    Relues en base, sans aucun telechargement. Un joueur que le rapprochement
    refuse ne produit aucune ligne — jamais un « non trouve », qui se lirait comme
    une information sur lui.
    """
    settings = settings or get_settings()
    index = known_keys(settings)
    if not index:
        # Base vierge : aucun historique n'a jamais ete telecharge. Ecrire
        # « aucun match connu » ferait chercher un probleme de rapprochement la
        # ou il n'y a qu'une collecte jamais lancee.
        return []

    try:
        start = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except ValueError:
        return []
    if start.tzinfo is None:
        start = start.replace(tzinfo=UTC)
    until = _iso(start)

    home_keys, away_keys = resolve(home, index), resolve(away, index)
    # Une seule requete par joueur, puis des fenetres decoupees en Python : les
    # lignes « ici » portent sur toutes les saisons collectees, la surface sur
    # douze mois et les abandons sur six. Trois requetes ne diraient rien de plus.
    everything = {
        home: _matches_of(home_keys, "", until, settings),
        away: _matches_of(away_keys, "", until, settings),
    }
    since_surface = _iso(start - timedelta(days=SURFACE_DAYS))
    since_retired = _iso(start - timedelta(days=RETIRED_DAYS))
    recent = {
        player: [match for match in matches if match.played_on >= since_surface]
        for player, matches in everything.items()
    }

    rendered: list[tuple[str, str]] = []
    meetings = _meetings(home_keys, away_keys, until, settings)
    meeting_line = _h2h_line(home, away, meetings)
    if meeting_line:
        rendered.append(meeting_line)

    names = tournament_names(competition_id, settings)
    if names:
        here = _h2h_here_fragment(home, away, _here(meetings, names))
        if here:
            rendered.append(("H2H ici", here))
        record = _pair(
            _record_here_fragment(home, _here(everything[home], names)),
            _record_here_fragment(away, _here(everything[away], names)),
        )
        if record:
            rendered.append(("Palmares", record))

    form = _pair(_form_fragment(home, recent[home]), _form_fragment(away, recent[away]))
    if form:
        rendered.append(("Forme", form))

    on_surface = _pair(
        _surface_fragment(home, recent[home], surface or ""),
        _surface_fragment(away, recent[away], surface or ""),
    )
    if on_surface:
        rendered.append(("Surface", on_surface))

    since = {
        player: [match for match in matches if match.played_on >= since_retired]
        for player, matches in recent.items()
    }
    quits = _pair(_retired_fragment(home, since[home]), _retired_fragment(away, since[away]))
    if quits:
        rendered.append(("Abandons", quits))
    return rendered


def _pair(home: str, away: str) -> str:
    return " | ".join(part for part in (home, away) if part)


# -- Ce qui s'est passe dans ce tournoi --------------------------------------


def tournament_names(competition_id: int | None, settings: Settings) -> tuple[str, ...]:
    """Noms du tournoi dans le jeu de donnees, pour cette competition.

    Vide quand la correspondance n'est pas renseignee : la source nomme les
    tournois par leur sponsor, et rien ne se deduit d'un libelle. Aucune ligne
    « ici » n'est alors rendue — plutot que d'en rendre une fausse.
    """
    if not competition_id:
        return ()
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT tennisdata_tournaments AS names FROM competitions WHERE id = ?",
            (competition_id,),
        ).fetchone()
    raw = (row["names"] if row else None) or ""
    return tuple(name.strip() for name in raw.split("|") if name.strip())


def _here(matches: list[Match], names: tuple[str, ...]) -> list[Match]:
    """Matchs joues dans ce tournoi, toutes editions confondues."""
    wanted = {_flat(name) for name in names}
    return [match for match in _played(matches) if _flat(match.tournament) in wanted]


def _best_result(matches: list[Match]) -> str:
    """`vainqueur 2025` ou `1er tour 2024` — le meilleur resultat et son annee.

    Le tour le plus profond atteint, par edition. Une finale gagnee vaut
    « vainqueur », perdue « finaliste » : le rang du tour ne suffit pas a le dire,
    et confondre les deux serait l'erreur la plus visible de la ligne.
    """
    best: tuple[int, str, bool, str] | None = None
    for match in matches:
        key = _flat(match.round)
        rank = ROUND_RANKS.get(key)
        if rank is None:
            continue
        season = match.played_on[:4]
        candidate = (rank, key, match.won, season)
        if best is None or candidate[0] > best[0] or (candidate[0] == best[0] and season > best[3]):
            best = candidate
    if best is None:
        return ""
    _, key, won, season = best
    if key == FINAL:
        return f"{'vainqueur' if won else 'finaliste'} {season}"
    return f"{ROUND_LABELS.get(key, key)} {season}"


def _record_here_fragment(player: str, matches: list[Match]) -> str:
    """`Sinner vainqueur 2025, 12V-2D` — le palmares, puis le bilan sur place."""
    if not matches:
        return ""
    wins = sum(1 for match in matches if match.won)
    best = _best_result(matches)
    bilan = f"{wins}V-{len(matches) - wins}D"
    return f"{player} {best}, {bilan}" if best else f"{player} {bilan}"


def _h2h_here_fragment(home: str, away: str, meetings: list[Match]) -> str:
    """`Sinner 1V-0D · 07/25 1/2` — se sont-ils deja croises **ici**.

    Le tour accompagne la date : un huitieme de finale et une finale dans le meme
    tournoi ne se valent pas, et c'est ce que la question « ici » cherche.
    """
    if not meetings:
        return ""
    wins = sum(1 for match in meetings if match.won)
    fragments = [f"{home} {wins}V-{len(meetings) - wins}D"]
    for match in reversed(meetings[-H2H_DETAIL:]):
        tour = ROUND_LABELS.get(_flat(match.round), match.round)
        winner = home if match.won else away
        fragments.append(f"{_month(match.played_on)} {tour} ({winner})")
    return " · ".join(fragments)
