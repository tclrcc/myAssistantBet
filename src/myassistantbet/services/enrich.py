"""Etage B — marches profonds, match par match.

Chaque marche demande coute 1 credit (un seul bookmaker, donc une seule region).
Le cout est donc parfaitement previsible : il est estime avant l'appel, affiche
dans l'UI, et compare au plancher `ODDS_API_CREDIT_FLOOR` avant tout depart.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from ..config import Settings, get_settings
from ..db import connect, query
from ..providers.apifootball import APIFootballClient
from ..providers.base import ProviderError, last_known_quota
from ..providers.oddsapi import DEFAULT_BOOKMAKER, PROVIDER, OddsAPIClient, expected_cost
from ..providers.tennisabstract import TennisAbstractClient
from ..providers.tennisdata import TennisDataClient
from ..providers.weather import WeatherClient
from . import coverage, dossier, elo, fixtures, reference, tennis_history, weather
from .context import KIND_VENUE, fetch_context
from .labels import affiche, is_reference, primary_book
from .markets import (
    markets_for,
)
from .scan import replace_odds  # meme regle de remplacement qu'a l'etage A
from .session import has_started

logger = logging.getLogger(__name__)


#: Marches profonds football (SPEC.md section 4).
@dataclass
class EnrichTarget:
    """Un evenement a enrichir, et le detail de ce qu'il va couter."""

    event_id: int
    oddsapi_event_id: str
    sport_key: str
    oddsapi_sport_key: str
    label: str
    markets: tuple[str, ...]
    #: Competition d'appartenance, pour memoriser les marches qu'elle sert.
    competition_id: int | None = None
    #: Necessaire au contexte API-Football. Absent = pas de contexte possible.
    apifootball_league_id: int | None = None
    home: str = ""
    away: str = ""
    commence_time: str = ""

    #: Books interroges pour cet evenement. Jusqu'a dix, le cout est le meme.
    bookmakers: tuple[str, ...] = (DEFAULT_BOOKMAKER,)
    #: Un releve de substitution est du a cet evenement : aucun de ses prix ne
    #: vient du book principal. Porte par la cible plutot que par une seconde
    #: liste, sinon un evenement a la fois achetable et relevable figurerait
    #: dans les deux et paierait son contexte deux fois.
    substitute: bool = False

    @property
    def cost(self) -> int:
        return expected_cost(list(self.markets), list(self.bookmakers))

    @property
    def context_possible(self) -> bool:
        return self.sport_key == "football" and self.apifootball_league_id is not None

    def as_event(self) -> dict[str, Any]:
        """Vue attendue par `services.context`."""
        return {
            "id": self.event_id,
            "home": self.home,
            "away": self.away,
            "commence_time": self.commence_time,
            "apifootball_league_id": self.apifootball_league_id,
        }


def _substitute_due(row: Any) -> bool:
    """Ce match attend-il un releve de substitution ?

    Deux conditions, et il faut les deux : API-Football doit pouvoir repondre
    — football, competition rattachee a une ligue — et **aucun de ses prix ne
    doit venir du book principal**. La seconde se lit chez `labels`, ou la
    notion de source principale est deja ecrite : la recopier ici en SQL
    l'aurait fait diverger au premier book ajoute, et une saisie manuelle
    serait passee pour une cote de reference.
    """
    if row["sport_key"] != "football" or not row["apifootball_league_id"]:
        return False
    books = [book for book in str(row["books"] or "").split(",") if book]
    if not books:
        return True
    return is_reference(primary_book(books))


def _substitute_target(row: Any, label: str) -> EnrichTarget:
    """Cible sans achat : tout y est gratuit en credits The Odds API."""
    return EnrichTarget(
        event_id=int(row["id"]),
        oddsapi_event_id="",
        sport_key=row["sport_key"],
        oddsapi_sport_key="",
        label=label,
        markets=(),
        bookmakers=(),
        substitute=True,
        competition_id=row["competition_id"],
        apifootball_league_id=row["apifootball_league_id"],
        home=row["home"],
        away=row["away"],
        commence_time=row["commence_time"],
    )


