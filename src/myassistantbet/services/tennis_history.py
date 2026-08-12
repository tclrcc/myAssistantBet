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
from datetime import UTC, date, datetime, timedelta
from statistics import median
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, utcnow
from ..providers.base import ProviderError
from ..providers.tennisdata import TOURS, RawMatch, TennisDataClient
from . import tennis_load, tennis_round

logger = logging.getLogger(__name__)

#: Saisons collectees, en partant de la plus recente. Trois suffisent : au-dela,
#: un resultat dit de moins en moins de l'etat actuel d'un joueur, et le premier
#: chargement double de volume pour un palmares qui n'eclaire plus le match.
SEASONS_KEPT = 3

#: Delai avant de redemander le fichier de la saison en cours. Une saison
#: terminee ne changera plus : elle n'est jamais retelechargee.
#:
#: **Une semaine etait la mauvaise cadence, et pas d'un peu.** Le fichier est
#: publie une fois par semaine mais **aucun jour connu** — il se remplit a mesure
#: que les tournois se terminent. Caler la relance sur notre propre derniere
#: collecte manquait donc une publication entiere : releve en reel le 8 aout,
#: l'historique s'arretait au 3 et n'aurait ete redemande que le 13, soit dix
#: jours de retard sur une source qui en accuse deja trois.
#:
#: Une tentative par jour colle a la cadence du travail planifie (`FREE_JOB_ID`)
#: et ne coute rien : 400 Ko par circuit, sans cle, sans quota, et hors de
#: `api_usage` comme tout ce qui ne consomme pas de credit.
CURRENT_SEASON_TTL_HOURS = 24

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

#: Matchs necessaires pour publier une forme de match — profil de jeux, marge,
#: niveau des adversaires. Meme raison que `PROFILE_MIN_MATCHES` au football :
#: une mediane sur deux matchs decrit une soiree, pas une tendance, et la lire
#: comme telle est pire que de ne rien lire. La collecte, elle, n'attend pas.
SHAPE_MIN_MATCHES = 5

#: Ecart, en jours, entre le dernier match collecte et le match analyse au-dela
#: duquel l'historique se declare en retard.
#:
#: Le fichier source est **hebdomadaire** et publie apres coup : le 8 aout, il
#: s'arretait au 3, si bien qu'aucun match du Canadian Open — commence le 4 —
#: n'existait en base. Toutes les lignes tirees de l'historique s'arretaient donc
#: avant le tournoi en cours **sans que rien ne le dise** : « Precedent » nommait
#: Los Cabos comme dernier tournoi de Lehecka alors qu'il jouait un huitieme ici,
#: et « Forme » ignorait ses deux victoires du tournoi. Le trou se lisait comme un
#: rapprochement rate, ce qu'il n'est pas.
#:
#: Deux jours, parce qu'un fichier frais accuse deja trois a quatre jours de
#: retard : en dessous, la ligne se rendrait sans qu'il manque rien.
HISTORY_LATE_DAYS = 2

#: Les tournois joues au meilleur des cinq sets. Le fichier ne le publie pas :
#: cote ATP, `series` vaut « Grand Slam » et c'est le seul format long du
#: circuit ; cote WTA la colonne est vide et tout se joue en trois sets.
LONG_FORMAT_SERIES = "grand slam"

#: Confrontations directes detaillees. Au-dela, la ligne devient un historique et
#: non un rapport de forces.
H2H_DETAIL = 3

