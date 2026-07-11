"""Tests for the dish reporter (CLAUDE.md §4.3.1, §10 Phase 4).

Everything except the actual dish gRPC call is exercised here: cell parsing,
status→measurement mapping, the bounded sample buffer with batch POSTing, and the
HTTP submission path against a real local HTTP server. The gRPC fetch itself needs
a physical dish and stays a thin untested adapter.
"""

import json
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest
from orbitcast_reporter import (
    Reporter,
    ReporterError,
    mint_token,
    parse_cell,
    post_batch,
    status_to_measurement,
)

TS = datetime(2026, 7, 10, 12, 0, 30, tzinfo=UTC)
CELL = 599686042433355775  # a valid res-5 H3 cell as BIGINT


# --- parse_cell -------------------------------------------------------------


def test_parse_cell_decimal() -> None:
    assert parse_cell("599686042433355775") == 599686042433355775


def test_parse_cell_h3_hex_string() -> None:
    # the canonical lowercase-hex H3 index form shown by h3-py / the web app
    assert parse_cell("851f8d37fffffff") == int("851f8d37fffffff", 16)


def test_parse_cell_rejects_garbage() -> None:
    with pytest.raises(ValueError):
        parse_cell("not-a-cell")


def test_parse_cell_rejects_nonpositive() -> None:
    with pytest.raises(ValueError):
        parse_cell("0")


# --- status_to_measurement ---------------------------------------------------


def full_status() -> dict:
    return {
        "pop_ping_latency_ms": 38.5,
        "downlink_throughput_bps": 145_000_000.0,
        "uplink_throughput_bps": 12_500_000.0,
        "fraction_obstructed": 0.021,
        "hardware_version": "rev4_prod3",
    }


def test_status_to_measurement_maps_fields() -> None:
    m = status_to_measurement(full_status(), ts=TS, h3_cell=CELL)
    assert m == {
        "ts": "2026-07-10T12:00:30+00:00",
        "h3_cell": CELL,
        "source": "reporter",
        "latency_ms": 38.5,
        "dl_mbps": 145.0,
        "ul_mbps": 12.5,
        "obstruction_pct": 2.1,
        "hw_version": "rev4_prod3",
    }


def test_status_to_measurement_missing_fields_are_none() -> None:
    m = status_to_measurement({}, ts=TS, h3_cell=CELL)
    assert m["latency_ms"] is None
    assert m["dl_mbps"] is None
    assert m["ul_mbps"] is None
    assert m["obstruction_pct"] is None
    assert m["hw_version"] is None


def test_status_to_measurement_zero_latency_is_unknown() -> None:
    # protobuf scalars default to 0 when unset; 0 ms latency is physically
    # impossible, so it must map to "unknown", not a perfect measurement
    status = full_status() | {"pop_ping_latency_ms": 0.0}
    assert status_to_measurement(status, ts=TS, h3_cell=CELL)["latency_ms"] is None


def test_status_to_measurement_zero_throughput_is_valid() -> None:
    # an idle dish legitimately reports 0 bps
    status = full_status() | {"downlink_throughput_bps": 0.0}
    assert status_to_measurement(status, ts=TS, h3_cell=CELL)["dl_mbps"] == 0.0


def test_status_to_measurement_clamps_obstruction() -> None:
    status = full_status() | {"fraction_obstructed": 1.5}
    assert status_to_measurement(status, ts=TS, h3_cell=CELL)["obstruction_pct"] == 100.0


# --- Reporter (poll/batch/flush loop) ----------------------------------------


class FakePoster:
    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.batches: list[list[dict]] = []

    def __call__(self, measurements: list[dict]) -> int:
        if self.fail:
            raise ReporterError("api unreachable")
        self.batches.append(list(measurements))
        return len(measurements)


def make_reporter(poster: FakePoster, **kwargs) -> Reporter:
    return Reporter(
        fetch=lambda: full_status(),
        post=poster,
        h3_cell=CELL,
        **kwargs,
    )


