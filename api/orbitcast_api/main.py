"""OrbitCast FastAPI application.

Phase 0 exposes only liveness. Feature routes (skyview, forecast, dish-doctor)
are added in later phases per CLAUDE.md §7.3.
"""

from fastapi import FastAPI

app = FastAPI(title="OrbitCast API", version="0.1.0")


@app.get("/healthz")
def healthz() -> dict[str, str]:
    """Liveness probe (CLAUDE.md §7.3)."""
    return {"status": "ok"}
