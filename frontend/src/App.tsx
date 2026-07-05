import { useEffect, useRef, useState } from "react";

import { fetchSkyview, geocode, type Skyview } from "./api";
import { secondsToReconfig } from "./countdown";
import "./styles.css";

export default function App() {
  const [query, setQuery] = useState("");
  const [place, setPlace] = useState<string | null>(null);
  const [data, setData] = useState<Skyview | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const offsetRef = useRef(0); // server_time(ms) - client now(ms)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const p = await geocode(query);
      const sv = await fetchSkyview(p.lat, p.lon);
      offsetRef.current = Date.parse(sv.server_time) - Date.now();
      setPlace(p.label);
      setData(sv);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong");
    } finally {
      setLoading(false);
    }
  }

  // Tick the reconfiguration countdown locally, synced to the server clock.
  useEffect(() => {
    if (!data) return;
    const id = setInterval(() => {
      setRemaining(secondsToReconfig(Date.now() + offsetRef.current));
    }, 100);
    return () => clearInterval(id);
  }, [data]);

  // Refresh satellite counts once a minute without re-geocoding.
  useEffect(() => {
    if (!data) return;
    const id = setInterval(async () => {
      try {
        const sv = await fetchSkyview(data.lat, data.lon);
        offsetRef.current = Date.parse(sv.server_time) - Date.now();
        setData(sv);
      } catch {
        /* keep last good data */
      }
    }, 60_000);
    return () => clearInterval(id);
  }, [data]);

  return (
    <main className="app">
      <header>
        <h1>OrbitCast</h1>
        <p className="tagline">
          Starlink satellites overhead and the next link-reconfiguration instant,
          for any location.
        </p>
      </header>

      <form onSubmit={onSubmit} className="search">
        <input
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          placeholder="Enter a city or town…"
          aria-label="Location"
        />
        <button type="submit" disabled={loading}>
          {loading ? "Locating…" : "Look up"}
        </button>
      </form>

      {error && <p className="error">{error}</p>}

      {data && (
        <section className="result">
          <p className="place">{place}</p>

          <div className="countdown">
            <span className="count">{remaining.toFixed(1)}</span>
            <span className="unit">s</span>
            <p className="count-label">until next link reconfiguration</p>
            <p className="schedule">
              reconfigures at :{data.schedule_seconds.join(", :")} past every UTC minute
            </p>
          </div>

          <div className="stats">
            <Stat label="Satellites overhead" value={String(data.sats_visible)} />
            <Stat
              label="Best elevation"
              value={data.max_elevation_deg != null ? `${data.max_elevation_deg.toFixed(0)}°` : "—"}
            />
            <Stat
              label="Nearest satellite"
              value={data.min_range_km != null ? `${data.min_range_km.toFixed(0)} km` : "—"}
            />
            <Stat
              label="Precipitation"
              value={data.weather ? `${data.weather.precip_mm_h.toFixed(1)} mm/h` : "—"}
            />
            <Stat
              label="Cloud cover"
              value={data.weather ? `${data.weather.cloud_cover_pct.toFixed(0)}%` : "—"}
            />
          </div>

          <footer className="notes">
            <p>
              &ldquo;Satellites overhead&rdquo; counts Starlink satellites above the 25°
              terminal mask — a supply proxy. Which satellite serves you is internal to
              SpaceX and not shown.
            </p>
            {data.gp_fetched_at && (
              <p>Orbital data: CelesTrak, fetched {new Date(data.gp_fetched_at).toUTCString()}.</p>
            )}
            <p className="attribution">
              Geocoding © OpenStreetMap contributors (Nominatim). Weather: Open-Meteo.
            </p>
          </footer>
        </section>
      )}
    </main>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-value">{value}</span>
      <span className="stat-label">{label}</span>
    </div>
  );
}