#: Un match donne sur tapis vert n'a pas ete joue : il ne compte ni dans la
#: forme, ni dans un bilan de surface, ni dans une confrontation directe. Il
#: reste une information sur la disponibilite, portee par sa propre ligne.
WALKOVER = "walkover"
RETIRED = "retired"
#: Quatrieme valeur du champ, releve en base : **2 lignes sur 13 858**. Un match
#: « awarded » est un match donne sur decision — disqualification, defaut — donc
#: un score tronque a l'instant ou il s'arrete, exactement comme un abandon. Le
#: traiter en match complet faisait entrer ce score dans `Usure`, `Profil` et
#: `Marge`. L'effet est nul a deux lignes ; la regle, elle, ne l'est pas.
AWARDED = "awarded"

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
    #: Lignes ecartees parce que leur date ne peut pas etre celle de leur saison.
    #: Comptees a part des erreurs : le telechargement a reussi, c'est la source
    #: qui s'est trompee sur quelques lignes. Les taire ferait passer une coquille
    #: pour une absence.
    rejected: int = 0

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
    redemandee. Seule la saison en cours se rafraichit, une fois par jour — le
    fichier est publie chaque semaine mais aucun jour connu, et attendre une
    semaine depuis notre derniere collecte manquait une publication entiere.

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


def in_season(played_on: str, season: int) -> bool:
    """Vrai si cette date peut etre celle de cette saison.

    **La regle evidente serait fausse.** Exiger que l'annee de la date egale la
    saison jetterait des matchs bien reels : la saison de tennis ouvre dans les
    tout derniers jours de decembre, et le fichier 2025 porte 69 matchs joues du
    29 au 31 decembre 2024, celui de 2024 onze matchs du 31 decembre 2023. Une
    date vaut donc pour sa saison si elle tombe dans l'annee de la saison, ou en
    decembre de l'annee precedente.

    Ce qui deborde de l'autre cote ne peut etre qu'une coquille de la source : le
    fichier 2026 datait la finale de l'Iasi Open du 20 juillet **2029**. Le degat
    est invisible, et c'est ce qui le rend genant — une date posterieure a tout
    match analyse sort de **chaque** fenetre de lecture, puisque la forme, la
    surface et les confrontations filtrent toutes sur `played_on < debut du
    match`. Le match ne s'affiche jamais nulle part, et disparait de l'historique
    des deux joueuses sans qu'aucune ligne ne signale le trou.
    """
    try:
        day = date.fromisoformat(played_on)
    except (TypeError, ValueError):
        return False
    return day.year == season or (day.year == season - 1 and day.month == 12)


def store(
    tour: str,
    season: int,
    matches: list[RawMatch],
    settings: Settings | None = None,
    now: datetime | None = None,
) -> int:
    """Ecrit une saison. Idempotent sur sa cle naturelle.

    Les lignes dont la date ne peut pas etre celle de la saison sont ecartees et
    **dites** : une coquille de la source ne doit pas passer pour une absence.
    """
    stamp = now.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ") if now else utcnow()
    rejected = [match for match in matches if not in_season(match.played_on, season)]
    if rejected:
        logger.warning(
            "Historique tennis %s %s : %d ligne(s) hors saison ecartee(s) — %s",
            tour,
            season,
            len(rejected),
            ", ".join(f"{match.played_on} {match.tournament}" for match in rejected[:5]),
        )
    matches = [match for match in matches if in_season(match.played_on, season)]
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
            stored = store(tour, season, matches, settings, now)
            report.matches += stored
            report.rejected += len(matches) - stored
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
    tour: str = ""
    series: str = ""
    opponent_key: str = ""

    @property
    def walkover(self) -> bool:
        return self.comment.casefold() == WALKOVER

    @property
    def retired(self) -> bool:
        """Score tronque : abandon en cours de jeu, ou match donne sur decision.

        Les deux se ressemblent la ou ca compte — le score s'arrete avant la fin
        — et c'est le seul usage qu'on en fait : les ecarter des lignes qui
        mesurent la duree ou l'ecart de jeux.
        """
        return self.comment.casefold() in {RETIRED, AWARDED}

    @property
    def long_format(self) -> bool:
        """Vrai si le match s'est joue au meilleur des cinq sets.

        Un Grand Chelem masculin ne se compare a rien d'autre : quarante jeux y
        sont ordinaires quand vingt-deux le sont ailleurs. Il reste compte dans
        « Usure » — cinq sets fatiguent vraiment — et sort du profil de jeux,
        qui sert a lire un match en trois sets.
        """
        return self.tour.casefold() == "atp" and self.series.casefold() == LONG_FORMAT_SERIES