def test_reporter_posts_when_batch_full() -> None:
    poster = FakePoster()
    reporter = make_reporter(poster, batch_size=3)
    assert reporter.tick() is None
    assert reporter.tick() is None
    assert reporter.tick() == 3
    assert len(poster.batches) == 1
    assert [m["source"] for m in poster.batches[0]] == ["reporter"] * 3
    assert reporter.pending == 0


def test_reporter_keeps_samples_when_post_fails() -> None:
    poster = FakePoster(fail=True)
    reporter = make_reporter(poster, batch_size=2)
    reporter.tick()
    assert reporter.tick() is None  # post failed, samples retained
    assert reporter.pending == 2
    poster.fail = False
    assert reporter.tick() == 3  # backlog + new sample all flushed
    assert reporter.pending == 0


def test_reporter_buffer_is_bounded() -> None:
    poster = FakePoster(fail=True)
    reporter = make_reporter(poster, batch_size=2, max_buffer=5)
    for _ in range(9):
        reporter.tick()
    assert reporter.pending == 5  # oldest dropped, newest kept


def test_reporter_tolerates_fetch_errors() -> None:
    poster = FakePoster()

    def broken_fetch() -> dict:
        raise ConnectionError("dish rebooting")

    reporter = Reporter(fetch=broken_fetch, post=poster, h3_cell=CELL, batch_size=2)
    assert reporter.tick() is None
    assert reporter.pending == 0


def test_reporter_splits_oversized_flush() -> None:
    # the API caps a batch at 1000 measurements; a long outage backlog must be
    # flushed in chunks, not rejected wholesale
    poster = FakePoster(fail=True)
    reporter = make_reporter(poster, batch_size=2, max_buffer=2500)
    for _ in range(2400):
        reporter.tick()
    poster.fail = False
    accepted = reporter.tick()
    assert accepted == 2401
    assert all(len(batch) <= 1000 for batch in poster.batches)
    assert reporter.pending == 0


# --- HTTP submission against a real local server -----------------------------


class _ApiHandler(BaseHTTPRequestHandler):
    requests: list[dict] = []
    status_code = 200

    def do_POST(self) -> None:  # noqa: N802 (stdlib API)
        body = self.rfile.read(int(self.headers["Content-Length"]))
        type(self).requests.append(
            {
                "path": self.path,
                "auth": self.headers.get("Authorization"),
                "content_type": self.headers.get("Content-Type"),
                "body": json.loads(body),
            }
        )
        payload = json.loads(body)
        if self.path == "/v1/users":
            response = {"user_id": "u1", "token": "tok_abc", "h3_cell": payload.get("h3_cell")}
        else:
            response = {"accepted": len(payload.get("measurements", []))}
        data = json.dumps(response).encode()
        self.send_response(type(self).status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args: object) -> None:  # keep test output pristine
        pass


@pytest.fixture
def api_server():
    _ApiHandler.requests = []
    _ApiHandler.status_code = 200
    server = HTTPServer(("127.0.0.1", 0), _ApiHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield f"http://127.0.0.1:{server.server_port}"
    server.shutdown()
    thread.join()


def test_post_batch_sends_bearer_json(api_server: str) -> None:
    measurements = [status_to_measurement(full_status(), ts=TS, h3_cell=CELL)]
    accepted = post_batch(api_server, "tok_abc", measurements)
    assert accepted == 1
    (req,) = _ApiHandler.requests
    assert req["path"] == "/v1/measurements"
    assert req["auth"] == "Bearer tok_abc"
    assert req["content_type"] == "application/json"
    assert req["body"] == {"measurements": measurements}


def test_post_batch_raises_on_server_error(api_server: str) -> None:
    _ApiHandler.status_code = 500
    with pytest.raises(ReporterError):
        post_batch(api_server, "tok_abc", [status_to_measurement({}, ts=TS, h3_cell=CELL)])


def test_mint_token(api_server: str) -> None:
    token = mint_token(api_server, CELL)
    assert token == "tok_abc"
    (req,) = _ApiHandler.requests
    assert req["path"] == "/v1/users"
    assert req["body"] == {"h3_cell": CELL}
