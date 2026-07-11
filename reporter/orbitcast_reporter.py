#!/usr/bin/env python3
"""OrbitCast dish reporter (CLAUDE.md §4.3.1) — run it on the LAN your dish is on.

Polls the Starlink dish's local gRPC status endpoint (192.168.100.1:9200,
``SpaceX.API.Device.Device/Handle`` ``get_status``) once per second and POSTs
batches of samples to the OrbitCast API under your anonymous token. This is the
high-value collection path: per-dish, per-second, with obstruction stats and
hardware version — data no public source has.

Single file on purpose: download it, ``pip install grpcio yagrc``, and run —
or use the Docker image (see reporter/README.md). Everything else is stdlib.

Privacy (D12): your location is sent only as an H3 res-5 cell (~250 km²) that you
choose yourself; the script never reads or transmits coordinates or IPs.

Usage:
    python orbitcast_reporter.py --api-url https://api.orbitcast.example \\
        --cell 851f8d37fffffff [--token <token>]

Config may also come from env: ORBITCAST_API_URL, ORBITCAST_CELL, ORBITCAST_TOKEN,
ORBITCAST_DISH_ADDR. Without --token, an anonymous token is minted on first run
and printed once — save it; it is also what you paste into the web app to see
your Dish Doctor verdict.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import time
import urllib.error
import urllib.request
from collections import deque
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any, cast

log = logging.getLogger("orbitcast.reporter")

DEFAULT_DISH_ADDR = "192.168.100.1:9200"
DEFAULT_POLL_SECONDS = 1.0
DEFAULT_BATCH_SIZE = 60  # one POST per minute at 1 Hz polling
DEFAULT_MAX_BUFFER = 3600  # keep at most an hour of backlog through API outages
API_BATCH_LIMIT = 1000  # POST /v1/measurements caps a batch at 1000 rows (§7.3)


class ReporterError(Exception):
    """The OrbitCast API could not be reached or rejected the request."""


def parse_cell(text: str) -> int:
    """Parse an H3 res-5 cell as either a decimal BIGINT or the canonical hex
    index string (e.g. ``851f8d37fffffff``) shown by the web app."""
    cleaned = text.strip().lower()
    try:
        value = int(cleaned)
    except ValueError:
        try:
            value = int(cleaned, 16)
        except ValueError:
            raise ValueError(f"not an H3 cell: {text!r}") from None
    if value <= 0 or value.bit_length() > 64:
        raise ValueError(f"not an H3 cell: {text!r}")
    return value


def status_to_measurement(status: dict, ts: datetime, h3_cell: int) -> dict:
    """Map one flat dish-status dict to the POST /v1/measurements row shape.

    A latency of 0 means "unset" (protobuf scalar default), never a real reading,
    so it maps to unknown; 0 bps throughput is a legitimately idle dish.
    """
    latency = status.get("pop_ping_latency_ms")
    if latency is not None and latency <= 0:
        latency = None
    dl_bps = status.get("downlink_throughput_bps")
    ul_bps = status.get("uplink_throughput_bps")
    fraction = status.get("fraction_obstructed")
    obstruction = None if fraction is None else round(min(max(fraction * 100.0, 0.0), 100.0), 4)
    return {
        "ts": ts.isoformat(),
        "h3_cell": h3_cell,
        "source": "reporter",
        "latency_ms": latency,
        "dl_mbps": None if dl_bps is None else dl_bps / 1e6,
        "ul_mbps": None if ul_bps is None else ul_bps / 1e6,
        "obstruction_pct": obstruction,
        "hw_version": status.get("hardware_version"),
    }


def _post_json(url: str, payload: dict, token: str | None = None) -> dict:
    headers = {"Content-Type": "application/json"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(
        url, data=json.dumps(payload).encode(), headers=headers, method="POST"
    )
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except (urllib.error.URLError, OSError, ValueError) as exc:
        raise ReporterError(f"POST {url} failed: {exc}") from exc


def post_batch(api_url: str, token: str, measurements: list[dict]) -> int:
    """Submit one batch; returns the accepted count."""
    result = _post_json(
        f"{api_url.rstrip('/')}/v1/measurements", {"measurements": measurements}, token
    )
    return int(result["accepted"])


def mint_token(api_url: str, h3_cell: int) -> str:
    """Mint an anonymous token (POST /v1/users). The server stores only its hash;
    it is shown exactly once, so the caller must persist it."""
    result = _post_json(f"{api_url.rstrip('/')}/v1/users", {"h3_cell": h3_cell})
    return str(result["token"])


class Reporter:
    """Poll → buffer → batch-POST loop, with an outage-bounded backlog.

    ``fetch`` returns one flat status dict (see ``status_to_measurement``);
    ``post`` submits a list of measurement rows and returns the accepted count.
    Both are injected so the loop is testable without a dish or a network.
    """

    def __init__(
        self,
        fetch: Callable[[], dict],
        post: Callable[[list[dict]], int],
        h3_cell: int,
        batch_size: int = DEFAULT_BATCH_SIZE,
        max_buffer: int = DEFAULT_MAX_BUFFER,
        clock: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self._fetch = fetch
        self._post = post
        self._h3_cell = h3_cell
        self._batch_size = batch_size
        self._buffer: deque[dict] = deque(maxlen=max_buffer)
        self._clock = clock

    @property
    def pending(self) -> int:
        return len(self._buffer)

    def tick(self) -> int | None:
        """Take one sample and flush if a full batch is buffered. Returns the
        number of rows accepted when a flush happened, else None. A dish that
        won't answer (rebooting, cable pulled) is logged and skipped, not fatal."""
        try:
            status = self._fetch()
        except Exception as exc:
            log.warning("dish status fetch failed: %s", exc)
            status = None
        if status is not None:
            self._buffer.append(status_to_measurement(status, self._clock(), self._h3_cell))
        if len(self._buffer) < self._batch_size:
            return None
        return self.flush()

    def flush(self) -> int | None:
        """POST the whole backlog in API-sized chunks. On failure the unposted
        rows stay buffered for the next attempt; partial progress still counts."""
        accepted = 0
        while self._buffer:
            chunk = [self._buffer[i] for i in range(min(len(self._buffer), API_BATCH_LIMIT))]
            try:
                accepted += self._post(chunk)
            except ReporterError as exc:
                log.warning("batch POST failed, keeping %d samples: %s", self.pending, exc)
                break
            for _ in chunk:
                self._buffer.popleft()
        return accepted or None