def _matches_of(keys: tuple[str, ...], since: str, until: str, settings: Settings) -> list[Match]:
    """Matchs d'un joueur sur une periode, du plus ancien au plus recent."""
    if not keys:
        return []
    marks = ", ".join("?" for _ in keys)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT played_on, tournament, surface, round, score, comment, winner_key, "
            "       loser_key, winner, loser, tour, series "
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
                tour=row["tour"] or "",
                series=row["series"] or "",
                opponent_key=(row["loser_key"] if won else row["winner_key"]) or "",
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


def _start(commence_time: str) -> datetime | None:
    """Coup d'envoi en UTC, ou None si la date est illisible.

    Toutes les fenetres se comptent depuis lui — jamais depuis « maintenant » :
    relire une fiche demain ne doit pas changer ce que le bloc disait.
    """
    try:
        moment = datetime.fromisoformat(commence_time.replace("Z", "+00:00"))
    except ValueError:
        return None
    return moment if moment.tzinfo else moment.replace(tzinfo=UTC)


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


def _games_fragment(player: str, matches: list[Match]) -> str:
    """`Sinner 20.4 jeux/match sur 10` — le temps passe sur le court, par procuration.

    Aucune source gratuite ne publie la **duree** d'un match : ni
    `tennis-data.co.uk`, qui ne sert que les scores, ni Tennis Abstract, dont
    les pages match sont interdites par son `robots.txt`. Le nombre de jeux en
    est le meilleur substitut disponible — un match en vingt jeux et un match en
    trente-huit ne laissent pas le meme joueur le lendemain.

    Les tapis verts sont exclus, comme partout, et les **abandons aussi** : leur
    score est tronque a l'instant ou le match s'arrete, donc les compter
    ferait passer un joueur qui a abandonne pour un joueur aux matchs courts.
    Ils ont deja leur ligne.

    **Les matchs au meilleur des cinq sets restent comptes, et leur nombre est
    dit.** Ils sont justes pour ce que cette ligne mesure — trente-neuf jeux
    fatiguent autant quel que soit le format, et c'est bien du temps passe sur le
    court. Ce qu'ils rendaient faux, c'est la **comparaison** : Lehecka affichait
    32.3 jeux/match contre 30.5 a Jodar, sans que rien ne dise que quatre de ses
    dix matchs etaient un Grand Chelem. Les retirer aurait efface une vraie
    fatigue ; les compter en silence faisait passer un joueur ordinaire pour un
    marathonien. Le compte tranche, et il ne coute que cinq caracteres.

    C'est l'arbitrage inverse de `Profil` et `Marge`, qui les ecartent : celles-la
    decrivent la forme d'un match en trois sets, pas une charge.
    """
    recent = [match for match in _played(matches) if not match.retired][-FORM_LAST:]
    kept = [match for match in recent if _games_in(match.score)]
    totals = [_games_in(match.score) for match in kept]
    if not totals:
        return ""
    longs = sum(match.long_format for match in kept)
    fragment = f"{player} {sum(totals) / len(totals):.1f} jeux/match sur {len(totals)}"
    return fragment + (f" ({longs} en 5 sets)" if longs else "")


def _games_in(score: str) -> int:
    """Jeux d'un match, reconstitues du score. Zero si le score est illisible.

    Le score est stocke tel qu'il a ete recompose des colonnes de sets —
    « 6-4 3-6 7-5 ». Un set dont un cote manque est ignore plutot que compte a
    moitie.
    """
    total = 0
    for manche in (score or "").split():
        parts = manche.split("-")
        if len(parts) != 2:
            continue
        try:
            total += int(parts[0]) + int(parts[1])
        except ValueError:
            continue
    return total


def _sets_in(score: str) -> list[tuple[int, int]]:
    """Les sets d'un score, **du point de vue du vainqueur du match**.

    Le score est stocke tel que le fichier le publie, gagnant d'abord : `6-4 3-6
    7-5` se lit toujours dans ce sens, quel que soit le joueur interroge. Un set
    dont un cote manque est ignore plutot que compte a moitie, comme dans
    `_games_in`.
    """
    sets: list[tuple[int, int]] = []
    for manche in (score or "").split():
        parts = manche.split("-")
        if len(parts) != 2:
            continue
        try:
            sets.append((int(parts[0]), int(parts[1])))
        except ValueError:
            continue
    return sets


