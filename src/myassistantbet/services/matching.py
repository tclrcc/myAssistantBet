"""Correspondance entre les noms d'equipes The Odds API et API-Football.

Les deux fournisseurs n'utilisent ni les memes identifiants ni les memes noms.
La resolution se fait en trois temps (SPEC.md section 5) :

1. table d'alias persistante — une resolution manuelle vaut pour toujours ;
2. normalisation (minuscules, accents retires, suffixes de club retires) puis
   distance de Levenshtein avec seuil ;
3. en cas de doute, **on ne devine pas** : l'evenement est marque `mapping_pending`
   et l'UI propose une resolution manuelle.
"""

from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass

from ..config import Settings
from ..db import connect, utcnow

logger = logging.getLogger(__name__)

#: Similarite minimale pour accepter une correspondance sans confirmation humaine.
MIN_SCORE = 0.85
#: Ecart minimal avec le second candidat. Deux clubs trop proches (« Manchester
#: United » / « Manchester City ») doivent passer par une resolution manuelle.
MIN_GAP = 0.08
#: Nombre de candidats memorises pour le formulaire de resolution manuelle.
CANDIDATES_KEPT = 8

#: Jetons de club retires en tete ou en fin de nom. Jamais au milieu : cela
#: distinguerait mal « Manchester United » de « Manchester City ».
CLUB_TOKENS = frozenset(
    {
        "fc",
        "cf",
        "sc",
        "ac",
        "as",
        "ss",
        "us",
        "sv",
        "fk",
        "gf",
        "ik",
        "if",
        "ifk",
        "bk",
        "sk",
        "aik",
        "cd",
        "ud",
        "rc",
        "rcd",
        "ca",
        "afc",
        "cfc",
        "club",
        "calcio",
        "futbol",
        "football",
        "sporting",
        "spor",
        "kulubu",
        "sd",
        "fsv",
        "vfl",
        "vfb",
        "tsg",
        "bsc",
        "ff",
        "gif",
        "bik",
        "aif",
        "kf",
        "nk",
        "hk",
        "ks",
        "cs",
    }
)


#: Derniers mots qui designent une equipe **reserve** et non la premiere. « B »
#: et « II » disent la meme chose chez deux fournisseurs differents, d'ou un
#: statut et non un marqueur litteral : `Real Sociedad B` et `Real Sociedad II`
#: sont bien le meme club, et les separer couperait un rapprochement juste.
RESERVE_TOKENS = frozenset({"ii", "iii", "b", "2", "u19", "u21", "u23", "reserve", "reserves"})


def is_reserve(name: str) -> bool:
    """Vrai si le nom designe une equipe reserve.

    Le marqueur se lit **en fin de nom seulement** : « Willem II » est une
    premiere equipe dont le chiffre fait partie du nom — il se lit reserve des
    deux cotes, donc il concorde, donc il passe. C'est la concordance qui decide,
    jamais le statut pris seul.
    """
    mots = re.sub(r"[^a-z0-9\s]", " ", name.lower()).split()
    return bool(mots) and mots[-1] in RESERVE_TOKENS


def reserve_mismatch(oddsapi_name: str, apifootball_name: str) -> bool:
    """Vrai si l'un des deux noms designe une reserve et l'autre non.

    **Un rattachement qui traverse ce statut est une erreur, et il est
    definitif.** Mesure du 22/08/2026 sur les 517 alias en base : **un seul**
    traverse le statut, et c'est le defaut — `Celta Vigo` rattache manuellement a
    `Celta de Vigo II`, la reserve. Consequence : Valencia - Celta Vigo, servi
    par les deux fournisseurs et apparie a 1.00 des deux cotes, restait
    introuvable — `_find_fixture` cherchait Valencia contre la reserve.

    Les deux autres cas que la base porte sont concordants et ne bougent pas :
    `Real Sociedad B` vers `Real Sociedad II`, deux ecritures de la meme reserve,
    et `Willem II` vers `Willem II`. Zero faux positif mesure.

    Le score ne suffisait pas a l'arreter : `celta vigo` contre `celta de vigo ii`
    vaut 0.62, donc `is_confident` refusait deja de le poser tout seul. C'est un
    **choix manuel** qui l'a pose, depuis une liste de candidats ou la reserve
    etait le meilleur des mauvais. La regle vit donc a l'ecriture, seul endroit
    que les deux chemins traversent.
    """
    return is_reserve(oddsapi_name) != is_reserve(apifootball_name)


class ReserveMismatch(ValueError):
    """Un rattachement qui lie une premiere equipe a une reserve, ou l'inverse."""


@dataclass
class Candidate:
    """Une equipe API-Football envisagee pour un nom The Odds API."""

    apifootball_id: int
    apifootball_name: str
    score: float


@dataclass
class Resolution:
    """Resultat d'une tentative de resolution."""

    oddsapi_name: str
    matched: Candidate | None
    candidates: list[Candidate]
    from_alias: bool = False

    @property
    def resolved(self) -> bool:
        return self.matched is not None