@dataclass
class Estimate:
    """Estimation affichee avant le clic sur « Enrichir la selection »."""

    targets: list[EnrichTarget] = field(default_factory=list)
    remaining: int | None = None
    floor: int = 500
    skipped: list[str] = field(default_factory=list)
    #: Evenements deja commences. Payer leurs marches profonds serait acheter
    #: des cotes qu'aucun pari avant-match ne peut plus utiliser.
    started: list[str] = field(default_factory=list)
    #: Evenements ecartes parce que le bookmaker n'a jamais servi le moindre
    #: marche profond sur leur competition. Distinct de `skipped` : ceux-la
    #: sont couverts par l'API, il n'y a simplement rien de plus a acheter.
    barren: list[str] = field(default_factory=list)
    #: Evenements qu'aucun credit ne peut servir — The Odds API ne les connait
    #: pas — mais qu'API-Football couvre : qualifications europeennes importees,
    #: matchs dont le fournisseur de cotes ignore la competition. Ils recoivent
    #: un releve de substitution et leur contexte, gratuitement. Sans eux, une
    #: shortlist entiere de qualifs Europa produisait un prompt vide.
    substitutes: list[EnrichTarget] = field(default_factory=list)
    #: Evenements de la shortlist, quel que soit leur sort. Sans ce compteur,
    #: « rien a enrichir » et « rien de coche » seraient indiscernables.
    considered: int = 0

    @property
    def events(self) -> int:
        return len(self.targets)

    @property
    def total_events(self) -> int:
        """Evenements que l'enrichissement va traiter, achetes ou releves.

        Distinct de `events`, qui ne compte que ce qui coute des credits : la
        barre de progression affichait « 0/0 » sur une selection entiere de
        qualifications europeennes, ou rien ne s'achete mais ou tout se releve.
        """
        return len(self.targets) + len(self.substitutes)

    @property
    def cost(self) -> int:
        return sum(target.cost for target in self.targets)

    @property
    def remaining_after(self) -> int | None:
        return None if self.remaining is None else self.remaining - self.cost

    @property
    def allowed(self) -> bool:
        if not self.targets:
            # Rien a acheter, mais peut-etre quelque chose a relever gratuitement.
            return bool(self.substitutes)
        if self.remaining is None:
            # Quota inconnu : on n'a jamais appele l'API. On laisse partir, le
            # plancher sera verifiable des le premier appel.
            return True
        return self.remaining_after is not None and self.remaining_after >= self.floor

    @property
    def blocked_reason(self) -> str | None:
        """Pourquoi le bouton est inactif. Jamais un motif faux : dire « rien de
        coche » alors que des matchs le sont enverrait chercher le probleme au
        mauvais endroit."""
        if not self.targets:
            if self.substitutes:
                return None
            if not self.considered:
                return "Aucun evenement selectionne."
            if self.started and not self.skipped and not self.barren:
                return "Rien a enrichir : tous les matchs selectionnes ont deja commence."
            if self.barren and not self.skipped:
                return (
                    "Rien a enrichir : le bookmaker ne sert aucun marche profond "
                    "sur ces competitions. Passe par la saisie groupee."
                )
            if self.skipped and not self.barren:
                return "Rien a enrichir : aucun de ces evenements n'est servi par l'API."
            return "Rien a enrichir sur cette selection."
        if self.allowed:
            return None
        return (
            f"Enrichissement bloque : {self.cost} credits necessaires, "
            f"il resterait {self.remaining_after} sous le plancher de {self.floor}."
        )


