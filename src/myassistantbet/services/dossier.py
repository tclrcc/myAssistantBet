"""Dossier d'equipe : ce qui vaut pour une equipe et non pour une rencontre.

Meme decoupage en deux temps que `services/context.py`, et pour la meme raison :

- `refresh_event()` appelle API-Football et **persiste les charges utiles brutes**
  dans `team_context` ;
- `dossier_lines()` relit la base et produit les lignes du bloc CONTEXTE.

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
from .context import KIND_TEAMS
from .context import load as load_context

logger = logging.getLogger(__name__)

KIND_COACH = "coach"

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
TTL_HOURS = {KIND_COACH: 24 * 7}

#: Sous cette anciennete, l'arrivee est un fait de la saison en cours et pas une
#: ligne d'etat civil : trois mois, soit le delai au-dela duquel une equipe n'est
#: plus « celle du nouvel entraineur ».
COACH_RECENT_DAYS = 90


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


def _parse(moment: str | None) -> datetime | None:
    if not moment:
        return None
    try:
        parsed = datetime.fromisoformat(moment.replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


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
    """
    settings = settings or get_settings()
    report = DossierReport()

    teams = teams_of(event_id, settings)
    team_ids = [int(teams[side]) for side in ("home", "away") if teams.get(side)]
    if not team_ids:
        return report

    stale = [
        team_id
        for team_id in team_ids
        if not _is_cached(team_id, KIND_COACH, report, settings, now)
    ]
    if not stale:
        return report

    blocked = _budget(len(stale) * CALL_COST, settings)
    if blocked:
        report.blocked_reason = blocked
        logger.warning(blocked)
        return report

    for team_id in stale:
        try:
            payload = await client.coachs(team_id)
        except ProviderError as exc:
            report.errors.append(f"entraineur : {exc}")
            continue
        store(team_id, KIND_COACH, payload, settings=settings, now=now)
        if KIND_COACH not in report.kinds:
            report.kinds.append(KIND_COACH)
    return report


def _is_cached(
    team_id: int,
    kind: str,
    report: DossierReport,
    settings: Settings,
    now: datetime | None,
) -> bool:
    """Vrai si le releve de cette equipe est encore frais. Le note au rapport."""
    known = load(team_id, kind, settings=settings)
    if known is None or not is_fresh(kind, known[1], now):
        return False
    if kind not in report.cached:
        report.cached.append(kind)
    return True


# -- Rendu ------------------------------------------------------------------


def _current_post(entries: list[dict[str, Any]], team_id: int) -> dict[str, Any] | None:
    """Entraineur en poste dans cette equipe, et la date de sa prise de fonction.

    Le fournisseur peut rendre plusieurs entraineurs pour une equipe : le poste
    en cours est celui dont l'etape de carriere **dans cette equipe** n'a pas de
    date de fin. A defaut de le trouver, aucune ligne — nommer l'entraineur de
    l'an dernier serait pire qu'un silence, parce que ce serait affirme.
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
    team: str, team_id: int | None, reference: datetime | None, settings: Settings
) -> str:
    """`Estoril I. Cathro (depuis 07/2024, 2 ans)`."""
    if not team_id:
        return ""
    known = load(int(team_id), KIND_COACH, settings=settings)
    if known is None:
        return ""
    post = _current_post(known[0] if isinstance(known[0], list) else [], int(team_id))
    if post is None:
        return ""
    tenure = _tenure(post.get("start"), reference)
    return f"{team} {post['name']} ({tenure})" if tenure else f"{team} {post['name']}"


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
    fragments = [
        _coach_fragment(home, teams.get("home"), reference, settings),
        _coach_fragment(away, teams.get("away"), reference, settings),
    ]
    rendered = " | ".join(fragment for fragment in fragments if fragment)
    return [("Entraineur", rendered)] if rendered else []
