"""Application FastAPI : cycle de vie, routes. Aucune logique metier ici."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator, Mapping
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Any
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound

from . import __version__, db
from .config import PACKAGE_DIR, get_settings
from .providers.apifootball import APIFootballClient
from .providers.base import ProviderError
from .providers.oddsapi import OddsAPIClient
from .providers.tennisabstract import TennisAbstractClient
from .providers.tennisdata import TennisDataClient
from .providers.weather import WeatherClient
from .scheduler import build_scheduler
from .services import board as board_service
from .services import competitions as competitions_service
from .services import context as context_service
from .services import coupons as coupons_service
from .services import coverage as coverage_service
from .services import dossier as dossier_service
from .services import elo as elo_service
from .services import enrich as enrich_service
from .services import fixtures as fixtures_service
from .services import grid as grid_service
from .services import history as history_service
from .services import labels as labels_service
from .services import manual as manual_service
from .services import mapping_ui as mapping_service
from .services import market_families as market_families_service
from .services import odds_view as odds_view_service
from .services import picks_import as picks_import_service
from .services import prompt as prompt_service
from .services import session as session_service
from .services import set_scores as set_scores_service
from .services import tennis_history as tennis_history_service
from .services import tennis_load as tennis_load_service
from .services import thresholds as thresholds_service
from .services.inference import EQUIVALENCE_MARGIN, MARGIN_REFERENCE
from .services.scan import run_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
# httpx journalise en INFO l'URL complete de chaque appel, **cle d'API
# comprise** : `apiKey=…` se retrouvait en clair dans journalctl, a rebours de
# la regle qui veut qu'un secret ne sorte jamais dans les logs. Nos propres
# lignes disent deja l'endpoint, le cout, les credits restants et la duree —
# les taire ici ne fait donc perdre aucune information, seulement le secret.
logging.getLogger("httpx").setLevel(logging.WARNING)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")
# Pictogrammes du bloc CONTEXTE : purement decoratifs, definis avec les autres
# libelles d'affichage plutot que dans le gabarit, pour rester testables.
templates.env.filters["context_icon"] = labels_service.context_icon


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()

    applied = db.run_migrations(settings)
    if applied:
        logger.info("Migrations appliquees au demarrage : %s", ", ".join(applied))
    else:
        logger.info("Schema deja a jour")
    logger.info("Base : %s", settings.db_path_absolute)

    app.state.http = httpx.AsyncClient(follow_redirects=True)
    app.state.scheduler = None
    if settings.scheduler_enabled:
        scheduler = build_scheduler(app.state.http, settings)
        scheduler.start()
        app.state.scheduler = scheduler

    try:
        yield
    finally:
        if app.state.scheduler is not None:
            app.state.scheduler.shutdown(wait=False)
        await app.state.http.aclose()


app = FastAPI(title="MyAssistantBet", version=__version__, lifespan=lifespan)
app.mount("/static", StaticFiles(directory=PACKAGE_DIR / "static"), name="static")


def _filters_from(params: Mapping[str, Any]) -> board_service.Filters:
    """Convertit un jeu de parametres en criteres de filtrage.

    Les parametres inconnus ou mal formes sont ignores : un lien bricole a la
    main ne doit pas renvoyer une erreur 500. Accepte aussi bien la chaine de
    requete d'un GET que le corps d'un POST envoye par `hx-include`.
    """
    return board_service.Filters(
        sport=str(params.get("sport", "")).strip(),
        competition_id=_int_or_none(params.get("competition_id")),
        hour_from=_int_or_none(params.get("hour_from")),
        hour_to=_int_or_none(params.get("hour_to")),
        text=str(params.get("text", "")).strip(),
        date=str(params.get("date", "")).strip(),
    )


def _filters(request: Request) -> board_service.Filters:
    return _filters_from(request.query_params)


def _int_or_none(value: object | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _render_board(
    request: Request,
    template: str,
    report: object | None = None,
    filters: board_service.Filters | None = None,
) -> HTMLResponse:
    view = board_service.build_view(filters or _filters(request), get_settings())
    return templates.TemplateResponse(
        request,
        template,
        {
            "rows": view.rows,
            "banner": view.banner,
            "options": view.options,
            "filters": view.filters,
            "report": report,
        },
    )


@app.get("/", response_class=HTMLResponse)
def board(request: Request) -> HTMLResponse:
    """Board complet : bandeau, filtres, evenements de la fenetre courante."""
    return _render_board(request, "board.html")


@app.get("/board", response_class=HTMLResponse)
def board_fragment(request: Request) -> HTMLResponse:
    """Fragment du board, recharge par HTMX a chaque changement de filtre."""
    return _render_board(request, "_board.html")


@app.post("/scan", response_class=HTMLResponse)
async def trigger_scan(request: Request) -> HTMLResponse:
    """Declenchement manuel de l'etage A, puis re-rendu du board."""
    settings = get_settings()
    client = OddsAPIClient(request.app.state.http, settings)
    report = await run_scan(client, settings)
    return _render_board(request, "_board.html", report=report)


@app.post("/events/{event_id}/select", response_class=HTMLResponse)
def select_event(
    request: Request, event_id: int, selected: str | None = Form(default=None)
) -> HTMLResponse:
    """Coche ou decoche un evenement dans la session courante."""
    board_service.toggle_selection(event_id, selected is not None, get_settings())
    return templates.TemplateResponse(
        request, "_banner.html", {"banner": board_service.banner(get_settings())}
    )


@app.post("/events/select-all", response_class=HTMLResponse)
async def select_all(request: Request) -> HTMLResponse:
    """Coche ou decoche d'un coup tous les evenements du filtre courant.

    Porte sur le filtre affiche, jamais sur toute la base : ce qu'on voit est
    ce qu'on selectionne.
    """
    form = dict(await request.form())
    wanted = str(form.get("mode", "on")) == "on"
    filters = _filters_from(form)
    board_service.toggle_filtered(filters, wanted, get_settings())
    return _render_board(request, "_board.html", filters=filters)


# --- Fiche evenement -------------------------------------------------------


def _event_context(event_id: int, **extra: object) -> dict[str, object]:
    settings = get_settings()
    view = odds_view_service.build(event_id, settings)
    if view is None:
        raise HTTPException(status_code=404, detail="Evenement inconnu")
    # Le contexte se relit en base, sans aucun appel reseau : la fiche montre
    # ce que le prompt dirait, sans avoir a le generer pour le savoir. C'est
    # **le meme assembleur** que celui du prompt : deux assemblages paralleles
    # ont diverge deux fois, laissant la fiche sans dossier d'equipe puis sans
    # historique tennis.
    lines = session_service.context_block(
        event_id,
        view.home,
        view.away,
        view.commence_utc,
        view.sport_key,
        oddsapi_key=view.oddsapi_key,
        surface=view.surface,
        competition_id=view.competition_id,
        settings=settings,
    )
    return {
        "event": view,
        "context_lines": lines,
        # Le detail des derniers matchs ne va pas dans le prompt — dix rencontres
        # par joueur y couteraient cinq cents caracteres — mais l'ecran n'a pas de
        # budget, et c'est la que la ligne « Forme » montre sa limite.
        "recent_matches": (
            tennis_history_service.recent_matches(view.home, view.away, view.commence_utc, settings)
            if view.sport_key == "tennis"
            else []
        ),
        "error": None,
        "result": None,
        **extra,
    }