def _shape_sample(matches: list[Match]) -> list[Match]:
    """Les matchs qui decrivent la **forme d'une rencontre en trois sets**.

    Meme fenetre que « Forme » et « Usure » — les dix derniers matchs joues,
    abandons exclus — puis les formats longs en sont retires. Filtrer avant de
    couper la fenetre irait chercher plus loin dans le passe et donnerait trois
    lignes portant sur trois periodes differentes ; le compte ecrit a cote dit
    combien de matchs ont ete gardes.
    """
    recent = [match for match in _played(matches) if not match.retired][-FORM_LAST:]
    return [match for match in recent if not match.long_format]


def _shape_fragment(player: str, matches: list[Match]) -> str:
    """`Fils med 22 jeux (18-31) · TB 4/8 · 2 sets 6/8` — la forme d'un match.

    « Usure » donne une moyenne, qui dit le temps passe sur le court ; elle ne
    dit pas si ce joueur produit des matchs serres ou des matchs a sens unique,
    et c'est la question des marches de jeux. La mediane et l'etendue la
    completent — un joueur dont le match le plus court fait vingt-trois jeux ne
    se lit pas comme un joueur qui oscille entre seize et trente.

    Le taux de tie-breaks est le meilleur substitut disponible au **style** :
    aucune source gratuite ne publie les statistiques de service, et un joueur
    qui tient son engagement produit des sets a 7-6. Les sets secs disent
    l'inverse — un tableau de breaks.
    """
    kept = _shape_sample(matches)
    if len(kept) < SHAPE_MIN_MATCHES:
        return ""
    totals, breaks, straight = [], 0, 0
    for match in kept:
        sets = _sets_in(match.score)
        if not sets:
            continue
        totals.append(sum(won + lost for won, lost in sets))
        breaks += any({won, lost} == {7, 6} for won, lost in sets)
        straight += len(sets) == 2
    if len(totals) < SHAPE_MIN_MATCHES:
        return ""
    return (
        f"{player} med {median(totals):.0f} jeux ({min(totals)}-{max(totals)})"
        f" · TB {breaks}/{len(totals)} · 2 sets {straight}/{len(totals)}"
    )


def _margin_fragment(player: str, matches: list[Match]) -> str:
    """`Fils +5.2 en V/6 · -4.1 en D/4` — l'ecart de jeux, marche par marche.

    C'est la grandeur du **handicap jeux**, et le bloc n'en portait aucune : on
    savait qui gagne, jamais de combien. Les deux sens sont separes parce qu'ils
    repondent a deux questions differentes — de combien il gagne quand il gagne,
    de combien il tombe quand il tombe. Melangees en une moyenne unique, elles
    s'annulent et un joueur regulier ressemble a un joueur irregulier.

    Le compte accompagne toujours la moyenne, meme regle que partout ailleurs.
    """
    won_by: list[int] = []
    lost_by: list[int] = []
    for match in _shape_sample(matches):
        sets = _sets_in(match.score)
        if not sets:
            continue
        margin = sum(won - lost for won, lost in sets)
        (won_by if match.won else lost_by).append(margin)
    if len(won_by) + len(lost_by) < SHAPE_MIN_MATCHES:
        return ""
    parts = []
    if won_by:
        parts.append(f"+{sum(won_by) / len(won_by):.1f} en V/{len(won_by)}")
    if lost_by:
        parts.append(f"-{sum(lost_by) / len(lost_by):.1f} en D/{len(lost_by)}")
    return f"{player} {' · '.join(parts)}"


