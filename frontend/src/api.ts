import { latLngToCell } from "h3-js";

export interface Weather {
  precip_mm_h: number;
  cloud_cover_pct: number;
  snow_mm_h: number;
}

export interface Skyview {
  lat: number;
  lon: number;
  sats_visible: number;
  max_elevation_deg: number | null;
  min_range_km: number | null;
  seconds_to_reconfig: number;
  next_reconfig: string;
  schedule_seconds: number[];
  server_time: string;
  gp_fetched_at: string | null;
  weather: Weather | null;
}

export interface Place {
  lat: number;
  lon: number;
  label: string;
}

export const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// Client-side geocode (Nominatim). Attribution shown in the UI; called only on
// submit, never per keystroke, to respect the usage policy (CLAUDE.md §7.3).
export async function geocode(query: string): Promise<Place> {
  const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resp.ok)
    throw new Error("The place lookup service isn't answering right now. Try again in a minute.");
  const results = (await resp.json()) as Array<{ lat: string; lon: string; display_name: string }>;
  if (results.length === 0)
    throw new Error("We couldn't find that place. Try the name of a nearby city or town.");
  const r = results[0];
  return { lat: parseFloat(r.lat), lon: parseFloat(r.lon), label: r.display_name };
}

// Reverse geocode for the "use my location" flow — a display label only, at city
// zoom. Same Nominatim policy as geocode(): one request per explicit user action.
export async function reverseGeocode(lat: number, lon: number): Promise<string> {
  const url = `https://nominatim.openstreetmap.org/reverse?format=json&zoom=10&lat=${lat}&lon=${lon}`;
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resp.ok) throw new Error("reverse geocode unavailable");
  const result = (await resp.json()) as { display_name?: string };
  if (!result.display_name) throw new Error("no reverse geocode result");
  return result.display_name;
}

// The server never sees raw coordinates (D12): resolve to an H3 res-5 cell
// client-side. Cell ids are 64-bit, beyond JS Number precision, so pass the
// BIGINT as a decimal string.
function cellId(lat: number, lon: number): string {
  return BigInt("0x" + latLngToCell(lat, lon, 5)).toString();
}

// The canonical hex H3 index for display — what the dish reporter's --cell flag
// takes (reporter/README.md).
export function cellHex(lat: number, lon: number): string {
  return latLngToCell(lat, lon, 5);
}

export async function fetchSkyview(lat: number, lon: number): Promise<Skyview> {
  const resp = await fetch(`${API_BASE}/v1/skyview?cell=${cellId(lat, lon)}`);
  if (!resp.ok)
    throw new Error("We couldn't reach the sky-view service. Give it a moment and try again.");
  return (await resp.json()) as Skyview;
}

export interface Band {
  q10: number;
  q50: number;
  q90: number;
}

export interface ForecastHour {
  hour: string;
  basis: string;
  // Null until the target has labels (throughput waits on M-Lab).
  latency: Band | null;
  dl: Band | null;
  weather: { precip_mm_h: number };
}

export interface Forecast {
  cell: number;
  lat: number;
  lon: number;
  generated_at: string;
  model_version: string | null;
  basis: string;
  horizon: ForecastHour[];
}

// Returns null when no model has been promoted yet (503) so the UI can show a
// "forecast coming soon" state rather than an error.
export async function fetchForecast(lat: number, lon: number): Promise<Forecast | null> {
  const resp = await fetch(`${API_BASE}/v1/forecast?cell=${cellId(lat, lon)}`);
  if (resp.status === 503) return null;
  if (!resp.ok) throw new Error("We couldn't load the forecast right now.");
  return (await resp.json()) as Forecast;
}

export interface MapCell {
  cell: string; // 64-bit H3 id as a decimal string (beyond JS Number precision)
  value: number;
  basis: string;
  n: number;
}

export interface RegionMap {
  res: number;
  metric: string;
  generated_at: string;
  model_version: string | null;
  cells: MapCell[];
}

// Regional hex aggregates for the map (CLAUDE.md §7.4). Returns null on 503 (no
// promoted model yet) so the UI degrades to a "coming soon" state like the chart.
export async function fetchMap(res = 4, metric = "dl_q50"): Promise<RegionMap | null> {
  const resp = await fetch(`${API_BASE}/v1/map?res=${res}&metric=${encodeURIComponent(metric)}`);
  if (resp.status === 503) return null;
  if (!resp.ok) throw new Error("We couldn't load the regional map right now.");
  return (await resp.json()) as RegionMap;
}

export interface DishDoctor {
  verdict: "insufficient_data" | "healthy" | "underperforming";
  n_evaluated: number;
  below_q10_count: number;
  distinct_hours_below: number;
  p_value: number | null;
  effect_size_pct: number | null;
  median_obstruction_pct: number | null;
  basis: string;
}

export interface LatencySample {
  ts: string; // ISO-8601 UTC, captured when the round trip completed
  latency_ms: number;
}

// Mint an anonymous token (CLAUDE.md §7.3, D12). Minted with no cell — locations
// travel per-measurement as res-5 cells — which also sidesteps sending a 64-bit
// H3 id as a JSON number (beyond JS Number precision).
export async function mintUser(): Promise<string> {
  const resp = await fetch(`${API_BASE}/v1/users`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({}),
  });
  if (!resp.ok)
    throw new Error("We couldn't set up your anonymous token. Please try again in a moment.");
  return ((await resp.json()) as { token: string }).token;
}

// Submit browser-probe latency samples (CLAUDE.md §4.3.2). The server never sees
// coordinates: the location is resolved to an H3 res-5 cell here and sent as a
// decimal string (D12), the same contract as the map/skyview paths. Returns how
// many rows the server accepted.
export async function submitMeasurements(
  token: string,
  samples: LatencySample[],
  lat: number,
  lon: number,
): Promise<number> {
  const cell = cellId(lat, lon);
  const measurements = samples.map((s) => ({
    ts: s.ts,
    h3_cell: cell,
    source: "browser" as const,
    latency_ms: s.latency_ms,
  }));
  const resp = await fetch(`${API_BASE}/v1/measurements`, {
    method: "POST",
    headers: { "Content-Type": "application/json", Authorization: `Bearer ${token}` },
    body: JSON.stringify({ measurements }),
  });
  if (!resp.ok)
    throw new Error("Your readings didn't make it through. Please try again in a moment.");
  return ((await resp.json()) as { accepted: number }).accepted;
}

// Per-user Dish Doctor verdict (CLAUDE.md §6.4). Authenticated with the user's
// anonymous token. Returns null on 503 (no model promoted yet) so the UI can show
// a "coming soon" state rather than an error.
export async function fetchDishDoctor(token: string): Promise<DishDoctor | null> {
  const resp = await fetch(`${API_BASE}/v1/dish-doctor`, {
    headers: { Authorization: `Bearer ${token}` },
  });
  if (resp.status === 503) return null;
  if (resp.status === 401)
    throw new Error("That token doesn't look right. Double-check it and try again.");
  if (!resp.ok) throw new Error("Dish Doctor isn't answering right now. Try again shortly.");
  return (await resp.json()) as DishDoctor;
}
