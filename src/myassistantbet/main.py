"""Application FastAPI : cycle de vie, routes. Aucune logique metier ici."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound

from . import __version__, db
from .config import PACKAGE_DIR, get_settings
from .providers.apifootball import APIFootballClient
from .providers.oddsapi import OddsAPIClient
from .scheduler import build_scheduler
from .services import board as board_service
from .services import enrich as enrich_service
from .services import mapping_ui as mapping_service
from .services import prompt as prompt_service
from .services import session as session_service
from .services.scan import run_scan

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)

templates = Jinja2Templates(directory=PACKAGE_DIR / "templates")


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


def _filters(request: Request) -> board_service.Filters:
    """Convertit les parametres de requete en criteres de filtrage.

    Les parametres inconnus ou mal formes sont ignores : un lien bricole a la
    main ne doit pas renvoyer une erreur 500.
    """
    params = request.query_params
    return board_service.Filters(
        sport=params.get("sport", "").strip(),
        competition_id=_int_or_none(params.get("competition_id")),
        hour_from=_int_or_none(params.get("hour_from")),
        hour_to=_int_or_none(params.get("hour_to")),
        text=params.get("text", "").strip(),
    )


def _int_or_none(value: str | None) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _render_board(request: Request, template: str, report: object | None = None) -> HTMLResponse:
    view = board_service.build_view(_filters(request), get_settings())
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


# --- Shortlist -------------------------------------------------------------

#: Progression des enrichissements en cours, par session. Mono-utilisateur,
#: un seul process : un dictionnaire en memoire suffit et evite une table.
ENRICH_PROGRESS: dict[int, enrich_service.EnrichReport] = {}

#: Taches d'enrichissement en vol. asyncio ne garde qu'une reference faible sur
#: les taches : sans cet ensemble, une tache peut etre collectee en cours de route.
ENRICH_TASKS: set[asyncio.Task[None]] = set()


def _shortlist_context(session_id: int) -> dict[str, object]:
    settings = get_settings()
    return {
        "view": session_service.build_view(session_id, settings),
        "estimate": enrich_service.build_estimate(session_id, settings),
        "progress": ENRICH_PROGRESS.get(session_id),
    }


def _require_session(session_id: int) -> None:
    if not session_service.session_exists(session_id, get_settings()):
        raise HTTPException(status_code=404, detail="Session inconnue")


@app.get("/session/{session_id}", response_class=HTMLResponse)
def shortlist(request: Request, session_id: int) -> HTMLResponse:
    """Shortlist : les matchs coches, regroupes par sport."""
    _require_session(session_id)
    return templates.TemplateResponse(request, "shortlist.html", _shortlist_context(session_id))


@app.post("/session/{session_id}/events/{event_id}/note")
def save_note(session_id: int, event_id: int, note: str = Form(default="")) -> PlainTextResponse:
    """Note libre d'un evenement, injectee telle quelle sous NOTE PERSO."""
    _require_session(session_id)
    session_service.set_note(session_id, event_id, note, get_settings())
    return PlainTextResponse("", status_code=204)


@app.post("/session/{session_id}/enrich", response_class=HTMLResponse)
async def start_enrich(request: Request, session_id: int) -> HTMLResponse:
    """Lance l'etage B en tache de fond et rend la zone de progression."""
    _require_session(session_id)
    settings = get_settings()
    running = ENRICH_PROGRESS.get(session_id)
    if running is None or running.finished:
        estimate = enrich_service.build_estimate(session_id, settings)
        ENRICH_PROGRESS[session_id] = enrich_service.EnrichReport(total=estimate.events)
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


# --- Mapping des equipes ---------------------------------------------------


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


def _build_prompt(session_id: int, template_name: str | None) -> prompt_service.RenderedPrompt:
    settings = get_settings()
    name = template_name or prompt_service.DEFAULT_TEMPLATE
    try:
        return prompt_service.build_prompt(session_id, name, settings)
    except TemplateNotFound as exc:
        raise HTTPException(status_code=404, detail=f"Template inconnu : {name}") from exc


@app.get("/session/{session_id}/prompt", response_class=HTMLResponse)
def prompt_page(request: Request, session_id: int, template: str | None = None) -> HTMLResponse:
    """Prompt rendu, sauvegarde en base a chaque generation."""
    _require_session(session_id)
    settings = get_settings()
    rendered = _build_prompt(session_id, template)
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
        },
    )


@app.get("/session/{session_id}/prompt.md")
def prompt_download(session_id: int, template: str | None = None) -> PlainTextResponse:
    """Le meme prompt, en telechargement Markdown."""
    _require_session(session_id)
    rendered = _build_prompt(session_id, template)
    filename = f"session-{session_id}.md"
    return PlainTextResponse(
        rendered.body,
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


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