def _level_fragment(
    player: str, matches: list[Match], ratings: dict[tuple[str, str], float]
) -> str:
    """`Fils Elo moy 1916/10 · meilleur battu 1994` — contre qui, au juste.

    Le fragment ne repete pas « adv. » : le libelle de la ligne le porte deja,
    et « Niveau adv. Fils adv. Elo moy » begayait.

    `VVVVVDDVDD` et `DDDDDDVVVD` ne se lisent pas pareil selon le niveau en
    face, et rien dans le bloc ne le disait : la ligne « Forme » traitait une
    victoire sur le 150e comme une victoire sur le 5e. L'Elo des adversaires est
    deja en base pour la ligne « Elo » et pour « Parcours » — ces deux lignes ne
    coutent donc aucun appel.

    Le « meilleur battu » est un fait, pas une categorie : ecrire « 3 victoires
    contre du top 20 » supposerait un seuil que rien ne fonde.
    """
    recent = [match for match in _played(matches) if not match.retired][-FORM_LAST:]
    faced = [
        (match, rating)
        for match in recent
        if (rating := ratings.get((match.tour.casefold(), match.opponent_key))) is not None
    ]
    if len(faced) < SHAPE_MIN_MATCHES:
        return ""
    average = sum(rating for _, rating in faced) / len(faced)
    beaten = [rating for match, rating in faced if match.won]
    fragment = f"{player} Elo moy {average:.0f}/{len(faced)}"
    return fragment + (f" · meilleur battu {max(beaten):.0f}" if beaten else "")


def horizon(tours: set[str], settings: Settings | None = None) -> str | None:
    """Date du match le plus recent collecte sur ces circuits.

    C'est jusqu'ou va l'historique, et donc jusqu'ou vont toutes les lignes qui
    en sortent. Le circuit fait partie de la question : l'ATP et la WTA ont deux
    fichiers, et l'un peut etre a jour quand l'autre ne l'est pas.
    """
    if not tours:
        return None
    marks = ", ".join("?" for _ in tours)
    with connect(settings) as conn:
        row = conn.execute(
            f"SELECT MAX(played_on) AS dernier FROM tennis_matches WHERE tour IN ({marks})",
            tuple(sorted(tours)),
        ).fetchone()
    return str(row["dernier"]) if row and row["dernier"] else None


def _collected_on(matches: list[Match], settings: Settings) -> date | None:
    """Date du dernier match collecte sur les circuits de ces joueurs."""
    last = horizon({match.tour.casefold() for match in matches if match.tour}, settings)
    if not last:
        return None
    try:
        return date.fromisoformat(last)
    except ValueError:
        return None


def _late_fragment(collected: date | None, start: datetime) -> str:
    """`dernier match connu le 03/08, soit 6j avant celui-ci` — ou rien.

    La ligne enonce un fait et **s'arrete la**. Elle ne dit pas « ce tournoi n'y
    figure pas » : ce serait faux d'un tournoi commence avant la date de
    collecte, et une affirmation fausse dans une ligne qui sert a douter est le
    pire endroit ou en mettre une. C'est « Fraicheur », juste en dessous, qui en
    tire la consequence — et elle la **compte** au lieu de la faire deviner.

    Ni apostrophe ni accent, comme toutes les valeurs rendues par ce module.
    Elles traversent un template Jinja pour la fiche d'un match, qui les echappe,
    et le test de parite fiche/prompt compare deux textes bruts.
    """
    if collected is None:
        return ""
    days = (start.date() - collected).days
    if days < HISTORY_LATE_DAYS:
        return ""
    return f"dernier match connu le {_short(collected.isoformat())}, soit {days}j avant celui-ci"


#: Les lignes que l'historique alimente, et qui s'arretent donc ou il s'arrete.
#: Nommees plutot que resumees : « les lignes ci-dessus » obligeait a remonter
#: le bloc pour savoir lesquelles, et la reponse decidait de leur credit.
STALE_LINES = "Forme/Usure/Profil/Marge/Niveau adv."


