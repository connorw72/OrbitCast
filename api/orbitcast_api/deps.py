"""Shared FastAPI dependencies for the write endpoints (CLAUDE.md §7.3).

The rate limiters are process singletons (in-memory, §7.1/§8 — no Redis). Exposing
them through dependency functions lets tests swap in tight or fresh limiters via
``app.dependency_overrides`` without touching production state.
"""

from fastapi import Request

from .ratelimit import RateLimiter

# Anonymous token minting: 30 per hour per client IP is ample for a real visitor
# and throttles bulk account creation (§7.3).
_user_rate_limiter = RateLimiter(max_requests=30, window_seconds=3600)

# Measurement ingest: 120 batch POSTs per minute per token. The dish reporter
# batches, so this is generous for honest use while capping floods.
_measurement_rate_limiter = RateLimiter(max_requests=120, window_seconds=60)


def get_user_rate_limiter() -> RateLimiter:
    return _user_rate_limiter


def get_measurement_rate_limiter() -> RateLimiter:
    return _measurement_rate_limiter


def client_ip(request: Request) -> str:
    """Best-effort client key for IP-based limits. We never store it (D12); it is
    used only as an ephemeral rate-limit bucket."""
    return request.client.host if request.client else "unknown"