@app.post("/events/{event_id}/context", response_class=HTMLResponse)
async def fetch_event_context(request: Request, event_id: int) -> HTMLResponse:
    """Recupere le contexte sportif d'un match. Aucun credit The Odds API."""
    settings = get_settings()
    with db.connect(settings) as conn:
        row = conn.execute(
            "SELECT e.id, e.home, e.away, e.commence_time, c.apifootball_league_id "
            "FROM events e LEFT JOIN competitions c ON c.id = e.competition_id "
            "WHERE e.id = ?",
            (event_id,),
        ).fetchone()
    if row is None:
        raise HTTPException(status_code=404, detail="Evenement inconnu")
    client = APIFootballClient(request.app.state.http, settings)
    report = await context_service.fetch_context(
        client,
        dict(row),
        settings,
        # Le pays du stade quand le fournisseur ne l'identifie pas. Ce bouton doit
        # ramener ce qu'un enrichissement ramene : deux chemins qui ne recuperent
        # pas la meme chose est le defaut que l'assembleur de contexte a deja paye
        # deux fois.
        geo_client=WeatherClient(request.app.state.http, settings),
    )
    # Le dossier d'equipe fait partie du contexte sportif du point de vue de
    # l'utilisateur : sans cet appel, ce bouton et l'enrichissement d'une session
    # ne recuperaient pas la meme chose, et la fiche resterait sans entraineur ni
    # historique sans que rien ne l'explique.
    if not report.mapping_pending:
        dossier_report = await dossier_service.refresh_event(client, event_id, settings)
        report.kinds += dossier_report.kinds
        report.errors += dossier_report.errors
        if dossier_report.blocked_reason:
            report.errors.append(dossier_report.blocked_reason)
    return templates.TemplateResponse(
        request, "_event_context.html", _event_context(event_id, context_report=report)
    )


@app.get("/events/{event_id}", response_class=HTMLResponse)
def event_page(request: Request, event_id: int) -> HTMLResponse:
    """Fiche d'un evenement : toutes ses cotes, et la saisie manuelle."""
    return templates.TemplateResponse(request, "event.html", _event_context(event_id))


@app.post("/events/{event_id}/shortlist", response_class=HTMLResponse)
def toggle_event_shortlist(
    request: Request, event_id: int, selected: str | None = Form(default=None)
) -> HTMLResponse:
    """Coche ou decoche depuis la fiche, et re-rend son en-tete."""
    board_service.toggle_selection(event_id, selected is not None, get_settings())
    return templates.TemplateResponse(request, "_event_head.html", _event_context(event_id))


@app.post("/events/{event_id}/unplayed", response_class=HTMLResponse)
def mark_event_unplayed(
    request: Request, event_id: int, outcome: str = Form(default="")
) -> HTMLResponse:
    """Marque une rencontre programmee comme non disputee, ou defait le marquage.

    Le bloc entier est re-rendu : `Repos`, `Parcours` et `Non joue` changent
    ensemble, et n'en rafraichir qu'un laisserait les trois se contredire a
    l'ecran.
    """
    try:
        tennis_load_service.mark_unplayed(event_id, outcome, get_settings())
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "_event_context.html", _event_context(event_id, error=str(exc))
        )
    return templates.TemplateResponse(request, "_event_context.html", _event_context(event_id))


@app.post("/events/{event_id}/odds/apifootball", response_class=HTMLResponse)
async def fetch_substitute_odds(request: Request, event_id: int) -> HTMLResponse:
    """Releve des cotes chez un substitut de Betclic, pour un match qui n'en a pas."""
    settings = get_settings()
    client = APIFootballClient(request.app.state.http, settings)
    report = await fixtures_service.import_odds(client, event_id, settings)
    return templates.TemplateResponse(
        request, "_event_odds.html", _event_context(event_id, odds_report=report)
    )


@app.post("/events/{event_id}/odds", response_class=HTMLResponse)
async def add_manual_odds(request: Request, event_id: int) -> HTMLResponse:
    """Ajoute des cotes saisies a la main. La saisie est conservee si refusee."""
    form = dict(await request.form())
    raw = str(form.get("odds", ""))
    replace = str(form.get("replace", "")) == "on"
    try:
        result = manual_service.attach_odds(event_id, raw, replace, get_settings())
    except manual_service.ManualError as exc:
        return templates.TemplateResponse(
            request,
            "_event_odds.html",
            _event_context(event_id, error=str(exc), typed=raw),
        )
    return templates.TemplateResponse(
        request, "_event_odds.html", _event_context(event_id, result=result)
    )


@app.post("/events/{event_id}/odds/clear", response_class=HTMLResponse)
def clear_manual_odds(request: Request, event_id: int) -> HTMLResponse:
    """Retire toutes les cotes manuelles de l'evenement. Les cotes d'API restent."""
    removed = manual_service.clear_manual_odds(event_id, get_settings())
    return templates.TemplateResponse(
        request,
        "_event_odds.html",
        _event_context(event_id, result=manual_service.AttachResult(removed=removed)),
    )


# --- Shortlist -------------------------------------------------------------

#: Colonnes de la grille de saisie, quand aucun libelle d'issue n'est fourni.
GRID_COLUMNS = 2

#: Progression des enrichissements en cours, par session. Mono-utilisateur,
#: un seul process : un dictionnaire en memoire suffit et evite une table.
ENRICH_PROGRESS: dict[int, enrich_service.EnrichReport] = {}

#: Taches d'enrichissement en vol. asyncio ne garde qu'une reference faible sur
#: les taches : sans cet ensemble, une tache peut etre collectee en cours de route.
ENRICH_TASKS: set[asyncio.Task[None]] = set()


def _shortlist_context(
    session_id: int, order: str = "time", thin_only: bool = False
) -> dict[str, object]:
    settings = get_settings()
    return {
        "view": session_service.build_view(session_id, settings, order=order, thin_only=thin_only),
        "estimate": enrich_service.build_estimate(session_id, settings),
        "progress": ENRICH_PROGRESS.get(session_id),
        "orders": session_service.SHORTLIST_ORDERS,
    }


def _require_session(session_id: int) -> None:
    if not session_service.session_exists(session_id, get_settings()):
        raise HTTPException(status_code=404, detail="Session inconnue")


@app.get("/session/{session_id}", response_class=HTMLResponse)
def shortlist(request: Request, session_id: int) -> HTMLResponse:
    """Shortlist : les matchs coches, regroupes par sport."""
    _require_session(session_id)
    return templates.TemplateResponse(
        request,
        "shortlist.html",
        _shortlist_context(
            session_id,
            str(request.query_params.get("order", "time")),
            bool(request.query_params.get("thin_only")),
        ),
    )


