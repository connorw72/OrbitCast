"""WS /v1/probe — browser latency probe (CLAUDE.md §7.3, §4.3.2).

A pure round-trip reflector: the client times how long each frame takes to bounce
back and derives latency samples client-side. No DB, no auth — the actual data
submission is the already-authenticated POST /v1/measurements. So this test builds
a plain TestClient (the DB-backed ``client`` fixture and its Docker dependency are
unnecessary here).
"""

from fastapi.testclient import TestClient
from orbitcast_api.main import app


def test_probe_echoes_a_single_frame() -> None:
    with TestClient(app).websocket_connect("/v1/probe") as ws:
        ws.send_text("ping-0")
        assert ws.receive_text() == "ping-0"


def test_probe_echoes_frames_in_order() -> None:
    with TestClient(app).websocket_connect("/v1/probe") as ws:
        for i in range(5):
            ws.send_text(f"ping-{i}")
            assert ws.receive_text() == f"ping-{i}"