@dataclass
class EnrichResult:
    """Resultat de l'enrichissement d'un evenement."""

    label: str
    markets_received: int = 0
    odds_rows: int = 0
    #: Book chez qui les cotes ont ete relevees faute de Betclic. Non jouable
    #: tel quel : le dire ici evite de le decouvrir dans le prompt.
    substitute_book: str | None = None
    cost: int = 0
    error: str | None = None
    context_kinds: list[str] = field(default_factory=list)
    context_errors: list[str] = field(default_factory=list)
    mapping_pending: bool = False
    #: Marches obtenus d'un book de reference : {marche: book}. Ils disent ou
    #: se situe le marche, pas le prix qu'on obtiendra chez le principal.
    borrowed: dict[str, str] = field(default_factory=dict)
    #: Ce qui a manque au dossier d'equipe, tenu a part du contexte. Un plancher
    #: d'appels franchi ne rend pas le contexte partiel — il est complet — et
    #: l'annoncer sous ce nom enverrait chercher un probleme de rapprochement.
    dossier_errors: list[str] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return self.error is None

    @property
    def context_note(self) -> str:
        """Ce qui manque cote contexte, formule pour l'UI. Vide si tout va bien."""
        if self.mapping_pending:
            return "équipes non identifiées côté API-Football — résolution manuelle requise"
        if self.context_errors:
            return "contexte partiel : " + " ; ".join(self.context_errors)
        return ""

    @property
    def dossier_note(self) -> str:
        """Ce qui manque au dossier d'equipe. Vide si tout va bien."""
        return " ; ".join(self.dossier_errors)

    @property
    def notes(self) -> list[str]:
        """Tout ce qui merite une mention visible pour ce match."""
        return [note for note in (self.context_note, self.dossier_note) if note]


@dataclass
class EnrichReport:
    """Bilan complet, et progression pendant l'execution."""

    total: int = 0
    done: int = 0
    results: list[EnrichResult] = field(default_factory=list)
    finished: bool = False

    @property
    def cost(self) -> int:
        return sum(result.cost for result in self.results)

    @property
    def failures(self) -> list[EnrichResult]:
        return [result for result in self.results if not result.ok]

    @property
    def context_notes(self) -> list[EnrichResult]:
        """Matchs dont le contexte ou le dossier est incomplet, a signaler dans l'UI.

        Les deux causes sont portees separement par le resultat — un plancher
        d'appels franchi ne rend pas le contexte partiel — mais l'UI les liste
        au meme endroit : ce qui compte pour l'oeil, c'est qu'il manque quelque
        chose sur ce match.
        """
        return [result for result in self.results if result.notes]

    @property
    def percent(self) -> int:
        return 100 if self.finished else int(100 * self.done / self.total) if self.total else 0