@app.get("/session/{session_id}/shortlist", response_class=HTMLResponse)
def shortlist_fragment(request: Request, session_id: int) -> HTMLResponse:
    """Fragment de la shortlist, recharge a chaque changement de tri ou de filtre."""
    _require_session(session_id)
    return templates.TemplateResponse(
        request,
        "_shortlist.html",
        _shortlist_context(
            session_id,
            str(request.query_params.get("order", "time")),
            bool(request.query_params.get("thin_only")),
        ),
    )


def _grid_context(session_id: int, **extra: object) -> dict[str, object]:
    return {
        "grid": grid_service.build_view(session_id, get_settings()),
        "market": "",
        "outcomes": "",
        "error": None,
        "result": None,
        "paste": None,
        "prefill": {},
        **extra,
    }


@app.post("/session/{session_id}/odds/paste", response_class=HTMLResponse)
async def grid_paste(request: Request, session_id: int) -> HTMLResponse:
    """Pre-remplit la grille depuis un bloc colle. N'ecrit rien en base."""
    _require_session(session_id)
    form = dict(await request.form())
    view = grid_service.build_view(session_id, get_settings())
    labels = grid_service.parse_outcome_labels(str(form.get("outcomes", "")))
    paste = grid_service.parse_paste(
        str(form.get("pasted", "")), view.rows, len(labels) or GRID_COLUMNS
    )
    return templates.TemplateResponse(
        request,
        "grid.html",
        _grid_context(
            session_id,
            market=str(form.get("market", "")),
            outcomes=str(form.get("outcomes", "")),
            paste=paste,
            prefill=paste.cells,
        ),
    )


@app.get("/session/{session_id}/odds", response_class=HTMLResponse)
def grid_page(request: Request, session_id: int) -> HTMLResponse:
    """Saisie groupee : un marche, une ligne par match de la shortlist."""
    _require_session(session_id)
    return templates.TemplateResponse(request, "grid.html", _grid_context(session_id))


@app.post("/session/{session_id}/odds", response_class=HTMLResponse)
async def grid_save(request: Request, session_id: int) -> HTMLResponse:
    """Enregistre la grille. La saisie est conservee telle quelle si elle est refusee."""
    _require_session(session_id)
    form = dict(await request.form())
    market = str(form.get("market", ""))
    outcomes = str(form.get("outcomes", ""))
    cells = {
        key[len("price_") :].replace("_", ":"): str(value)
        for key, value in form.items()
        if key.startswith("price_")
    }
    typed = {"market": market, "outcomes": outcomes}
    try:
        result = grid_service.save_grid(
            session_id,
            market,
            grid_service.parse_outcome_labels(outcomes),
            cells,
            str(form.get("replace", "")) == "on",
            get_settings(),
        )
    except manual_service.ManualError as exc:
        return templates.TemplateResponse(
            request, "grid.html", _grid_context(session_id, error=str(exc), **typed)
        )
    return templates.TemplateResponse(
        request, "grid.html", _grid_context(session_id, result=result, **typed)
    )


@app.post("/session/{session_id}/events/{event_id}/note")
def save_note(session_id: int, event_id: int, note: str = Form(default="")) -> PlainTextResponse:
    """Note libre d'un evenement, injectee telle quelle sous NOTE PERSO."""
    _require_session(session_id)
    session_service.set_note(session_id, event_id, note, get_settings())
    return PlainTextResponse("", status_code=204)


@app.post("/session/{session_id}/events/{event_id}/remove", response_class=HTMLResponse)
def remove_from_shortlist(request: Request, session_id: int, event_id: int) -> HTMLResponse:
    """Retire un evenement de la shortlist, depuis la shortlist elle-meme.

    Un match commence a quitte le board : sans cette action, il n'y aurait plus
    aucun endroit ou le decocher.
    """
    _require_session(session_id)
    session_service.remove_event(session_id, event_id, get_settings())
    return templates.TemplateResponse(request, "_shortlist.html", _shortlist_context(session_id))


@app.post("/session/{session_id}/enrich", response_class=HTMLResponse)
async def start_enrich(request: Request, session_id: int) -> HTMLResponse:
    """Lance l'etage B en tache de fond et rend la zone de progression."""
    _require_session(session_id)
    settings = get_settings()
    running = ENRICH_PROGRESS.get(session_id)
    if running is None or running.finished:
        estimate = enrich_service.build_estimate(session_id, settings)
        ENRICH_PROGRESS[session_id] = enrich_service.EnrichReport(total=estimate.steps())
        client = OddsAPIClient(request.app.state.http, settings)
        # Sans cle API-Football, on enrichit les cotes sans contexte plutot que
        # d'echouer : le bloc CONTEXTE sera simplement absent.
        context_client = (
            APIFootballClient(request.app.state.http, settings)
            if settings.apifootball_key
            else None
        )

        async def _run() -> None:
            def _track(report: enrich_service.EnrichReport) -> None:
                ENRICH_PROGRESS[session_id] = report

            await enrich_service.run_enrich(
                client,
                session_id,
                settings,
                on_progress=_track,
                context_client=context_client,
                # Gratuit et sans cle : toujours branche, contrairement au
                # contexte football qui depend d'un abonnement.
                elo_client=TennisAbstractClient(request.app.state.http, settings),
                history_client=TennisDataClient(request.app.state.http, settings),
                # Gratuite et sans cle, comme l'Elo et l'historique tennis : elle
                # ne consulte donc aucun garde-fou de credit.
                weather_client=WeatherClient(request.app.state.http, settings),
            )

        task = asyncio.create_task(_run())
        ENRICH_TASKS.add(task)
        task.add_done_callback(ENRICH_TASKS.discard)

    return templates.TemplateResponse(request, "_enrich.html", _shortlist_context(session_id))


@app.get("/session/{session_id}/enrich/status", response_class=HTMLResponse)
def enrich_status(request: Request, session_id: int) -> HTMLResponse:
    """Fragment de progression, interroge en boucle par HTMX."""
    _require_session(session_id)
    return templates.TemplateResponse(request, "_enrich.html", _shortlist_context(session_id))


# --- Evenements manuels ----------------------------------------------------


def _empty_manual_form() -> dict[str, str]:
    today = datetime.now(ZoneInfo(get_settings().tz))
    return {
        "sport": "cycling",
        "competition": "",
        "home": "",
        "away": "",
        "date": today.strftime("%Y-%m-%d"),
        "time": "14:00",
        "odds": "",
        "links": "",
        "notes": "",
        "profile": "",
        "startlist": "",
    }


@app.get("/manual", response_class=HTMLResponse)
def manual_form(request: Request) -> HTMLResponse:
    """Formulaire d'ajout d'un evenement que ni Odds API ni API-Football ne couvrent."""
    return templates.TemplateResponse(
        request, "manual.html", {"form": _empty_manual_form(), "error": None, "created": None}
    )


