"""Anonymous bearer-token authentication (CLAUDE.md §7.2, §7.3).

The Authorization header carries the raw token; we look the user up by the token's
SHA-256 hash (the only form we store, D12). ``require_user`` is a FastAPI dependency
returning the authenticated user's id, or raising 401.
"""

from typing import Annotated

from fastapi import Depends, Header, HTTPException
from psycopg import Connection

from .db import get_conn
from .security import hash_token


def require_user(
    conn: Annotated[Connection, Depends(get_conn)],
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing or malformed bearer token")
    token = authorization.removeprefix("Bearer ").strip()

    row = conn.execute(
        "SELECT id FROM users WHERE token_hash = %s", (hash_token(token),)
    ).fetchone()
    if row is None:
        raise HTTPException(status_code=401, detail="Invalid token")
    return str(row[0])