def _dish_fetcher(addr: str) -> Callable[[], dict]:
    """Build the real gRPC status fetcher, following the community
    starlink-grpc-tools approach: server reflection via yagrc, so no generated
    proto files are needed. Field names are SpaceX-internal and unversioned
    [VERIFY AT BUILD TIME] — re-check against starlink-grpc-tools if this breaks.

    Imports are local so the rest of the file (and its tests) run without grpcio.
    """
    import grpc  # noqa: PLC0415 — optional heavy dep, reporter-only
    from yagrc import reflector as yagrc_reflector

    channel = grpc.insecure_channel(addr)
    reflector = yagrc_reflector.GrpcReflectionClient()
    reflector.load_protocols(channel, symbols=["SpaceX.API.Device.Device"])
    request_class = reflector.message_class("SpaceX.API.Device.Request")
    stub = reflector.service_stub_class("SpaceX.API.Device.Device")(channel)

    def fetch() -> dict:
        # reflection-built messages have no static type; the field names below are
        # the [VERIFY AT BUILD TIME] surface
        request = cast(Any, request_class())
        request.get_status.SetInParent()
        status = stub.Handle(request, timeout=10).dish_get_status
        return {
            "pop_ping_latency_ms": status.pop_ping_latency_ms,
            "downlink_throughput_bps": status.downlink_throughput_bps,
            "uplink_throughput_bps": status.uplink_throughput_bps,
            "fraction_obstructed": status.obstruction_stats.fraction_obstructed,
            "hardware_version": status.device_info.hardware_version or None,
        }

    return fetch


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description=(__doc__ or "").split("\n\n")[0])
    parser.add_argument("--api-url", default=os.environ.get("ORBITCAST_API_URL"))
    parser.add_argument(
        "--cell",
        default=os.environ.get("ORBITCAST_CELL"),
        help="your H3 res-5 cell (hex or decimal) — shown by the OrbitCast web app",
    )
    parser.add_argument("--token", default=os.environ.get("ORBITCAST_TOKEN"))
    parser.add_argument("--dish", default=os.environ.get("ORBITCAST_DISH_ADDR", DEFAULT_DISH_ADDR))
    parser.add_argument("--interval", type=float, default=DEFAULT_POLL_SECONDS)
    parser.add_argument("--batch-size", type=int, default=DEFAULT_BATCH_SIZE)
    args = parser.parse_args(argv)
    if not args.api_url or not args.cell:
        parser.error("--api-url and --cell are required (or ORBITCAST_API_URL/ORBITCAST_CELL)")

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cell = parse_cell(args.cell)

    token = args.token
    if not token:
        token = mint_token(args.api_url, cell)
        print(
            f"\nMinted a new anonymous token: {token}\n"
            "Save it (set ORBITCAST_TOKEN next run) — it is shown only once, and it is\n"
            "what you paste into the web app's Dish Doctor to see your verdict.\n"
        )

    reporter = Reporter(
        fetch=_dish_fetcher(args.dish),
        post=lambda measurements: post_batch(args.api_url, token, measurements),
        h3_cell=cell,
        batch_size=args.batch_size,
    )
    log.info("reporting dish %s to %s every %.1fs", args.dish, args.api_url, args.interval)
    try:
        while True:
            accepted = reporter.tick()
            if accepted:
                log.info("submitted %d samples", accepted)
            time.sleep(args.interval)
    except KeyboardInterrupt:
        log.info("stopping; flushing %d buffered samples", reporter.pending)
        reporter.flush()


if __name__ == "__main__":
    main()