def build_estimate(
    session_id: int,
    settings: Settings | None = None,
    now: datetime | None = None,
) -> Estimate:
    """Cout previsionnel de l'enrichissement d'une session, avant tout appel."""
    settings = settings or get_settings()
    estimate = Estimate(floor=settings.odds_api_credit_floor)

    quota = last_known_quota(PROVIDER, settings)
    if quota:
        estimate.remaining = quota["remaining"]

    with connect(settings) as conn:
        rows = conn.execute(
            "SELECT e.id, e.oddsapi_event_id, e.home, e.away, e.commence_time, "
            "       s.key AS sport_key, c.oddsapi_key AS competition_key, "
            "       c.id AS competition_id, c.apifootball_league_id, "
            # L'etage A a-t-il ramene ses cotes sur ce match ? Sur une competition
            # que le book principal ne sert pas, la reponse est non, et le 1N2
            # doit alors etre reclame ici — sans quoi il n'arrive jamais.
            "       EXISTS(SELECT 1 FROM odds o WHERE o.event_id = e.id "
            "              AND o.market_key = 'h2h') AS base_served, "
            # Les sources qui servent deja cet evenement. C'est `labels` qui dit
            # laquelle est la principale et si elle est de reference : la regle
            # est ecrite une fois, ici on ne fait que lui donner la liste.
            "       (SELECT GROUP_CONCAT(DISTINCT o.bookmaker) FROM odds o "
            "         WHERE o.event_id = e.id) AS books "
            "FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE se.session_id = ? "
            "ORDER BY e.commence_time",
            (session_id,),
        ).fetchall()

    estimate.considered = len(rows)
    for row in rows:
        label = affiche(row["home"], row["away"])
        if has_started(row["commence_time"], now):
            estimate.started.append(label)
            continue
        # Un releve de substitution est du des qu'**aucun prix ne vient du book
        # principal**, et non plus seulement quand le match n'a aucune cote. La
        # justification d'origine — « ailleurs il n'ajouterait qu'un prix non
        # jouable a cote d'un prix jouable » — ne tient pas ici : il n'y a aucun
        # prix jouable, tout est deja de reference. Mesure qui l'a declenche :
        # sur la qualification de Ligue des champions, The Odds API a ete payee
        # et ne sert que le 1N2 — 14 marches profonds sur 15 constates absents
        # chez betclic_fr, pinnacle et unibet_nl reunis — quand API-Football sert
        # dix marches de plus chez un book dont l'ecart a Betclic est mesure.
        due = _substitute_due(row)
        if not row["oddsapi_event_id"] or not row["competition_key"]:
            # The Odds API ne connait pas ce match. API-Football, peut-etre :
            # c'est le cas des qualifications europeennes importees. Le releve
            # de substitution et le contexte sont gratuits en credits.
            if due:
                estimate.substitutes.append(_substitute_target(row, label))
                continue
            # Evenement manuel (cyclisme, ATP 250) : aucun appel possible.
            estimate.skipped.append(label)
            continue
        base_served = bool(row["base_served"])
        markets = markets_for(row["sport_key"], row["competition_key"], settings, base_served)
        if not markets:
            if due:
                estimate.substitutes.append(_substitute_target(row, label))
                continue
            estimate.skipped.append(label)
            continue
        # Ne pas repayer un constat deja fait : les marches que cette competition
        # n'a jamais servis sont retires, et si plus rien d'utile ne reste,
        # l'evenement est ecarte plutot qu'appele pour rien. Sauf s'il ne reste
        # que le 1N2 d'un match que l'etage A n'a pas servi : celui-la vaut son
        # credit, c'est le seul moyen de l'obtenir.
        markets = coverage.useful(
            row["competition_id"], markets, settings, anchor_alone=not base_served
        )
        if not markets:
            # Plus rien a acheter, mais peut-etre encore quelque chose a relever :
            # c'est exactement l'etat d'une competition ou le book principal ne
            # sert rien et ou le constat de couverture est deja fait.
            if due:
                estimate.substitutes.append(_substitute_target(row, label))
                continue
            estimate.barren.append(label)
            continue
        estimate.targets.append(
            EnrichTarget(
                event_id=int(row["id"]),
                oddsapi_event_id=row["oddsapi_event_id"],
                sport_key=row["sport_key"],
                oddsapi_sport_key=row["competition_key"],
                label=label,
                markets=markets,
                substitute=due,
                bookmakers=(DEFAULT_BOOKMAKER, *settings.reference_books),
                competition_id=row["competition_id"],
                apifootball_league_id=row["apifootball_league_id"],
                home=row["home"],
                away=row["away"],
                commence_time=row["commence_time"],
            )
        )
    return estimate


def _store(event_id: int, payload: dict[str, Any], settings: Settings) -> tuple[int, int]:
    """Ecrit les cotes profondes. Renvoie (marches recus, lignes inserees)."""
    markets = sum(
        len(bookmaker.get("markets") or []) for bookmaker in payload.get("bookmakers") or []
    )
    with connect(settings) as conn:
        rows = replace_odds(conn, event_id, payload)
    return markets, rows


async def _add_substitute_odds(
    client: APIFootballClient,
    target: EnrichTarget,
    result: EnrichResult,
    settings: Settings,
) -> None:
    """Releve de substitution, ecrit dans le resultat de l'evenement.

    Ecrit une seule fois pour les deux boucles : un evenement achetable peut
    aussi etre relevable, et deux appels du meme geste auraient fini par ne plus
    dire la meme chose de leur resultat.
    """
    try:
        odds_report = await fixtures.import_odds(client, target.event_id, settings)
    except ProviderError as exc:
        result.error = result.error or str(exc)
        logger.warning("Releve de substitution echoue pour %s : %s", target.label, exc)
        return
    if odds_report.error:
        result.error = result.error or odds_report.error
        return
    # Les marches releves s'ajoutent a ceux qui ont ete achetes : le compte du
    # rapport dit ce que l'evenement a recu, quelle qu'en soit la provenance.
    result.markets_received += odds_report.markets
    result.odds_rows += odds_report.outcomes
    result.substitute_book = odds_report.bookmaker


