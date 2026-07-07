"""OrbitCast FastAPI application.

Phase 1 adds the deterministic sky view. Forecast and Dish Doctor routes come in
later phases per CLAUDE.md §7.3.
"""

import os

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from .routes import forecast, skyview
from .routes import map as map_routes

app = FastAPI(title="OrbitCast API", version="0.1.0")

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


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (CLAUDE.md §7.3)."""
    return {"status": "ok"}
