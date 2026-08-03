"""Application FastAPI : cycle de vie, routes. Aucune logique metier ici."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import datetime
from zoneinfo import ZoneInfo

import httpx
from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from jinja2 import TemplateNotFound

from . import __version__, db
from .config import PACKAGE_DIR, get_settings
from .providers.apifootball import APIFootballClient
from .providers.base import ProviderError
from .providers.oddsapi import OddsAPIClient
from .scheduler import build_scheduler
from .services import board as board_service
from .services import competitions as competitions_service
from .services import enrich as enrich_service
from .services import history as history_service
from .services import manual as manual_service
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


def _competitions_context(report: object | None = None) -> dict[str, object]:
    return {"competitions": competitions_service.list_all(get_settings()), "report": report}


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


@app.post("/competitions/{competition_id}/active", response_class=HTMLResponse)
def competition_toggle(
    request: Request, competition_id: int, active: str | None = Form(default=None)
) -> HTMLResponse:
    """Active ou desactive une competition : seules les actives sont scannees."""
    competitions_service.set_active(competition_id, active is not None, get_settings())
    return templates.TemplateResponse(request, "_competitions.html", _competitions_context())


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


# --- Historique et picks ---------------------------------------------------


@app.get("/history", response_class=HTMLResponse)
def history_page(request: Request) -> HTMLResponse:
    """Sessions passees et taux de reussite. Aucun indicateur financier."""
    settings = get_settings()
    return templates.TemplateResponse(
        request,
        "history.html",
        {
            "sessions": history_service.list_sessions(settings),
            "stats": history_service.stats(settings),
        },
    )


def _picks_context(session_id: int, error: str | None = None) -> dict[str, object]:
    settings = get_settings()
    return {
        "session_id": session_id,
        "session_label": session_service.session_label(session_id, settings),
        "prompts": history_service.list_prompts(session_id, settings),
        "events": history_service.session_events(session_id, settings),
        "tiers": history_service.tiers(settings),
        "picks": history_service.list_picks(session_id, settings),
        "result_labels": list(history_service.RESULT_LABELS.items()),
        "error": error,
    }


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
            settings=get_settings(),
        )
    except history_service.HistoryError as exc:
        return templates.TemplateResponse(
            request, "picks.html", _picks_context(session_id, str(exc))
        )
    return templates.TemplateResponse(request, "picks.html", _picks_context(session_id))


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
    return templates.TemplateResponse(request, "_picks.html", _picks_context(session_id))


@app.post("/picks/{pick_id}/delete", response_class=HTMLResponse)
def remove_pick(request: Request, pick_id: int) -> HTMLResponse:
    session_id = _pick_session(pick_id)
    history_service.delete_pick(pick_id, get_settings())
    return templates.TemplateResponse(request, "_picks.html", _picks_context(session_id))


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