async def _add_context(
    context_client: APIFootballClient,
    target: EnrichTarget,
    result: EnrichResult,
    settings: Settings,
    cache: dict[str, Any],
    now: datetime | None = None,
) -> None:
    """Ajoute le contexte sportif. Un echec ici ne remet jamais en cause les cotes."""
    try:
        report = await fetch_context(context_client, target.as_event(), settings, cache, now)
    except Exception as exc:  # noqa: BLE001 — le contexte est un bonus, jamais bloquant
        result.context_errors.append(f"{type(exc).__name__}: {exc}")
        logger.exception("Contexte indisponible pour %s", target.label)
        return
    result.context_kinds = report.kinds
    result.context_errors = report.errors
    result.mapping_pending = report.mapping_pending

    if report.mapping_pending:
        # Sans rapprochement sur, aucun identifiant d'equipe : le dossier n'a
        # rien a interroger, et deviner l'equipe serait pire que l'absence.
        return
    try:
        dossier_report = await dossier.refresh_event(context_client, target.event_id, settings)
    except Exception as exc:  # noqa: BLE001 — le dossier est un bonus, jamais bloquant
        result.dossier_errors.append(f"dossier : {type(exc).__name__}: {exc}")
        logger.exception("Dossier d'equipe indisponible pour %s", target.label)
        return
    result.context_kinds += dossier_report.kinds
    result.dossier_errors += dossier_report.errors
    if dossier_report.blocked_reason:
        # Un plancher franchi n'est pas une panne, mais le taire ferait chercher
        # une erreur de rapprochement la ou il n'y a qu'un quota bas.
        result.dossier_errors.append(dossier_report.blocked_reason)


def has_tennis(session_id: int, settings: Settings) -> bool:
    """Vrai si la session contient au moins un match de tennis.

    Porte sur toute la session et non sur les seules cibles payantes : un match
    dont plus aucun marche n'est a acheter garde son bloc, et son Elo l'interesse
    autant que les autres.
    """
    with connect(settings) as conn:
        row = conn.execute(
            "SELECT 1 FROM session_events se "
            "JOIN events e ON e.id = se.event_id "
            "JOIN sports s ON s.id = e.sport_id "
            "WHERE se.session_id = ? AND s.key = 'tennis' LIMIT 1",
            (session_id,),
        ).fetchone()
    return row is not None


async def _refresh_elo(
    elo_client: TennisAbstractClient,
    session_id: int,
    settings: Settings,
    now: datetime | None,
) -> None:
    """Met a jour les classements Elo si la session en a l'usage.

    Gratuit et sans quota : aucun garde-fou de credit ne s'applique. Un echec
    n'interrompt rien — le bloc CONTEXTE perdra sa ligne Elo, comme avant.
    """
    if not has_tennis(session_id, settings):
        return
    try:
        await elo.refresh(elo_client, settings, now=now)
    except Exception as exc:  # noqa: BLE001 — l'Elo est un bonus, jamais bloquant
        logger.exception("Rafraichissement Elo impossible : %s", exc)


async def _refresh_tennis_history(
    history_client: TennisDataClient,
    session_id: int,
    settings: Settings,
    now: datetime | None,
) -> None:
    """Met a jour l'historique des matchs de tennis si la session en a l'usage.

    Meme regime que l'Elo : gratuit, sans quota, avant tout garde-fou de credit,
    et un echec ne coute que des lignes. Seule la saison en cours se retelecharge,
    une fois par semaine.
    """
    if not has_tennis(session_id, settings):
        return
    try:
        report = await tennis_history.refresh(history_client, settings, now=now)
        if report.errors:
            logger.warning("Historique tennis partiel : %s", " ; ".join(report.errors))
        if report.rejected:
            # Tenu a part des erreurs : le telechargement a reussi, c'est la
            # source qui a mal date quelques lignes.
            logger.warning(
                "Historique tennis : %d ligne(s) hors de leur saison, ecartee(s)",
                report.rejected,
            )
    except Exception as exc:  # noqa: BLE001 — l'historique est un bonus, jamais bloquant
        logger.exception("Historique tennis indisponible : %s", exc)


