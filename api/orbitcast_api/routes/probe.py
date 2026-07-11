"""WS /v1/probe — browser latency probe (CLAUDE.md §7.3, §4.3.2).

A pure round-trip reflector: the browser sends a frame, we bounce it straight back,
and the client times the round trip to derive a latency sample. Latency only —
browsers can't measure bulk throughput honestly (§4.3.2). The samples are submitted
separately via the authenticated POST /v1/measurements, so this endpoint needs no
DB and no auth; it just has to be cheap and low-overhead.
"""

from fastapi import APIRouter, WebSocket, WebSocketDisconnect

router = APIRouter()

# Bound a single socket so it can't be held open indefinitely as a cheap DoS. A
# probe sends ~30 pings (§4.3.2); 100 leaves ample headroom.
_MAX_ECHOES = 100


@router.websocket("/v1/probe")
async def probe(ws: WebSocket) -> None:
    await ws.accept()
    try:
        for _ in range(_MAX_ECHOES):
            await ws.send_text(await ws.receive_text())
    except WebSocketDisconnect:
        return
    await ws.close()