@app.post("/manual", response_class=HTMLResponse)
async def manual_create(request: Request) -> HTMLResponse:
    """Cree l'evenement manuel. La saisie est conservee si elle est refusee."""
    settings = get_settings()
    form = dict(await request.form())
    values = {**_empty_manual_form(), **{key: str(value) for key, value in form.items()}}

    try:
        event = manual_service.build(
            sport_key=values["sport"],
            competition=values["competition"],
            home=values["home"],
            away=values["away"],
            date_value=values["date"],
            time_value=values["time"],
            odds_raw=values["odds"],
            links_raw=values["links"],
            notes=values["notes"],
            profile=values["profile"],
            startlist=values["startlist"],
            settings=settings,
        )
    except manual_service.ManualError as exc:
        return templates.TemplateResponse(
            request, "manual.html", {"form": values, "error": str(exc), "created": None}
        )

    manual_service.save(event, settings)
    return templates.TemplateResponse(
        request, "manual.html", {"form": _empty_manual_form(), "error": None, "created": event}
    )


# --- Competitions ----------------------------------------------------------


def _competitions_context(
    report: object | None = None,
    elo_report: object | None = None,
    import_report: object | None = None,
    error: str | None = None,
    typed: dict[str, str] | None = None,
) -> dict[str, object]:
    settings = get_settings()
    return {
        "competitions": competitions_service.list_all(settings),
        "coverage": coverage_service.by_competition(settings),
        "report": report,
        "elo_report": elo_report,
        "import_report": import_report,
        # Une saisie refusee revient avec son texte : retaper un libelle et un
        # identifiant de ligue parce qu'un champ manquait est une punition.
        "error": error,
        "typed": typed or {},
        "surfaces": competitions_service.SURFACES,
        # Par sport : les niveaux du tennis et ceux du football ne se proposent
        # pas dans le meme menu, et la saisie refuse deja le melange.
        "categories": competitions_service.CATEGORIES_BY_SPORT,
        "unclassified": competitions_service.unclassified(settings),
        # Ce qui manque doit se voir dans l'interface, pas se decouvrir dans le
        # prompt : une competition passee a l'analyse sans fiche est une analyse
        # muette sur le format, et le compte dit combien il y en a eu.
        "missing_notes": competitions_service.without_notes(settings),
        "elo_state": elo_service.state(settings),
    }


@app.get("/competitions", response_class=HTMLResponse)
def competitions_page(request: Request) -> HTMLResponse:
    """Competitions connues et leur etat d'activation."""
    return templates.TemplateResponse(request, "competitions.html", _competitions_context())


@app.post("/competitions/sync", response_class=HTMLResponse)
async def competitions_sync(request: Request) -> HTMLResponse:
    """Synchronise le catalogue depuis `/sports`. Endpoint gratuit."""
    settings = get_settings()
    client = OddsAPIClient(request.app.state.http, settings)
    try:
        report = await competitions_service.sync_from_api(client, settings)
    except ProviderError as exc:
        logger.warning("Synchronisation impossible : %s", exc)
        report = None
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context(report))


