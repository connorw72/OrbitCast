"""POST /v1/users — mint an anonymous token (CLAUDE.md §7.3, D12).

The hook that converts visitors into contributors: no email, no signup. We return
a random bearer token once and persist only its SHA-256 hash plus the user's
declared res-5 cell. Rate-limited per client IP to blunt bulk minting.
"""

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from psycopg import Connection

from ..db import get_conn
from ..deps import client_ip, get_user_rate_limiter
from ..ratelimit import RateLimiter
from ..schemas import UserCreate, UserCreated
from ..security import hash_token, mint_token

router = APIRouter()


@router.post("/v1/users")
def create_user(
    body: UserCreate,
    request: Request,
    conn: Annotated[Connection, Depends(get_conn)],
    limiter: Annotated[RateLimiter, Depends(get_user_rate_limiter)],
) -> UserCreated:
    if not limiter.allow(client_ip(request)):
        raise HTTPException(status_code=429, detail="Too many requests")

    token = mint_token()
    row = conn.execute(
        "INSERT INTO users (token_hash, h3_cell) VALUES (%s, %s) RETURNING id",
        (hash_token(token), body.h3_cell),
    ).fetchone()
    assert row is not None  # RETURNING always yields a row on a successful insert
    return UserCreated(user_id=str(row[0]), token=token, h3_cell=body.h3_cell)