async def _refresh_weather(
    client: WeatherClient,
    session_id: int,
    settings: Settings,
    now: datetime | None,
) -> None:
    """Meteo de chaque match de la shortlist, par le lieu qu'on lui connait.

    **Deux chemins, un seul service.** Au football la ville vient du lieu du
    match, deja persiste par `fetch_context` — donc le stade reel, y compris
    quand la rencontre est delocalisee. Au tennis elle vient de la competition,
    saisie a la main : aucun fournisseur ne sert le lieu d'un tournoi, et
    « ATP Cincinnati Open » se joue a Mason.

    Aucune erreur n'est propagee : la meteo est un bonus, et un service
    injoignable ne doit pas faire echouer un enrichissement. Le manque se dit
    dans la ligne, jamais par un silence.
    """
    for event_id, ville, pays, coup in _weather_targets(session_id, settings):
        try:
            await weather.refresh_event(client, event_id, ville, pays, coup, settings, now)
        except Exception as exc:  # noqa: BLE001 — un bonus ne fait jamais echouer
            logger.warning("Meteo indisponible pour l'evenement %s : %s", event_id, exc)


def _weather_targets(session_id: int, settings: Settings) -> list[tuple[int, str, str | None, str]]:
    """`(event_id, ville, pays, coup d'envoi)` pour chaque match de la shortlist.

    Le lieu du football est relu dans `context`, la ville du tennis dans
    `competitions` : c'est la meme requete, et rapprocher les deux ici evite deux
    parcours de shortlist qui auraient fini par diverger.
    """
    rows = query(
        "SELECT e.id, e.commence_time, c.city AS ville_competition, "
        "       (SELECT payload_json FROM context x "
        "         WHERE x.event_id = e.id AND x.kind = ? LIMIT 1) AS lieu "
        "FROM session_events se JOIN events e ON e.id = se.event_id "
        "LEFT JOIN competitions c ON c.id = e.competition_id "
        "WHERE se.session_id = ?",
        (KIND_VENUE, session_id),
        settings=settings,
    )
    cibles = []
    for row in rows:
        venue = json.loads(row["lieu"]) if row["lieu"] else {}
        # Le stade **du match** d'abord : une rencontre delocalisee n'a pas la
        # meteo du stade habituel, et c'est justement la que ca compte.
        ville = venue.get("city") or venue.get("usual_city") or row["ville_competition"] or ""
        pays = venue.get("country") or venue.get("home_country")
        if ville:
            cibles.append((int(row["id"]), str(ville), pays, str(row["commence_time"])))
    return cibles