def _which(manquants: tennis_load.Uncounted) -> str:
    """`(tout le Parcours)` ou ` : Musetti, Lehecka` — jamais les deux.

    **Nommer les adversaires est la seule chose que le bloc ne dit nulle part
    ailleurs.** Le compte est sur cette ligne, la liste complete sur
    « Parcours » ; savoir *lesquels* manquent demandait de croiser les deux de
    tete, ce que ce projet cherche precisement a ne plus faire faire.

    Quand ils manquent tous, les nommer recopierait « Parcours » mot pour mot :
    trois mots suffisent alors, et ils disent la meme chose.
    """
    if manquants.whole_path:
        return " (tout le Parcours)"
    if not manquants.opponents:
        return ""
    return " : " + ", ".join(manquants.opponents)


def _freshness_line(
    home: str,
    away: str,
    competition_id: int | None,
    commence_time: str,
    collected: date | None,
    settings: Settings,
) -> tuple[str, str] | None:
    """Ligne « Fraicheur » : ce que l'historique ne compte pas encore.

    « Historique » disait jusqu'ou allait le jeu de donnees et « Parcours »
    nommait les adversaires du tournoi en cours : il fallait croiser les deux,
    de tete, pour comprendre que trois matchs de ce quart de finaliste
    n'entraient dans aucune des cinq lignes qui le decrivent. L'application sait
    les compter, donc elle les compte.

    Le rapprochement se fait sur la **journee de tournoi** : le fichier de
    resultats date un match du jour ou il se joue sur place, et une session du
    soir a Montreal part apres minuit a Paris.
    """
    if collected is None:
        return None
    fragments = []
    entrants = []
    for player in (home, away):
        if not player:
            continue
        manquants = tennis_load.played_since(
            player, competition_id, commence_time, collected, settings
        )
        if manquants.count:
            fragments.append(f"{player} {manquants.count} non comptes{_which(manquants)}")
            continue
        # **Deux silences, et ils ne se lisent pas pareil.** « Rien ne manque »
        # parce que tout est deja compte decrit l'etat du joueur maintenant ;
        # « rien ne manque » parce qu'il n'a encore rien joue ici decrit son
        # etat *avant* le tournoi. Meme motif que les trois etats de l'alerte
        # meteo et les quatre du lieu : un silence delibere et un silence vide
        # ne s'ecrivent pas de la meme facon.
        joues = len(tennis_load.load_for(player, competition_id, commence_time, settings).days)
        if joues:
            fragments.append(f"{player} {joues} ici, tous comptes")
        else:
            entrants.append(player)

    # « vu » et non « dispute » : nos scans sont la seule source, et le debut
    # d'un tableau peut leur echapper — c'est ce que dit la troisieme ligne
    # ci-dessous. Ecrire « aucun match dispute » affirmerait plus que ce que
    # nous savons, sur la ligne qui existe justement pour borner ce que nous
    # savons.
    if entrants:
        qui = "les deux joueurs entrent" if len(entrants) == 2 else f"{entrants[0]} entre"
        fragments.append(f"{qui} en lice — aucun match vu dans ce tournoi")

    detail = " | ".join(fragments) if fragments else "toutes les lignes a jour"
    rows = [f"{STALE_LINES} arretees au {_short(collected.isoformat())}", detail]
    if tennis_round.truncated(competition_id, commence_time, settings):
        # Le **nombre** de tours manquants n'est pas derivable : il demanderait
        # la taille du tableau, que rien ne donne. Le fait, lui, l'est — et la
        # fenetre de nos scans dit jusqu'ou remonte ce que nous avons pu voir.
        # « vu depuis le 04/08 » etait ambigu : premier jour du tournoi, ou
        # premier jour ou nous avons regarde ? Il a fallu le deviner.
        fenetre = tennis_load.scan_window(competition_id, settings)
        manque = "tours anterieurs non scannes — le debut du tableau nous echappe"
        rows.append(f"{manque} ({fenetre})" if fenetre else manque)
    return ("Fraicheur", "\n".join(rows))


