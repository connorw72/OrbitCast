// Deterministic Starlink link-reconfiguration schedule (CLAUDE.md §4.1).
// Mirrors orbitcast_core.schedule so the countdown ticks smoothly client-side
// without polling the API every second. Synced to the server clock via an offset.
export const SCHEDULE = [12, 27, 42, 57] as const;

export function secondsToReconfig(utcMs: number): number {
  const d = new Date(utcMs);
  const s = d.getUTCSeconds() + d.getUTCMilliseconds() / 1000;
  for (const b of SCHEDULE) {
    if (b > s) return b - s;
  }
  return 60 + SCHEDULE[0] - s; // wrap 57 -> next minute's 12
}