@app.post("/competitions/apifootball", response_class=HTMLResponse)
def competition_create(
    request: Request,
    label: str = Form(default=""),
    apifootball_league_id: str = Form(default=""),
    category: str = Form(default=""),
) -> HTMLResponse:
    """Cree une competition football absente du catalogue The Odds API.

    La Supercoupe d'Europe n'y figure a aucun moment : la synchronisation ne
    peut pas la decouvrir, et sans cette route elle n'entrait que comme effet de
    bord d'une saisie manuelle, donc sans ligue rattachee ni import de matchs.
    """
    typed = {
        "label": label,
        "apifootball_league_id": apifootball_league_id,
        "category": category,
    }
    try:
        competitions_service.create_apifootball(
            label, apifootball_league_id, category, get_settings()
        )
    except competitions_service.CompetitionError as exc:
        return templates.TemplateResponse(
            request, "_competitions.html", _competitions_context(error=str(exc), typed=typed)
        )
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/active", response_class=HTMLResponse)
def competition_toggle(
    request: Request, competition_id: int, active: str | None = Form(default=None)
) -> HTMLResponse:
    """Active ou desactive une competition : seules les actives sont scannees."""
    competitions_service.set_active(competition_id, active is not None, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


# --- Mapping des equipes ---------------------------------------------------


@app.post("/competitions/{competition_id}/notes", response_class=HTMLResponse)
def competition_notes(
    request: Request, competition_id: int, notes: str = Form(default="")
) -> HTMLResponse:
    """Enregistre la fiche d'une competition, injectee une fois par prompt."""
    competitions_service.set_notes(competition_id, notes, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/surface", response_class=HTMLResponse)
def competition_surface(
    request: Request, competition_id: int, surface: str = Form(default="")
) -> HTMLResponse:
    """Fixe la surface d'une competition : elle decide quel Elo de surface est rendu."""
    competitions_service.set_surface(competition_id, surface, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/city", response_class=HTMLResponse)
def competition_city(
    request: Request, competition_id: int, city: str = Form(default="")
) -> HTMLResponse:
    """Fixe la ville d'une competition : elle situe la meteo du lieu.

    Aucun fournisseur ne sert le lieu d'un tournoi de tennis, et le libelle ne
    le dit pas — « ATP Cincinnati Open » se joue a Mason.
    """
    competitions_service.set_city(competition_id, city, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/timezone", response_class=HTMLResponse)
def competition_timezone(
    request: Request, competition_id: int, timezone: str = Form(default="")
) -> HTMLResponse:
    """Fixe le fuseau du lieu : il date un fait la ou il se produit.

    Un fuseau illisible est **refuse** et non ignore : accepte, il ferait rendre
    des heures UTC sous le mot « local », soit l'affirmation exactement inverse.
    """
    try:
        competitions_service.set_timezone(competition_id, timezone, get_settings())
    except ValueError as exc:
        return templates.TemplateResponse(
            request, "_competitions.html", _competitions_context(error=str(exc))
        )
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/fixtures", response_class=HTMLResponse)
async def competition_import_fixtures(request: Request, competition_id: int) -> HTMLResponse:
    """Importe les matchs depuis API-Football, pour ce que The Odds API ne sert pas."""
    settings = get_settings()
    client = APIFootballClient(request.app.state.http, settings)
    report = await fixtures_service.import_competition(client, competition_id, settings)
    return templates.TemplateResponse(
        request, "_competitions.html", _competitions_context(import_report=report)
    )


@app.post("/competitions/{competition_id}/tennisdata", response_class=HTMLResponse)
def competition_tennisdata(
    request: Request, competition_id: int, tennisdata_tournaments: str = Form(default="")
) -> HTMLResponse:
    """Rattache un tournoi de tennis a son nom dans le jeu de donnees de resultats."""
    competitions_service.set_tennisdata_tournaments(
        competition_id, tennisdata_tournaments, get_settings()
    )
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/apifootball", response_class=HTMLResponse)
def competition_apifootball(
    request: Request, competition_id: int, apifootball_league_id: str = Form(default="")
) -> HTMLResponse:
    """Rattache une competition football a sa ligue API-Football (contexte)."""
    competitions_service.set_apifootball_league(
        competition_id, apifootball_league_id, get_settings()
    )
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/{competition_id}/category", response_class=HTMLResponse)
def competition_category(
    request: Request, competition_id: int, category: str = Form(default="")
) -> HTMLResponse:
    """Fixe le niveau d'une competition : Grand Chelem, Masters 1000, 500…"""
    competitions_service.set_category(competition_id, category, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.post("/competitions/elo/refresh", response_class=HTMLResponse)
async def refresh_elo(request: Request) -> HTMLResponse:
    """Rafraichit les classements Elo tennis. Gratuit, aucun credit consomme."""
    settings = get_settings()
    client = TennisAbstractClient(request.app.state.http, settings)
    report = await elo_service.refresh(client, settings, force=True)
    return templates.TemplateResponse(
        request, "_competitions.html", _competitions_context(elo_report=report)
    )


@app.post("/competitions/{competition_id}/coverage/reset", response_class=HTMLResponse)
def reset_coverage(request: Request, competition_id: int) -> HTMLResponse:
    """Oublie les marches constates vides : la competition sera retestee."""
    coverage_service.reset(competition_id, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


@app.get("/mapping", response_class=HTMLResponse)
def mapping_page(request: Request) -> HTMLResponse:
    """Resolution manuelle des correspondances d'equipes incertaines."""
    return templates.TemplateResponse(
        request, "mapping.html", {"events": mapping_service.pending_events(get_settings())}
    )


#: Champ de formulaire du choix manuel, un par equipe a resoudre.
CHOICE_FIELD = Form(default=[])


@app.post("/mapping/{event_id}", response_class=HTMLResponse)
def resolve_mapping(
    request: Request, event_id: int, choice: list[str] = CHOICE_FIELD
) -> HTMLResponse:
    """Enregistre les alias choisis. Un choix manuel vaut pour toujours."""
    settings = get_settings()
    choices: dict[str, tuple[int, str]] = {}
    for raw in choice:
        oddsapi_name, apifootball_id, apifootball_name = _parse_choice(raw)
        if oddsapi_name:
            choices[oddsapi_name] = (apifootball_id, apifootball_name)

    mapping_service.resolve_manually(event_id, choices, settings)
    return templates.TemplateResponse(
        request, "_mapping_list.html", {"events": mapping_service.pending_events(settings)}
    )


def _parse_choice(raw: str) -> tuple[str, int, str]:
    """`nom odds|id|nom apifootball`. Une valeur vide signifie « pas de choix »."""
    parts = raw.split("|", 2)
    if len(parts) != 3:
        return "", 0, ""
    try:
        return parts[0], int(parts[1]), parts[2]
    except ValueError:
        return "", 0, ""


# --- Prompt ----------------------------------------------------------------


def _build_prompt(
    session_id: int, template_name: str | None, competition_id: int | None = None
) -> prompt_service.RenderedPrompt:
    settings = get_settings()
    name = template_name or prompt_service.DEFAULT_TEMPLATE
    try:
        return prompt_service.build_prompt(
            session_id, name, settings, competition_id=competition_id
        )
    except TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Template inconnu : {name}") from exc


@app.get("/session/{session_id}/prompt", response_class=HTMLResponse)
def prompt_page(
    request: Request,
    session_id: int,
    template: str | None = None,
    competition_id: int | None = None,
) -> HTMLResponse:
    """Prompt rendu, sauvegarde en base a chaque generation.

    `competition_id` restreint le lot sans toucher a la shortlist : sur une
    soiree a trente matchs, l'analyse s'etiole faute de pouvoir chercher autant
    par match, et decocher pour scinder ferait perdre le rattachement des picks.
    """
    _require_session(session_id)
    settings = get_settings()
    rendered = _build_prompt(session_id, template, competition_id)
    prompt_service.save_prompt(session_id, rendered, settings)

    return templates.TemplateResponse(
        request,
        "prompt.html",
        {
            "session_id": session_id,
            "session_label": session_service.session_label(session_id, settings),
            "templates": prompt_service.list_templates(),
            "template_name": rendered.template_name,
            "body": rendered.body,
            "token_estimate": rendered.token_estimate,
            "blocks": rendered.blocks,
            "started": rendered.started,
            "competitions": session_service.competitions_of(session_id, settings),
            "competition_id": competition_id,
        },
    )


@app.get("/session/{session_id}/prompt.md")
def prompt_download(
    session_id: int, template: str | None = None, competition_id: int | None = None
) -> PlainTextResponse:
    """Le meme prompt, en telechargement Markdown."""
    _require_session(session_id)
    rendered = _build_prompt(session_id, template, competition_id)
    suffix = f"-competition-{competition_id}" if competition_id else ""
    filename = f"session-{session_id}{suffix}.md"
    return PlainTextResponse(
        rendered.body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


# --- Historique et picks ---------------------------------------------------


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    """Sessions passees et taux de reussite. Aucun indicateur financier."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "history.html",
        {"sessions": history_service.list_sessions(settings)},
    )


@app.get("/stats", response_class=HTMLResponse)
def stats_page(request: Request) -> HTMLResponse:
    """Deux mesures distinctes : ce que vaut l'analyse, ce que valent les paris."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "stats.html",
        {
            "analysis": history_service.analysis(settings),
            # Point de comparaison du residu, pas une estimation du vrai
            # overround : il montre ou le constat cesse de tenir.
            "margin_reference": MARGIN_REFERENCE,
            # Le seuil produit sous lequel un second axe ne se justifie pas.
            "equivalence_margin": EQUIVALENCE_MARGIN,
            "labelling": history_service.labelling(settings),
            "stats": history_service.stats(settings),
            "coupon_rates": coupons_service.rates(settings),
            # La seule mesure de la page qui ne melange aucun prix : quatre
            # issues, verifiables sur n'importe quelle feuille de match.
            "set_scores": set_scores_service.report(settings),
            "set_score_options": list(set_scores_service.SCORES),
            "set_score_matrix": set_scores_service.matrix_rows(set_scores_service.report(settings)),
        },
    )


def _picks_context(session_id: int, error: str | None = None, **extra: object) -> dict[str, object]:
    settings = get_settings()
    now = datetime.now(ZoneInfo(settings.tz))
    return {
        "session_id": session_id,
        "session_label": session_service.session_label(session_id, settings),
        "prompts": history_service.list_prompts(session_id, settings),
        "events": history_service.pickable_groups(session_id, settings),
        "tiers": history_service.tiers(settings),
        # Le vocabulaire du « pourquoi » : deux menus fermes plutot que deux
        # champs libres, une faute de frappe faisant sinon disparaitre la ligne
        # de son regroupement sans un mot.
        "angles": history_service.ANGLES,
        "source_levels": history_service.SOURCE_LEVELS,
        # D'ou vient la cote recopiee. Un 1.92 de reference et un 1.92 du book
        # principal ne decrivent pas le meme marche : sans cette colonne, le
        # taux par bande de cote melangeait deux populations.
        "price_sources": history_service.PRICE_SOURCES,
        "picks": history_service.list_picks(session_id, settings),
        "worksheet": history_service.worksheet(session_id, settings),
        "result_labels": list(history_service.RESULT_LABELS.items()),
        "coupons": coupons_service.list_for_session(session_id, settings),
        # Le score exact en sets : la seule mesure de la lecture de la maniere
        # qui soit independante de tout prix. Menus fermes, jamais un champ
        # libre — une faute de frappe ferait disparaitre la ligne du comptage.
        "set_scores": set_scores_service.lot(session_id, settings),
        "set_score_options": list(set_scores_service.SCORES),
        "set_scores_error": None,
        "set_scores_saved": False,
        "available_picks": coupons_service.available_picks(session_id, settings),
        # Un pari se saisit apres l'avoir pose : l'instant present est le bon
        # defaut, et le corriger reste possible.
        "today": now.strftime("%Y-%m-%d"),
        "now_hm": now.strftime("%H:%M"),
        "error": error,
        "coupon_error": None,
        "coupon_notice": None,
        "preview": None,
        "imported": 0,
        "import_failures": [],
        **extra,
    }


@app.post("/history/{session_id}/set-scores/{event_id}", response_class=HTMLResponse)
def save_set_score(
    request: Request,
    session_id: int,
    event_id: int,
    predicted: str = Form(default=""),
    alternate: str = Form(default=""),
    actual: str = Form(default=""),
) -> HTMLResponse:
    """Enregistre le score en sets annonce pour un match, et son resultat reel."""
    _require_session(session_id)
    extra: dict[str, object] = {"set_scores_saved": True}
    try:
        set_scores_service.save(session_id, event_id, predicted, alternate, actual)
    except set_scores_service.SetScoreError as exc:
        extra = {"set_scores_error": str(exc)}
    # Le fragment, jamais la page : rendre `picks.html` dans un `outerHTML`
    # imbriquerait un `<html>` complet dans le `<div>` remplace.
    return templates.TemplateResponse(
        request, "_set_scores.html", _picks_context(session_id, **extra)
    )


@app.get("/history/{session_id}", response_class=HTMLResponse)
def picks_page(request: Request, session_id: int) -> HTMLResponse:
    """Saisie a posteriori des picks joues pour une session."""
    _require_session(session_id)
    return templates.TemplateResponse(request, "picks.html", _picks_context(session_id))


@app.post("/history/{session_id}/picks", response_class=HTMLResponse)
async def add_pick(request: Request, session_id: int) -> HTMLResponse:
    """Ajoute un pick. Une saisie refusee reaffiche la page avec son motif."""
    _require_session(session_id)
    form = {key: str(value) for key, value in (await request.form()).items()}
    try:
        history_service.add_pick(
            session_id,
            tier=form.get("tier", ""),
            market=form.get("market", ""),
            selection=form.get("selection", ""),
            event_id=form.get("event_id", ""),
            price=form.get("price", ""),
            confidence=form.get("confidence", ""),
            stake=form.get("stake", ""),
            angle=form.get("angle", ""),
            source_level=form.get("source_level", ""),
            price_source=form.get("price_source", ""),
            independence_note=form.get("independence_note", ""),
            settings=get_settings(),
        )
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "picks.html", _picks_context(session_id, str(exc))
        )
    return templates.TemplateResponse(request, "picks.html", _picks_context(session_id))


@app.post("/history/{session_id}/picks/preview", response_class=HTMLResponse)
async def preview_picks_import(request: Request, session_id: int) -> HTMLResponse:
    """Lit le tableau de Claude et propose un import. N'ecrit rien en base."""
    _require_session(session_id)
    form = dict(await request.form())
    preview = picks_import_service.build_preview(
        session_id, str(form.get("table", "")), get_settings()
    )
    return templates.TemplateResponse(
        request, "picks.html", _picks_context(session_id, preview=preview)
    )


@app.post("/history/{session_id}/picks/import", response_class=HTMLResponse)
async def confirm_picks_import(request: Request, session_id: int) -> HTMLResponse:
    """Enregistre les lignes cochees dans la proposition d'import."""
    _require_session(session_id)
    form = {key: str(value) for key, value in (await request.form()).items()}
    settings = get_settings()
    created, failures = 0, []
    for index in sorted({key.split("_")[-1] for key in form if key.startswith("keep_")}):
        try:
            history_service.add_pick(
                session_id,
                tier=form.get(f"tier_{index}", ""),
                market=form.get(f"market_{index}", ""),
                selection=form.get(f"selection_{index}", ""),
                event_id=form.get(f"event_{index}", ""),
                price=form.get(f"price_{index}", ""),
                confidence=form.get(f"confidence_{index}", ""),
                angle=form.get(f"angle_{index}", ""),
                source_level=form.get(f"source_{index}", ""),
                price_source=form.get(f"price_source_{index}", ""),
                independence_note=form.get(f"independence_{index}", ""),
                settings=settings,
            )
            created += 1
        except history_service.HistoryError as exc:
            failures.append(f"ligne {index} : {exc}")

    return templates.TemplateResponse(
        request,
        "picks.html",
        _picks_context(session_id, imported=created, import_failures=failures),
    )


# --- Coupons joues ---------------------------------------------------------


async def _attach_screenshot(coupon_id: int, upload: object) -> str | None:
    """Attache une capture si le formulaire en portait une. Renvoie l'erreur eventuelle.

    Une capture refusee n'annule jamais le coupon : le pari a bien ete pose, et
    le perdre parce que l'image ne convient pas serait absurde.
    """
    filename = getattr(upload, "filename", "") or ""
    if not filename:
        return None
    try:
        content = await upload.read()  # type: ignore[attr-defined]
    finally:
        # Starlette adosse chaque envoi a un fichier temporaire : ne pas le
        # fermer laisse un descripteur ouvert jusqu'au ramasse-miettes.
        await upload.close()  # type: ignore[attr-defined]
    try:
        coupons_service.save_screenshot(
            coupon_id,
            filename,
            content,
            getattr(upload, "content_type", "") or "",
            get_settings(),
        )
    except history_service.HistoryError as exc:
        logger.warning("Capture refusee pour le coupon %d : %s", coupon_id, exc)
        return str(exc)
    return None


@app.post("/history/{session_id}/coupons", response_class=HTMLResponse)
async def add_coupon(request: Request, session_id: int) -> HTMLResponse:
    """Enregistre un pari joue a partir de picks deja saisis."""
    _require_session(session_id)
    form = await request.form()
    pick_ids = [int(value) for value in form.getlist("pick_id") if str(value).isdigit()]

    try:
        coupon_id = coupons_service.create(
            session_id,
            pick_ids,
            stake=str(form.get("stake", "")),
            date_value=str(form.get("date", "")),
            time_value=str(form.get("time", "")),
            bookmaker=str(form.get("bookmaker", coupons_service.DEFAULT_BOOKMAKER)),
            note=str(form.get("note", "")),
            settings=get_settings(),
        )
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "picks.html", _picks_context(session_id, coupon_error=str(exc))
        )

    problem = await _attach_screenshot(coupon_id, form.get("screenshot"))
    return templates.TemplateResponse(
        request,
        "picks.html",
        _picks_context(
            session_id,
            coupon_error=problem,
            coupon_notice=None if problem else "Coupon enregistré.",
        ),
    )


@app.post("/coupons/{coupon_id}/screenshot", response_class=HTMLResponse)
async def add_coupon_screenshot(request: Request, coupon_id: int) -> HTMLResponse:
    """Ajoute ou remplace la capture d'un coupon deja enregistre."""
    session_id = _coupon_session(coupon_id)
    form = await request.form()
    problem = await _attach_screenshot(coupon_id, form.get("screenshot"))
    return templates.TemplateResponse(
        request, "picks.html", _picks_context(session_id, coupon_error=problem)
    )


@app.get("/coupons/{coupon_id}/screenshot")
def coupon_screenshot(coupon_id: int) -> FileResponse:
    """Sert la capture d'un coupon. Le nom est revalide avant d'ouvrir le fichier."""
    path = coupons_service.screenshot_path(coupon_id, get_settings())
    if path is None or not path.is_file():
        raise HTTPException(status_code=404, detail="Aucune capture pour ce coupon")
    return FileResponse(path)


@app.post("/picks/{pick_id}/play", response_class=HTMLResponse)
def play_pick(request: Request, pick_id: int) -> HTMLResponse:
    """Transforme une selection en pari simple, en un clic."""
    session_id = _pick_session(pick_id)
    try:
        coupons_service.play_single(pick_id, get_settings())
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "_worksheet.html", _picks_context(session_id, coupon_error=str(exc))
        )
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


@app.post("/coupons/{coupon_id}/settle", response_class=HTMLResponse)
def settle_coupon(request: Request, coupon_id: int, result: str = Form(default="")) -> HTMLResponse:
    """Applique un resultat aux jambes encore en attente d'un coupon."""
    session_id = _coupon_session(coupon_id)
    try:
        coupons_service.settle_all(coupon_id, result, get_settings())
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "_worksheet.html", _picks_context(session_id, coupon_error=str(exc))
        )
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


@app.post("/coupons/{coupon_id}/delete", response_class=HTMLResponse)
def remove_coupon(request: Request, coupon_id: int) -> HTMLResponse:
    """Supprime un coupon. Ses jambes redeviennent des picks libres."""
    session_id = _coupon_session(coupon_id)
    coupons_service.delete(coupon_id, get_settings())
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


def _coupon_session(coupon_id: int) -> int:
    row = db.query_one("SELECT session_id FROM coupons WHERE id = ?", (coupon_id,))
    if row is None:
        raise HTTPException(status_code=404, detail="Coupon inconnu")
    return int(row["session_id"])


@app.post("/picks/{pick_id}/result", response_class=HTMLResponse)
def set_pick_result(
    request: Request, pick_id: int, result: str = Form(default="pending")
) -> HTMLResponse:
    """Met a jour le resultat d'un pick, depuis le selecteur de la ligne."""
    settings = get_settings()
    session_id = _pick_session(pick_id)
    try:
        history_service.set_result(pick_id, result, settings)
    except history_service.HistoryError as exc:
        logger.warning("Resultat refuse : %s", exc)
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


@app.post("/picks/{pick_id}/real-price", response_class=HTMLResponse)
def set_pick_real_price(
    request: Request, pick_id: int, price: str = Form(default="")
) -> HTMLResponse:
    """Enregistre la cote **obtenue** chez le bookmaker principal.

    Elle ne se releve jamais toute seule : ce serait une integration
    transactionnelle avec un bookmaker, interdit n°7 de SPEC.md. Le palier est
    recalcule dessus a l'ecriture.
    """
    settings = get_settings()
    session_id = _pick_session(pick_id)
    try:
        history_service.set_real_price(pick_id, price, settings)
    except history_service.HistoryError as exc:
        logger.warning("Cote obtenue refusee : %s", exc)
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


@app.get("/history/{session_id}/pick-options", response_class=HTMLResponse)
def pick_options(
    request: Request, session_id: int, q: str = "", selected_id: str = ""
) -> HTMLResponse:
    """Options d'un selecteur de match, filtrees par une recherche de libelle.

    Ne rend que les `<option>`, jamais le formulaire : la recherche remplace le
    contenu du menu qui la suit, et rerendre le formulaire ferait perdre le
    focus a chaque frappe.

    `q` vide redonne la liste ordinaire — c'est ce qui permet d'effacer sa
    recherche sans recharger la page.
    """
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "_pick_options.html",
        {
            "events": history_service.pickable_groups(session_id, settings, q),
            "selected_id": _int_or_none(selected_id),
        },
    )


@app.get("/picks/{pick_id}/event", response_class=HTMLResponse)
def edit_pick_event(request: Request, pick_id: int) -> HTMLResponse:
    """Selecteur de match d'une selection, charge a la demande.

    A la demande justement : la feuille de session porte quinze selections et le
    menu compte cent matchs. Les rendre tous d'avance alourdirait chaque
    rafraichissement du plan de travail pour un geste qui se fait une fois.
    """
    settings = get_settings()
    pick = history_service.get_pick(pick_id, settings)
    if pick is None:
        raise HTTPException(status_code=404, detail="Pick inconnu")
    return templates.TemplateResponse(
        request,
        "_pick_event.html",
        {"pick": pick, "events": history_service.pickable_groups(pick.session_id, settings)},
    )


@app.post("/picks/{pick_id}/event", response_class=HTMLResponse)
def set_pick_event(
    request: Request, pick_id: int, event_id: str = Form(default="")
) -> HTMLResponse:
    """Rattache une selection a un match, ou l'en detache."""
    session_id = _pick_session(pick_id)
    try:
        history_service.set_event(pick_id, event_id, get_settings())
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "_worksheet.html", _picks_context(session_id, coupon_error=str(exc))
        )
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