def ratings_by_key(
    index: dict[str, set[str]],
    settings: Settings | None = None,
    cache: dict[str, Any] | None = None,
) -> dict[tuple[str, str], float]:
    """Elo de chaque identite du fichier de resultats, indexee par circuit et cle.

    `elo.lookup()` rapproche **un** nom complet ; ici il faut l'inverse, et pour
    des centaines d'adversaires a la fois : le fichier de resultats ne nomme
    qu'« Fritz T. ». On parcourt donc le classement une fois et on rapproche
    chaque joueur avec `resolve()`, la meme regle qu'ailleurs — elle refuse le
    moindre doute plutot que d'attribuer a un joueur le rating d'un autre.

    **Une cle que deux joueurs du classement se disputent est retiree.** Le cas
    ne s'observe pas aujourd'hui, mais garder le dernier arrive rendrait
    l'erreur silencieuse le jour ou deux homonymes apparaissent, et une ligne
    absente vaut mieux qu'une ligne fausse.
    """
    # Ce rapprochement porte sur le **classement entier**, identique pour tous
    # les matchs d'un lot : il resout treize mille noms par evenement, et c'est
    # ce qui rendait la shortlist lente des que la densite l'a fait tourner sur
    # chaque ligne. Le cache est fourni par l'appelant et vit le temps d'un lot —
    # pas de memo global, dont l'invalidation apres un rafraichissement d'Elo
    # serait a inventer.
    if cache is not None and "ratings" in cache:
        return cache["ratings"]

    found: dict[tuple[str, str], set[float]] = defaultdict(set)
    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT tour, player, elo FROM tennis_elo WHERE elo IS NOT NULL"
        ).fetchall()
    for row in rows:
        for key in resolve(row["player"], index):
            found[(str(row["tour"]).casefold(), key)].add(float(row["elo"]))
    ratings = {key: next(iter(values)) for key, values in found.items() if len(values) == 1}
    if cache is not None:
        cache["ratings"] = ratings
    return ratings


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
    cache: dict[str, Any] | None = None,
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

    start = _start(commence_time)
    if start is None:
        return []
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

    # En tete des lignes qu'elle qualifie : toutes celles qui suivent sortent de
    # l'historique, et s'arretent donc ou il s'arrete. La taire faisait lire un
    # tournoi manquant comme un rapprochement rate.
    collected = _collected_on(everything[home] + everything[away], settings)
    late = _late_fragment(collected, start)
    if late:
        rendered.append(("Historique", late))
        # Ce que ce retard coute **en matchs**, joueur par joueur. Le compte
        # dormait dans nos propres scans : les tours precedents du tournoi ont
        # ete vus les jours d'avant. Aucun appel.
        fraicheur = _freshness_line(home, away, competition_id, commence_time, collected, settings)
        if fraicheur:
            rendered.append(fraicheur)

    meetings = _meetings(home_keys, away_keys, until, settings)
    meeting_line = _h2h_line(home, away, meetings)
    if meeting_line:
        rendered.append(meeting_line)
    elif home_keys and away_keys:
        # Deux joueurs rapproches sans aucun match joue : **le dire**. Omettre la
        # ligne rend l'absence indiscernable d'un rapprochement rate, et envoie
        # chercher un H2H qui n'existe pas.
        #
        # « aucun match joue » et non « jamais rencontres » : le second serait faux
        # quand leur seule rencontre a ete un forfait — ils ont bien ete tires l'un
        # contre l'autre, personne n'est entre sur le court. Et la periode est
        # ecrite parce que nos donnees commencent trois saisons en arriere : sans
        # elle, la ligne affirmerait quelque chose sur toute leur carriere.
        rendered.append(("H2H", f"aucun match joue depuis {min(seasons_for(start))}"))

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

    # Le niveau des adversaires suit immediatement la forme : c'est elle qu'il
    # corrige. Une seule lecture du classement sert les deux joueurs.
    ratings = ratings_by_key(index, settings, cache)
    level = _pair(
        _level_fragment(home, recent[home], ratings),
        _level_fragment(away, recent[away], ratings),
    )
    if level:
        rendered.append(("Niveau adv.", level))

    games = _pair(_games_fragment(home, recent[home]), _games_fragment(away, recent[away]))
    if games:
        rendered.append(("Usure", games))

    shape = _pair(_shape_fragment(home, recent[home]), _shape_fragment(away, recent[away]))
    if shape:
        rendered.append(("Profil", shape))

    margin = _pair(_margin_fragment(home, recent[home]), _margin_fragment(away, recent[away]))
    if margin:
        rendered.append(("Marge", margin))

    if names:
        last = _pair(
            _last_tournament_fragment(home, recent[home], names),
            _last_tournament_fragment(away, recent[away], names),
        )
        if last:
            # **« connu » n'est pas un adverbe de prudence, c'est le fait.** Le
            # bloc a servi « Mark Lajal 1er tour Nordic Open (dur, 14/10) » pour
            # un match du 12/08, soit dix mois sans competition — donc un retour
            # apres coupure, l'un des faits que ce prompt designe comme
            # debloquant les paliers hauts. Verification : le joueur avait joue
            # Roland-Garros, Birmingham et Wimbledon entre les deux. C'etait un
            # trou de couverture presente comme un fait, et sur un joueur dont
            # « Forme » ne portait qu'un match, rien ne l'en distinguait.
            #
            # La borne, elle, est deja dans le bloc : « Historique » dit ou
            # s'arrete le jeu de donnees, deux lignes plus haut. La repeter ici
            # couterait des tokens pour la meme information.
            rendered.append(("Precedent", f"dernier connu : {last}"))

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


