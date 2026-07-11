import { useCallback, useEffect, useRef, useState } from "react";

import {
  cellHex,
  fetchDishDoctor,
  fetchForecast,
  fetchMap,
  fetchSkyview,
  geocode,
  mintUser,
  submitMeasurements,
  type DishDoctor as DishDoctorData,
  type Forecast,
  type RegionMap as RegionMapData,
  type Skyview,
} from "./api";
import { secondsToReconfig } from "./countdown";
import { runBrowserProbe } from "./probe";
import DishDoctor from "./DishDoctor";
import ForecastChart from "./ForecastChart";
import Methodology from "./Methodology";
import Privacy from "./Privacy";
import RegionMap from "./RegionMap";
import "./styles.css";

const BASIS_LABEL: Record<string, string> = {
  cell: "based on measurements in your cell",
  region: "based on regional data",
  latitude_prior: "based on a latitude-band prior (no local data yet)",
};

// Static-page routing on the URL hash (no router dependency for two pages, §7.4).
// The dashboard keeps its state while a static page is shown, so navigating back
// doesn't lose a looked-up location.
function useHashRoute(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => {
      setHash(window.location.hash);
      window.scrollTo(0, 0);
    };
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const route = useHashRoute();
  const [query, setQuery] = useState("");
  const [place, setPlace] = useState<string | null>(null);
  const [data, setData] = useState<Skyview | null>(null);
  const [forecast, setForecast] = useState<Forecast | null>(null);
  const [regionMap, setRegionMap] = useState<RegionMapData | null>(null);
  const [dishDoctor, setDishDoctor] = useState<DishDoctorData | null>(null);
  const [forecastPending, setForecastPending] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [remaining, setRemaining] = useState(0);
  const [contributing, setContributing] = useState(false);
  const [contribProgress, setContribProgress] = useState<number | null>(null);
  const [contribMsg, setContribMsg] = useState<string | null>(null);
  const [tokenInput, setTokenInput] = useState("");
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
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
      // Forecast + regional map are best-effort: null means no model promoted
      // yet (503). Fetched in parallel; the map is independent of exact location.
      setForecastPending(true);
      try {
        const [fc, rm] = await Promise.all([
          fetchForecast(p.lat, p.lon),
          fetchMap().catch(() => null),
        ]);
        setForecast(fc);
        setRegionMap(rm);
      } finally {
        setForecastPending(false);
      }
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

  // Dish Doctor only applies once the user is contributing their own readings:
  // the anonymous token is written to localStorage by the browser-probe flow
  // (below) or the dish reporter. Absent a token there is nothing to judge, so
  // the card stays hidden rather than nagging first-time visitors.
  const refreshDishDoctor = useCallback(() => {
    const token = localStorage.getItem("orbitcast_token");
    if (!token) return;
    fetchDishDoctor(token)
      .then(setDishDoctor)
      .catch(() => setDishDoctor(null));
  }, []);

  useEffect(() => {
    refreshDishDoctor();
  }, [refreshDishDoctor]);

  // Zero-install crowdsourcing (§4.3.2): run a ~15 s WebSocket latency probe,
  // mint an anonymous token if we don't have one, and submit the samples tagged
  // with the res-5 cell only (never coordinates, D12). Also unlocks the Dish
  // Doctor card by writing the token it reads.
  async function onContribute() {
    if (!data) return;
    setContributing(true);
    setContribMsg(null);
    setContribProgress(0);
    try {
      const samples = await runBrowserProbe((done, total) =>
        setContribProgress(Math.round((done / total) * 100)),
      );
      let token = localStorage.getItem("orbitcast_token");
      if (!token) {
        token = await mintUser();
        localStorage.setItem("orbitcast_token", token);
      }
      const accepted = await submitMeasurements(token, samples, data.lat, data.lon);
      const sorted = [...samples].sort((a, b) => a.latency_ms - b.latency_ms);
      const median = sorted[Math.floor(sorted.length / 2)].latency_ms;
      setContribMsg(
        `Thanks — ${accepted} readings contributed (median ${median.toFixed(0)} ms round-trip).`,
      );
      refreshDishDoctor();
    } catch (err) {
      setContribMsg(err instanceof Error ? err.message : "Contribution failed");
    } finally {
      setContributing(false);
      setContribProgress(null);
    }
  }

  // Reporter users (§4.3.1) mint their token in the reporter script, not the
  // browser. Linking pastes it in: verify it against the Dish Doctor endpoint
  // (a 401 means a typo'd token), then persist it under the same localStorage
  // key the probe flow writes so both paths unlock the card identically.
  async function onLinkToken(e: React.FormEvent) {
    e.preventDefault();
    const token = tokenInput.trim();
    if (!token) return;
    try {
      const dd = await fetchDishDoctor(token);
      localStorage.setItem("orbitcast_token", token);
      setDishDoctor(dd);
      setTokenInput("");
      setTokenMsg(
        dd
          ? "Token linked — your verdict is below."
          : "Token linked. Your verdict appears once a forecast model is live.",
      );
    } catch (err) {
      setTokenMsg(err instanceof Error ? err.message : "Could not verify the token");
    }
  }

  const staticPage =
    route === "#/methodology" ? <Methodology /> : route === "#/privacy" ? <Privacy /> : null;

  return (
    <main className="app">
      <header>
        <h1>
          <a href="#/" className="home-link">
            OrbitCast
          </a>
        </h1>
        <p className="tagline">
          Starlink satellites overhead and the next link-reconfiguration instant,
          for any location.
        </p>
        <nav className="site-nav">
          <a href="#/">Dashboard</a>
          <a href="#/methodology">Methodology</a>
          <a href="#/privacy">Privacy</a>
        </nav>
      </header>

      {staticPage}

      <div style={{ display: staticPage ? "none" : undefined }}>
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

          <div className="forecast">
            <h2>Next 48 hours</h2>
            {forecast ? (
              <>
                <ForecastChart horizon={forecast.horizon} />
                <p className="basis">
                  {BASIS_LABEL[forecast.basis] ?? forecast.basis}
                  {forecast.model_version ? ` · model ${forecast.model_version}` : ""}
                </p>
              </>
            ) : forecastPending ? (
              <p className="muted">Loading forecast…</p>
            ) : (
              <p className="muted">
                Forecast coming soon — the model trains once enough measurements are collected.
              </p>
            )}
          </div>

          {regionMap && regionMap.cells.length > 0 && (
            <div className="region">
              <h2>Regional forecast</h2>
              <RegionMap data={regionMap} />
              <p className="basis">
                Median download forecast across cells with data, aggregated to H3 res {regionMap.res}.
                Hexes are drawn only where a signal exists — not a global grid.
              </p>
            </div>
          )}

          <div className="contribute">
            <h2>Help improve the forecast</h2>
            <p className="muted">
              Run a ~15-second anonymous latency test from your browser. Only your H3
              cell (~250 km²) is sent — never your address, coordinates, or IP.
            </p>
            <button type="button" onClick={onContribute} disabled={contributing}>
              {contributing
                ? contribProgress != null
                  ? `Measuring… ${contribProgress}%`
                  : "Measuring…"
                : "Contribute anonymous latency readings"}
            </button>
            {contribMsg && <p className="basis">{contribMsg}</p>}

            <div className="reporter-link">
              <h3>Running the dish reporter?</h3>
              <p className="muted">
                Point it at your cell <code>{cellHex(data.lat, data.lon)}</code> and paste
                the token it printed to see your Dish Doctor verdict here.
              </p>
              <form onSubmit={onLinkToken} className="token-form">
                <input
                  value={tokenInput}
                  onChange={(e) => setTokenInput(e.target.value)}
                  placeholder="Reporter token"
                  aria-label="Reporter token"
                />
                <button type="submit" disabled={!tokenInput.trim()}>
                  Link
                </button>
              </form>
              {tokenMsg && <p className="basis">{tokenMsg}</p>}
            </div>
          </div>

          {dishDoctor && (
            <section className="dish-doctor-section">
              <DishDoctor data={dishDoctor} />
            </section>
          )}

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
      </div>
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
