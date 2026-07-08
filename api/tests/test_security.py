"""Anonymous-token minting + hashing (CLAUDE.md §7.2, D12).

The server stores only `token_hash` — never the raw token — so a DB leak cannot
impersonate a user. The raw token is returned to the client exactly once.
"""

from orbitcast_api.security import hash_token, mint_token


def test_mint_token_is_url_safe_and_high_entropy() -> None:
    token = mint_token()
    # URL-safe so it survives an Authorization header untouched.
    assert token.replace("-", "").replace("_", "").isalnum()
    # secrets.token_urlsafe(32) → ~43 chars; guard against a trivially short token.
    assert len(token) >= 40


def test_mint_token_is_unique_per_call() -> None:
    assert mint_token() != mint_token()


def test_hash_token_is_deterministic_sha256_hex() -> None:
    token = "example-token"
    # Stable across calls (so lookup by hash works) and a 64-char sha256 hex digest.
    digest = hash_token(token)
    assert digest == hash_token(token)
    assert len(digest) == 64
    assert all(c in "0123456789abcdef" for c in digest)


def test_hash_token_does_not_leak_the_token() -> None:
    token = mint_token()
    assert token not in hash_token(token)
