"""Application FastAPI : cycle de vie, routes. Aucune logique metier ici."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import httpx
from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates

from . import __version__, db
from .config import PACKAGE_DIR, get_settings
from .providers.oddsapi import OddsAPIClient
from .scheduler import build_scheduler
from .services import board as board_service
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
