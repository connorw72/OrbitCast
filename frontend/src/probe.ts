import { API_BASE, type LatencySample } from "./api";

// Browser latency probe (CLAUDE.md §4.3.2): ~30 round trips over ~15 s against the
// WS /v1/probe echo endpoint. We time each send→echo locally; latency only —
// browsers can't measure bulk throughput honestly.
const SAMPLES = 30;
const GAP_MS = 400; // ~30 samples × 400 ms spacing ≈ 15 s wall time
const TIMEOUT_MS = 5000;

function wsUrl(): string {
  // http→ws, https→wss; only the scheme differs from the REST base.
  return `${API_BASE.replace(/^http/, "ws")}/v1/probe`;
}

export async function runBrowserProbe(
  onProgress?: (done: number, total: number) => void,
): Promise<LatencySample[]> {
  const ws = new WebSocket(wsUrl());
  await new Promise<void>((resolve, reject) => {
    ws.onopen = () => resolve();
    ws.onerror = () =>
      reject(new Error("We couldn't open a test connection. Check your network and try again."));
  });

  const samples: LatencySample[] = [];
  try {
    for (let i = 0; i < SAMPLES; i++) {
      const token = `p${i}`; // unique per ping so a stale echo can't be mistimed
      const start = performance.now();
      const rtt = await new Promise<number>((resolve, reject) => {
        const timer = setTimeout(
          () => reject(new Error("The test timed out mid-run. Worth trying again.")),
          TIMEOUT_MS,
        );
        ws.onmessage = (ev) => {
          if (ev.data !== token) return; // ignore anything but this ping's echo
          clearTimeout(timer);
          resolve(performance.now() - start);
        };
        ws.onerror = () => {
          clearTimeout(timer);
          reject(new Error("The test connection dropped mid-run. Worth trying again."));
        };
        ws.send(token);
      });
      samples.push({ ts: new Date().toISOString(), latency_ms: rtt });
      onProgress?.(i + 1, SAMPLES);
      if (i < SAMPLES - 1) await new Promise((r) => setTimeout(r, GAP_MS));
    }
  } finally {
    ws.close();
  }
  return samples;
}