@app.post("/picks/{pick_id}/delete", response_class=HTMLResponse)
def remove_pick(request: Request, pick_id: int) -> HTMLResponse:
    session_id = _pick_session(pick_id)
    history_service.delete_pick(pick_id, get_settings())
    return templates.TemplateResponse(request, "_worksheet.html", _picks_context(session_id))


def _pick_session(pick_id: int) -> int:
    row = db.query_one(
        "SELECT session_id FROM picks WHERE id = ?", (pick_id,), settings=get_settings()
    )
    if row is None:
        raise HTTPException(status_code=404, detail="Pick inconnu")
    return int(row["session_id"])


# --- Reglages : templates et bandes de cotes -------------------------------


def _settings_context(**overrides: object) -> dict[str, object]:
    settings = get_settings()
    # Lu **une fois** : il sert l'avancement du gate et la reference des cibles
    # relatives, et deux appels donneraient deux photos de la meme chose.
    _recul = history_service.feedback(settings)
    available = prompt_service.list_templates()
    name = str(overrides.pop("template_name", "") or "") or prompt_service.DEFAULT_TEMPLATE
    if name not in available:
        name = available[0] if available else prompt_service.DEFAULT_TEMPLATE

    context: dict[str, object] = {
        "templates": available,
        "template_name": name,
        "template_body": prompt_service.read_template(name) if available else "",
        "default_template": prompt_service.DEFAULT_TEMPLATE,
        "template_error": None,
        "template_saved": None,
        "tiers": prompt_service.load_tiers(settings),
        "tiers_error": None,
        "tiers_saved": False,
        "bands": prompt_service.load_bands(settings, reference=_recul.global_rate),
        "bands_error": None,
        "bands_saved": False,
        # Ou en est le recul, et ce qu'il manque pour ouvrir le gate. C'est le
        # seul reglage dont l'effet est differe : sans cet avancement, on ne
        # pouvait pas mesurer sa distance a l'activation. Les deux seuils
        # eux-memes se reglent desormais dans la table des seuils.
        "feedback": _recul,
        "preferences": prompt_service.read_preference(prompt_service.PREFERENCE_NOTES, settings),
        "preferences_error": None,
        "preferences_saved": False,
        # Les familles de marches : ce qui est deja classe, et ce qui reclame
        # une decision. Un marche inconnu n'est jamais range d'office dans
        # « Autre » — ce serait lire un oubli comme une decision.
        "market_families": [
            {"key": key, "family": family}
            for key, family in sorted(market_families_service.load(settings).items())
        ],
        "market_todo": market_families_service.unclassified(settings),
        "market_family_options": market_families_service.FAMILIES,
        "families_saved": False,
        # Les seuils numeriques : le registre les declare, l'ecran les rend
        # sans les connaitre un par un.
        "thresholds": thresholds_service.current(settings),
        "thresholds_saved": False,
    }
    context.update(overrides)
    return context


