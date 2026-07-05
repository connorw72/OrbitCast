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

export async function fetchSkyview(lat: number, lon: number): Promise<Skyview> {
  // The server never sees raw coordinates (D12): we resolve to an H3 res-5 cell
  // client-side. Cell ids are 64-bit, beyond JS Number precision, so pass the
  // BIGINT as a decimal string.
  const cellInt = BigInt("0x" + latLngToCell(lat, lon, 5)).toString();
  const resp = await fetch(`${API_BASE}/v1/skyview?cell=${cellInt}`);
  if (!resp.ok) throw new Error("Sky view service unavailable");
  return (await resp.json()) as Skyview;
}