async def run_enrich(
    client: OddsAPIClient,
    session_id: int,
    settings: Settings | None = None,
    on_progress: Callable[[EnrichReport], None] | None = None,
    context_client: APIFootballClient | None = None,
    now: datetime | None = None,
    elo_client: TennisAbstractClient | None = None,
    history_client: TennisDataClient | None = None,
    weather_client: WeatherClient | None = None,
) -> EnrichReport:
    """Enrichit tous les evenements d'une session : marches profonds puis contexte.

    Le garde-fou de quota est verifie avant de partir. Un evenement en echec
    n'interrompt pas les suivants, et un contexte manquant n'empeche jamais les
    cotes d'etre recuperees.
    """
    settings = settings or get_settings()
    # Avant tout garde-fou de credit : l'Elo est gratuit, et c'est justement
    # quand il n'y a plus un seul marche a acheter qu'il faut le recuperer.
    if elo_client is not None:
        await _refresh_elo(elo_client, session_id, settings, now)
    if history_client is not None:
        await _refresh_tennis_history(history_client, session_id, settings, now)

    estimate = build_estimate(session_id, settings, now)
    report = EnrichReport(total=estimate.total_events)

    if not estimate.allowed:
        report.finished = True
        reason = estimate.blocked_reason or "Enrichissement impossible."
        report.results.append(EnrichResult(label="—", error=reason))
        logger.warning("Enrichissement refuse : %s", reason)
        if on_progress:
            on_progress(report)
        return report

    # Classements et statistiques d'equipe sont partages entre les matchs d'une
    # meme ligue : on ne les paie qu'une fois par enrichissement.
    context_cache: dict[str, Any] = {}

    for target in estimate.targets:
        result = EnrichResult(label=target.label)
        try:
            payload, cost = await client.get_event_odds(
                target.oddsapi_sport_key,
                target.oddsapi_event_id,
                markets=target.markets,
                bookmakers=target.bookmakers,
            )
            result.cost = cost
            # Une seule source par marche : le principal d'abord, un book de
            # reference seulement pour ce qu'il ne sert pas.
            payload = reference.merge(payload, DEFAULT_BOOKMAKER, settings.reference_books)
            result.borrowed = reference.borrowed_markets(payload, DEFAULT_BOOKMAKER)
            result.markets_received, result.odds_rows = _store(target.event_id, payload, settings)
            # Le constat ne vaut que pour les books qui l'ont produit : les
            # memoriser avec lui, sinon elargir la liste ne rouvrirait rien.
            coverage.record(
                target.competition_id, target.markets, payload, settings, target.bookmakers
            )
        except ProviderError as exc:
            result.error = str(exc)
            logger.warning("Enrichissement echoue pour %s : %s", target.label, exc)
        except Exception as exc:  # noqa: BLE001 — reponse inattendue, jamais fatale
            # Une reponse de forme imprevue ne doit pas tuer l'enrichissement des
            # autres matchs, ni mourir en silence dans une tache de fond.
            result.error = f"reponse inexploitable : {type(exc).__name__}: {exc}"
            logger.exception("Reponse inexploitable pour %s", target.label)

        # Achete d'abord, releve ensuite, et dans cet ordre : ce que The Odds API
        # sert prime, le releve ne comble que ce qui reste. Un evenement peut
        # etre les deux — la competition vend quelques marches sans qu'aucun
        # vienne du book principal — et il ne doit alors figurer que dans une
        # seule liste, sinon son contexte serait paye deux fois.
        if target.substitute and context_client is not None:
            await _add_substitute_odds(context_client, target, result, settings)

        if context_client is not None and target.context_possible:
            await _add_context(context_client, target, result, settings, context_cache, now)

        report.results.append(result)
        report.done += 1
        if on_progress:
            on_progress(report)

    # Matchs que The Odds API ne connait pas : releve de substitution puis
    # contexte. Aucun credit n'est en jeu, donc ce bloc passe apres le garde-fou
    # sans avoir a le consulter — c'est justement quand il n'y a plus rien a
    # acheter que ces matchs sont tout ce qui reste.
    for target in estimate.substitutes:
        result = EnrichResult(label=target.label)
        if context_client is None:
            result.error = "aucun client API-Football : ni cotes de substitution ni contexte"
            report.results.append(result)
            report.done += 1
            continue
        await _add_substitute_odds(context_client, target, result, settings)

        if context_client is not None and target.context_possible:
            await _add_context(context_client, target, result, settings, context_cache, now)

        report.results.append(result)
        report.done += 1
        if on_progress:
            on_progress(report)

    # La meteo **apres** le contexte, et non pendant : les coordonnees d'un match
    # de football se deduisent du lieu, que `fetch_context` vient d'ecrire. Elle
    # est gratuite et sans cle, donc hors du garde-fou de credit — comme l'Elo.
    if weather_client is not None:
        await _refresh_weather(weather_client, session_id, settings, now)

    report.finished = True
    logger.info(
        "Enrichissement termine : %d/%d evenements, cout %d credits, %d echec(s)",
        report.done - len(report.failures),
        report.total,
        report.cost,
        len(report.failures),
    )
    if on_progress:
        on_progress(report)
    return report