@app.get("/settings", response_class=HTMLResponse)
def settings_page(request: Request, template: str | None = None) -> HTMLResponse:
    """Edition des templates de prompt et des bandes de cotes."""
    return templates.TemplateResponse(
        request, "settings.html", _settings_context(template_name=template)
    )


@app.post("/settings/templates", response_class=HTMLResponse)
async def save_template(request: Request) -> HTMLResponse:
    """Enregistre un template apres compilation. Un template casse est refuse."""
    form = {key: str(value) for key, value in (await request.form()).items()}
    name = form.get("name", "")
    body = form.get("body", "")
    try:
        saved = prompt_service.save_template(name, body)
    except prompt_service.CustomizationError as exc:
        return templates.TemplateResponse(
            request,
            "_templates.html",
            _settings_context(template_error=str(exc), template_body=body, template_name=name),
        )
    return templates.TemplateResponse(
        request, "_templates.html", _settings_context(template_name=saved, template_saved=saved)
    )


@app.post("/settings/templates/delete", response_class=HTMLResponse)
def remove_template(request: Request, name: str = Form(default="")) -> HTMLResponse:
    try:
        prompt_service.delete_template(name)
    except prompt_service.CustomizationError as exc:
        return templates.TemplateResponse(
            request, "_templates.html", _settings_context(template_error=str(exc))
        )
    return templates.TemplateResponse(request, "_templates.html", _settings_context())


