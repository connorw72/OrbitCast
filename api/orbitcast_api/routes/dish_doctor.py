"""GET /v1/dish-doctor — per-user underperformance verdict (CLAUDE.md §6.4, §7.3).

Authenticated: the caller's anonymous token identifies whose measurements to score
against the promoted model's q10 band. 503 before any model is promoted rather
than fabricating a verdict. The statistics live in ``orbitcast_ml.anomaly`` and the
DB read + inference wiring in ``orbitcast_api.dish_doctor``; this route is thin.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from psycopg import Connection

from ..auth import require_user
from ..config import get_settings
from ..db import get_conn
from ..dish_doctor import score_dish, to_response
from ..forecast import load_promoted_model
from ..satellites import get_satellites
from ..schemas import DishDoctorResponse

router = APIRouter()


@router.get("/v1/dish-doctor")
def dish_doctor(
    user_id: Annotated[str, Depends(require_user)],
    conn: Annotated[Connection, Depends(get_conn)],
) -> DishDoctorResponse:
    settings = get_settings()
    model = load_promoted_model(settings.models_dir)
    if model is None:
        raise HTTPException(status_code=503, detail="Forecast model not available yet")

    verdict = score_dish(conn, user_id, model, get_satellites(), settings.marts_dir)
    return to_response(verdict)