def _last_tournament_fragment(player: str, matches: list[Match], exclude: tuple[str, ...]) -> str:
    """`Blockx finaliste Estoril Open (terre, 26/07)` — le tournoi d'avant.

    C'est le fait que la ligne « Forme » detruit. Blockx affiche `dur 2V-3D/12m`,
    ce qui se lit comme un joueur faible ; il sort en realite d'une **finale sur
    terre battue** la semaine derniere, et arrive donc en confiance sur une surface
    qui n'est pas la sienne. Dix lettres V/D ne peuvent pas dire cela.

    Le tournoi en cours est **exclu** : sur un match de deuxieme tour, le dernier
    tournoi joue est celui-la meme, et la ligne repeterait « 1er tour » sans rien
    apprendre. C'est pourquoi elle depend du rattachement du tournoi, comme les
    lignes « ici ».
    """
    ignores = {_flat(name) for name in exclude}
    passes = [match for match in matches if _flat(match.tournament) not in ignores]
    if not passes:
        return ""
    dernier = passes[-1].tournament
    lot = [match for match in passes if match.tournament == dernier]
    resultat = _best_result(lot)
    if not resultat:
        return ""
    # `_best_result` porte l'annee ; ici le tournoi est recent, la date parle mieux.
    resultat = resultat.rsplit(" ", 1)[0]
    surface = SURFACE_LABELS.get(_flat(lot[-1].surface), lot[-1].surface)
    quand = _short(lot[-1].played_on)
    detail = ", ".join(part for part in (surface, quand) if part)
    return f"{player} {resultat} {dernier} ({detail})"


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


def recent_matches(
    home: str,
    away: str,
    commence_time: str,
    settings: Settings | None = None,
    limit: int = FORM_LAST,
) -> list[tuple[str, list[Match]]]:
    """Derniers matchs joues par chaque joueur, du plus recent au plus ancien.

    Pour la **fiche** d'un match, pas pour le prompt : dix rencontres par joueur
    avec adversaire, score, tournoi, surface et tour coutent cinq cents caracteres
    par bloc, et le prompt compte ses tokens. L'ecran, lui, n'a pas de budget — et
    c'est la que la ligne « Forme » montre sa limite : `VVDVDDVVVD` ne dit pas que
    les trois defaites viennent d'une finale perdue sur une autre surface.

    Un joueur non rapproche rend une liste vide, jamais une erreur.
    """
    settings = settings or get_settings()
    index = known_keys(settings)
    if not index:
        return []
    start = _start(commence_time)
    if start is None:
        return []
    until = _iso(start)
    return [
        (player, _played(_matches_of(resolve(player, index), "", until, settings))[-limit:][::-1])
        for player in (home, away)
    ]
