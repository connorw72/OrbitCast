"""OrbitCast FastAPI application.

Phase 1 adds the deterministic sky view. Forecast and Dish Doctor routes come in
later phases per CLAUDE.md §7.3.
"""

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .db import get_pool, init_schema
from .routes import dish_doctor, forecast, measurements, probe, skyview, users
from .routes import map as map_routes

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Self-migrate the serving store on startup (§7.2: idempotent DDL).

    Best-effort: if the DB is momentarily unreachable we log and continue rather
    than taking the whole API down — the deterministic sky view and forecast paths
    don't need Postgres, and `restart: unless-stopped` plus the compose healthcheck
    cover recovery (§9 F10). The write endpoints will surface a 5xx until it's back.
    """
    try:
        with get_pool().connection() as conn:
            init_schema(conn)
    except Exception:
        logger.warning("Serving-store schema init failed; DB-backed routes degraded", exc_info=True)
    yield


app = FastAPI(title="OrbitCast API", version="0.1.0", lifespan=lifespan)

# The frontend is a static bundle served from another origin (Vercel), so it needs
# cross-origin access to the API. Configurable; permissive default for local dev.
_origins = os.environ.get("ORBITCAST_CORS_ORIGINS", "*").split(",")
app.add_middleware(
    CORSMiddleware,
    allow_origins=_origins,
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)

app.include_router(skyview.router)
app.include_router(forecast.router)
app.include_router(map_routes.router)
app.include_router(users.router)
app.include_router(measurements.router)
app.include_router(dish_doctor.router)
app.include_router(probe.router)


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (CLAUDE.md §7.3)."""
    return {"status": "ok"}