@app.post("/settings/preferences", response_class=HTMLResponse)
def save_preferences(request: Request, preferences: str = Form(default="")) -> HTMLResponse:
    """Enregistre les consignes permanentes recopiees dans chaque prompt."""
    try:
        prompt_service.save_preference(prompt_service.PREFERENCE_NOTES, preferences, get_settings())
    except prompt_service.CustomizationError as exc:
        return templates.TemplateResponse(
            request,
            "_preferences.html",
            _settings_context(preferences_error=str(exc), preferences=preferences),
        )
    return templates.TemplateResponse(
        request, "_preferences.html", _settings_context(preferences_saved=True)
    )


@app.post("/settings/thresholds", response_class=HTMLResponse)
async def save_threshold(request: Request) -> HTMLResponse:
    """Enregistre un seuil numerique. Hors bornes, il revient a son defaut."""
    form = await request.form()
    thresholds_service.save(str(form.get("key", "")), str(form.get("value", "")), get_settings())
    return templates.TemplateResponse(
        request, "_thresholds.html", _settings_context(thresholds_saved=True)
    )


@app.post("/settings/families", response_class=HTMLResponse)
async def save_market_family(request: Request) -> HTMLResponse:
    """Classe un libelle de marche dans une famille, ou retire son classement."""
    form = await request.form()
    market_families_service.set_family(
        str(form.get("market_key", "")), str(form.get("family", "")), get_settings()
    )
    return templates.TemplateResponse(
        request, "_families.html", _settings_context(families_saved=True)
    )


@app.post("/settings/bands", response_class=HTMLResponse)
async def save_bands(request: Request) -> HTMLResponse:
    """Enregistre les bandes cibles de confiance, apres controle des bornes."""
    form = await request.form()
    levels = form.getlist("level")
    rows = [
        {
            "level": _int_or_none(level),
            "low": _float_or_none(form.getlist("low")[index]),
            "high": _float_or_none(form.getlist("high")[index]),
        }
        for index, level in enumerate(levels)
    ]

    try:
        prompt_service.save_bands(rows, get_settings())
    except prompt_service.CustomizationError as exc:
        return templates.TemplateResponse(
            request, "_bands.html", _settings_context(bands_error=str(exc))
        )
    return templates.TemplateResponse(request, "_bands.html", _settings_context(bands_saved=True))


@app.post("/settings/tiers", response_class=HTMLResponse)
async def save_tiers(request: Request) -> HTMLResponse:
    """Enregistre les bandes de cotes, apres controle de coherence des bornes."""
    form = await request.form()
    keys = form.getlist("key")
    rows = [
        {
            "key": key,
            "emoji": form.getlist("emoji")[index],
            "label": form.getlist("label")[index],
            "min_price": _float_or_none(form.getlist("min_price")[index]),
            "max_price": _float_or_none(form.getlist("max_price")[index]),
            "quota_min": _int_or_none(form.getlist("quota_min")[index]) or 0,
            "quota_max": _int_or_none(form.getlist("quota_max")[index]) or 0,
        }
        for index, key in enumerate(keys)
    ]

    try:
        prompt_service.save_tiers(rows, get_settings())
    except prompt_service.CustomizationError as exc:
        return templates.TemplateResponse(
            request, "_tiers.html", _settings_context(tiers_error=str(exc))
        )
    return templates.TemplateResponse(request, "_tiers.html", _settings_context(tiers_saved=True))


def _float_or_none(value: str) -> float | None:
    text = (value or "").strip().replace(",", ".")
    try:
        return float(text)
    except (TypeError, ValueError):
        return None


@app.get("/health")
def health() -> JSONResponse:
    """Etat de l'application : base de donnees et configuration (sans secrets)."""
    settings = get_settings()
    db_state = db.health(settings)
    payload = {
        "status": "ok" if db_state["ok"] else "degraded",
        "version": __version__,
        "db": db_state,
        "config": settings.public_dict(),
    }
    return JSONResponse(payload, status_code=200 if db_state["ok"] else 503)
