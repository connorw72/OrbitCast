"""In-process rate limiter (CLAUDE.md §7.3 — both write endpoints are rate-limited;
§7.1/§8 reject Redis: a single host with an in-memory limiter is the right scale).

A sliding-window limiter keyed by an arbitrary string (client IP for token minting,
token for measurement ingest). An injectable clock keeps the tests deterministic.
"""

from orbitcast_api.ratelimit import RateLimiter


def test_allows_up_to_the_limit_then_blocks() -> None:
    t = [1000.0]
    rl = RateLimiter(max_requests=3, window_seconds=60, now=lambda: t[0])
    assert rl.allow("ip-a") is True
    assert rl.allow("ip-a") is True
    assert rl.allow("ip-a") is True
    # 4th request inside the window is denied.
    assert rl.allow("ip-a") is False


def test_keys_are_independent() -> None:
    t = [1000.0]
    rl = RateLimiter(max_requests=1, window_seconds=60, now=lambda: t[0])
    assert rl.allow("ip-a") is True
    assert rl.allow("ip-a") is False
    # A different key has its own budget.
    assert rl.allow("ip-b") is True


def test_window_slides_and_frees_capacity() -> None:
    t = [1000.0]
    rl = RateLimiter(max_requests=2, window_seconds=60, now=lambda: t[0])
    assert rl.allow("k") is True
    assert rl.allow("k") is True
    assert rl.allow("k") is False
    # Advance past the window: old hits expire and capacity returns.
    t[0] += 61
    assert rl.allow("k") is True


def test_partial_expiry_only_drops_stale_hits() -> None:
    t = [1000.0]
    rl = RateLimiter(max_requests=2, window_seconds=60, now=lambda: t[0])
    assert rl.allow("k") is True  # hit at t=1000
    t[0] += 30
    assert rl.allow("k") is True  # hit at t=1030, window full
    assert rl.allow("k") is False
    t[0] += 31  # t=1061: the t=1000 hit expired, the t=1030 hit remains
    assert rl.allow("k") is True
    assert rl.allow("k") is False
