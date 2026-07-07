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

const API_BASE = import.meta.env.VITE_API_BASE ?? "http://localhost:8000";

// Client-side geocode (Nominatim). Attribution shown in the UI; called only on
// submit, never per keystroke, to respect the usage policy (CLAUDE.md §7.3).
export async function geocode(query: string): Promise<Place> {
  const url = `https://nominatim.openstreetmap.org/search?format=json&limit=1&q=${encodeURIComponent(query)}`;
  const resp = await fetch(url, { headers: { Accept: "application/json" } });
  if (!resp.ok) throw new Error("Geocoding service unavailable");
  const results = (await resp.json()) as Array<{ lat: string; lon: string; display_name: string }>;
  if (results.length === 0) throw new Error("Location not found — try a city or town name");
  const r = results[0];
  return { lat: parseFloat(r.lat), lon: parseFloat(r.lon), label: r.display_name };
}

// The server never sees raw coordinates (D12): resolve to an H3 res-5 cell
// client-side. Cell ids are 64-bit, beyond JS Number precision, so pass the
// BIGINT as a decimal string.
function cellId(lat: number, lon: number): string {
  return BigInt("0x" + latLngToCell(lat, lon, 5)).toString();
}

export async function fetchSkyview(lat: number, lon: number): Promise<Skyview> {
  const resp = await fetch(`${API_BASE}/v1/skyview?cell=${cellId(lat, lon)}`);
  if (!resp.ok) throw new Error("Sky view service unavailable");
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
  if (!resp.ok) throw new Error("Forecast service unavailable");
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
  if (!resp.ok) throw new Error("Map service unavailable");
  return (await resp.json()) as RegionMap;
}
