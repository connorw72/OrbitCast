"""Anonymous-token auth primitives (CLAUDE.md §7.2, D12).

No email, no signup: a user is a random bearer token. We persist only its SHA-256
hash, so the DB never holds anything that can impersonate a user. The raw token is
shown to the client once, at mint time, and is unrecoverable afterward.
"""

import hashlib
import secrets


def mint_token() -> str:
    """A fresh, URL-safe anonymous token (~43 chars, 256 bits of entropy)."""
    return secrets.token_urlsafe(32)


def hash_token(token: str) -> str:
    """SHA-256 hex digest used as the stored `token_hash` and lookup key.

    A plain (unsalted) hash is deliberate: tokens are already high-entropy random
    strings, so they are not brute-forceable and a per-token salt would break the
    single-column lookup. Constant-time comparison is unnecessary because we look
    up *by* this digest rather than comparing a stored secret.
    """
    return hashlib.sha256(token.encode("utf-8")).hexdigest()
