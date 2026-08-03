"""Application FastAPI : cycle de vie, routes. Aucune logique metier ici."""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.responses import JSONResponse

from . import __version__, db
from .config import get_settings

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)-8s %(name)s — %(message)s",
)
logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    applied = db.run_migrations(settings)
    if applied:
        logger.info("Migrations appliquees au demarrage : %s", ", ".join(applied))
    else:
        logger.info("Schema deja a jour")
    logger.info("Base : %s", settings.db_path_absolute)
    yield


app = FastAPI(title="MyAssistantBet", version=__version__, lifespan=lifespan)


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