def normalize(name: str) -> str:
    """Minuscules, accents retires, ponctuation retiree, jetons de club retires."""
    decomposed = unicodedata.normalize("NFKD", name)
    stripped = "".join(char for char in decomposed if not unicodedata.combining(char))
    cleaned = re.sub(r"[^a-z0-9\s]", " ", stripped.lower())
    tokens = cleaned.split()

    # Retrait iteratif aux deux extremites : « 1. FC Köln » perd « 1 » puis « fc »,
    # « Schalke 04 » perd « 04 ». Les millesimes et numeros d'ordre ne distinguent
    # jamais deux clubs d'une meme competition.
    def _droppable(token: str) -> bool:
        return token in CLUB_TOKENS or token.isdigit()

    while tokens and _droppable(tokens[0]):
        tokens.pop(0)
    while tokens and _droppable(tokens[-1]):
        tokens.pop()

    # Un nom entierement compose de jetons de club (« AIK ») reste lui-meme.
    return " ".join(tokens) if tokens else " ".join(cleaned.split())


def levenshtein(left: str, right: str) -> int:
    """Distance d'edition, implementee ici pour ne pas ajouter de dependance."""
    if left == right:
        return 0
    if not left:
        return len(right)
    if not right:
        return len(left)

    previous = list(range(len(right) + 1))
    for i, left_char in enumerate(left, start=1):
        current = [i]
        for j, right_char in enumerate(right, start=1):
            current.append(
                min(
                    previous[j] + 1,
                    current[j - 1] + 1,
                    previous[j - 1] + (left_char != right_char),
                )
            )
        previous = current
    return previous[-1]


def similarity(left: str, right: str) -> float:
    """Score de 0 a 1 entre deux noms normalises."""
    if not left or not right:
        return 0.0
    if left == right:
        return 1.0
    longest = max(len(left), len(right))
    return 1.0 - levenshtein(left, right) / longest


def score_candidates(oddsapi_name: str, teams: list[tuple[int, str]]) -> list[Candidate]:
    """Classe les equipes API-Football par similarite decroissante."""
    needle = normalize(oddsapi_name)
    scored = [
        Candidate(
            apifootball_id=team_id,
            apifootball_name=team_name,
            score=round(similarity(needle, normalize(team_name)), 4),
        )
        for team_id, team_name in teams
    ]
    return sorted(scored, key=lambda item: (-item.score, item.apifootball_name))


def is_confident(candidates: list[Candidate]) -> bool:
    """Vrai si le meilleur candidat est a la fois bon et nettement detache."""
    if not candidates:
        return False
    best = candidates[0]
    if best.score < MIN_SCORE:
        return False
    if len(candidates) == 1:
        return True
    return best.score - candidates[1].score >= MIN_GAP or best.score == 1.0


def lookup_alias(oddsapi_name: str, settings: Settings | None = None) -> Candidate | None:
    """Correspondance deja etablie pour ce nom, ou None."""
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT apifootball_id, apifootball_name FROM team_aliases WHERE oddsapi_name = ?",
            (oddsapi_name,),
        ).fetchone()
    if row is None:
        return None
    return Candidate(
        apifootball_id=int(row["apifootball_id"]),
        apifootball_name=row["apifootball_name"],
        score=1.0,
    )


def save_alias(
    oddsapi_name: str,
    apifootball_id: int,
    apifootball_name: str,
    source: str = "auto",
    settings: Settings | None = None,
) -> None:
    """Memorise une correspondance. Une resolution manuelle ecrase l'automatique.

    **Refuse un rattachement qui traverse le statut de reserve** — voir
    `reserve_mismatch`. C'est le seul refus de ce module, et il porte sur le seul
    geste que rien d'autre ne rattrape : un alias est definitif, et il n'existait
    aucune surface pour en corriger un faux.
    """
    if reserve_mismatch(oddsapi_name, apifootball_name):
        raise ReserveMismatch(
            f"{oddsapi_name!r} et {apifootball_name!r} ne designent pas la meme equipe : "
            "l'un est une reserve, l'autre non"
        )
    with connect(settings) as conn:
        conn.execute(
            "INSERT INTO team_aliases (oddsapi_name, apifootball_id, apifootball_name, "
            "                          source, created_at) VALUES (?, ?, ?, ?, ?) "
            "ON CONFLICT(oddsapi_name) DO UPDATE SET "
            "  apifootball_id = excluded.apifootball_id, "
            "  apifootball_name = excluded.apifootball_name, "
            "  source = excluded.source",
            (oddsapi_name, apifootball_id, apifootball_name, source, utcnow()),
        )
    logger.info("Alias %s : %r -> %s (%s)", source, oddsapi_name, apifootball_id, apifootball_name)


def resolve_team(
    oddsapi_name: str,
    teams: list[tuple[int, str]],
    settings: Settings | None = None,
    *,
    remember: bool = True,
) -> Resolution:
    """Resout un nom d'equipe. N'invente jamais une correspondance douteuse."""
    alias = lookup_alias(oddsapi_name, settings)
    if alias is not None:
        return Resolution(oddsapi_name, alias, [alias], from_alias=True)

    candidates = score_candidates(oddsapi_name, teams)
    kept = candidates[:CANDIDATES_KEPT]

    if is_confident(candidates):
        best = candidates[0]
        if remember:
            save_alias(oddsapi_name, best.apifootball_id, best.apifootball_name, "auto", settings)
        return Resolution(oddsapi_name, best, kept)

    logger.info(
        "Mapping incertain pour %r (meilleur score %.2f)",
        oddsapi_name,
        candidates[0].score if candidates else 0.0,
    )
    return Resolution(oddsapi_name, None, kept)
