import { useCallback, useEffect, useRef, useState } from "react";
import { AlertCircle, CheckCircle2, Copy, LocateFixed } from "lucide-react";

import {
  cellHex,
  fetchDishDoctor,
  fetchForecast,
  fetchMap,
  fetchSkyview,
  geocode,
  mintUser,
  reverseGeocode,
  submitMeasurements,
  type DishDoctor as DishDoctorData,
  type Forecast,
  type ForecastHour,
  type Place,
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
  cell: "based on real measurements from your area",
  region: "your area is quiet, so this leans on measurements from the wider region",
  latitude_prior:
    "no measurements near you yet; this is how Starlink typically behaves at your latitude",
};

// Nominatim labels are exhaustive ("Austin, Travis County, Texas, United
// States"); trim the middle for a label a person would actually say.
function shortPlace(label: string): string {
  const parts = label.split(",").map((p) => p.trim());
  if (parts.length <= 3) return label;
  return `${parts[0]}, ${parts[parts.length - 2]}, ${parts[parts.length - 1]}`;
}

// "2 hours ago" reads better than a UTC timestamp in the footer.
function timeAgo(iso: string): string {
  const mins = Math.max(0, Math.round((Date.now() - Date.parse(iso)) / 60_000));
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins} minute${mins === 1 ? "" : "s"} ago`;
  const hours = Math.round(mins / 60);
  if (hours < 48) return `${hours} hour${hours === 1 ? "" : "s"} ago`;
  const days = Math.round(hours / 24);
  return `${days} days ago`;
}

// Model versions are timestamps ("v20260708T010837Z"); readers just want to
// know the model is fresh, so show "updated Jul 8" and keep the raw id out of
// the UI (it stays in the API payload for anyone debugging).
function modelFreshness(version: string): string {
  const m = /^v(\d{4})(\d{2})(\d{2})T/.exec(version);
  if (!m) return `model ${version}`;
  const date = new Date(Date.UTC(+m[1], +m[2] - 1, +m[3]));
  return `model updated ${date.toLocaleDateString(undefined, { month: "short", day: "numeric" })}`;
}

const LAST_PLACE_KEY = "orbitcast_last_place";

function loadLastPlace(): Place | null {
  try {
    const raw = localStorage.getItem(LAST_PLACE_KEY);
    if (!raw) return null;
    const p = JSON.parse(raw) as Place;
    return typeof p.lat === "number" && typeof p.lon === "number" && typeof p.label === "string"
      ? p
      : null;
  } catch {
    return null;
  }
}

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
  const [contribOk, setContribOk] = useState(false);
  const [tokenInput, setTokenInput] = useState("");
  const [tokenMsg, setTokenMsg] = useState<string | null>(null);
  const [lastPlace, setLastPlace] = useState<Place | null>(loadLastPlace);
  const [reshuffled, setReshuffled] = useState(false);
  const offsetRef = useRef(0); // server_time(ms) - client now(ms)
  const prevRemainingRef = useRef(Infinity);
  const searchRef = useRef<HTMLInputElement>(null);

  // Shared by all three entry points: the search box, the "use my location"
  // button, and the "pick up where you left off" chip.
  const lookup = useCallback(async (p: Place) => {
    setLoading(true);
    setError(null);
    try {
      const sv = await fetchSkyview(p.lat, p.lon);
      offsetRef.current = Date.parse(sv.server_time) - Date.now();
      setPlace(shortPlace(p.label));
      setData(sv);
      // Remember the place (label + the coordinates it resolved to) so the next
      // visit is one click. Stays in the browser, like everything else.
      try {
        localStorage.setItem(LAST_PLACE_KEY, JSON.stringify(p));
        setLastPlace(p);
      } catch {
        /* storage full/blocked — remembering is a nicety, not a requirement */
      }
      // The sky view is up — free the search button now rather than holding it
      // through the (possibly slow, cold-start) forecast fetch below.
      setLoading(false);
      // Forecast + regional map are best-effort: null means no model promoted
      // yet (503). Fetched in parallel but rendered independently — the map can
      // be much slower than the forecast, and the chart shouldn't wait for it.
      setForecastPending(true);
      const mapPromise = fetchMap().catch(() => null);
      try {
        setForecast(await fetchForecast(p.lat, p.lon));
      } finally {
        setForecastPending(false);
      }
      setRegionMap(await mapPromise);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
    } finally {
      setLoading(false);
    }
  }, []);

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault();
    if (!query.trim()) return;
    setLoading(true);
    setError(null);
    try {
      const p = await geocode(query);
      await lookup(p);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Something went wrong. Please try again.");
      setLoading(false);
    }
  }

  // Browser geolocation → sky view, no typing. Coordinates stay client-side
  // exactly like typed locations (D12): the server only ever sees the grid
  // cell; Nominatim only supplies a display label.
  function onUseMyLocation() {
    if (!("geolocation" in navigator)) {
      setError("Your browser doesn't offer location access. Just type a town instead.");
      return;
    }
    setLoading(true);
    setError(null);
    navigator.geolocation.getCurrentPosition(
      async (pos) => {
        const { latitude, longitude } = pos.coords;
        let label = "Your location";
        try {
          label = await reverseGeocode(latitude, longitude);
        } catch {
          /* a label is cosmetic — proceed without one */
        }
        await lookup({ lat: latitude, lon: longitude, label });
      },
      () => {
        setLoading(false);
        setError("We couldn't get your location. No worries, just type a town instead.");
      },
      { maximumAge: 300_000, timeout: 15_000 },
    );
  }

  // Tick the reconfiguration countdown locally, synced to the server clock.
  // When the count wraps (jumps back up toward 15) the reconfig instant just
  // passed: stamp the panel for a beat so the event is felt, not just implied.
  useEffect(() => {
    if (!data) return;
    const id = setInterval(() => {
      const r = secondsToReconfig(Date.now() + offsetRef.current);
      if (r > prevRemainingRef.current + 5) {
        setReshuffled(true);
        setTimeout(() => setReshuffled(false), 1600);
      }
      prevRemainingRef.current = r;
      setRemaining(r);
    }, 100);
    return () => clearInterval(id);
  }, [data]);

  // "/" jumps to the search box from anywhere (unless already typing).
  useEffect(() => {
    function onKey(e: KeyboardEvent) {
      if (e.key !== "/" || e.metaKey || e.ctrlKey || e.altKey) return;
      const t = e.target as HTMLElement | null;
      if (t && (t.tagName === "INPUT" || t.tagName === "TEXTAREA")) return;
      e.preventDefault();
      searchRef.current?.focus();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, []);

  // Tab title follows the looked-up town so pinned tabs stay tellable-apart.
  useEffect(() => {
    document.title = place
      ? `OrbitCast — ${place.split(",")[0].trim()}`
      : "OrbitCast — know your Starlink sky";
  }, [place]);

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
      setContribOk(true);
      setContribMsg(
        `Thank you! ${accepted} readings added. Your typical round trip was ${median.toFixed(0)} ms.`,
      );
      refreshDishDoctor();
    } catch (err) {
      setContribOk(false);
      setContribMsg(
        err instanceof Error ? err.message : "That didn't go through. Please try again.",
      );
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
          ? "You're linked. Your dish check-up is just below."
          : "You're linked! Your check-up will appear once the forecast model is live.",
      );
    } catch (err) {
      setTokenMsg(
        err instanceof Error ? err.message : "We couldn't verify that token. Please try again.",
      );
    }
  }

  const staticPage =
    route === "#/methodology" ? <Methodology /> : route === "#/privacy" ? <Privacy /> : null;


  return (
    <main className="app">
      <header>
        <div className="status-strip">
          <h1>
            <a href="#/" className="wordmark">
              OrbitCast
            </a>
          </h1>
          <UtcClock />
        </div>
        <p className="tagline">
          See the Starlink satellites above you right now, and a live countdown to
          the next moment your connection is most likely to blip.
        </p>
        <nav className="site-nav">
          <a href="#/" aria-current={staticPage ? undefined : "page"}>
            Dashboard
          </a>
          <a href="#/methodology" aria-current={route === "#/methodology" ? "page" : undefined}>
            Methodology
          </a>
          <a href="#/privacy" aria-current={route === "#/privacy" ? "page" : undefined}>
            Privacy
          </a>
        </nav>
      </header>

      {staticPage}

      <div style={{ display: staticPage ? "none" : undefined }}>
      <div className={data ? undefined : "search-hero"}>
        <form onSubmit={onSubmit} className="search">
          <input
            ref={searchRef}
            value={query}
            onChange={(e) => setQuery(e.target.value)}
            placeholder="Type your town… “Bozeman, Montana” works"
            aria-label="Location"
          />
          <button type="submit" disabled={loading}>
            {loading ? "Finding you…" : "Show my sky"}
          </button>
        </form>
        <div className="search-extras">
          <button type="button" className="text-btn" onClick={onUseMyLocation} disabled={loading}>
            <LocateFixed size={14} aria-hidden="true" />
            use my current location
          </button>
          {!data && lastPlace && (
            <button
              type="button"
              className="text-btn"
              onClick={() => lookup(lastPlace)}
              disabled={loading}
            >
              back to {shortPlace(lastPlace.label)}
            </button>
          )}
        </div>
        {!data && (
          <p className="search-hint">
            Free, and no account needed. Your exact location never leaves your browser;
            our server only ever sees a rough ~250 km² area of the map.
          </p>
        )}
      </div>

      {error && (
        <p className="error">
          <AlertCircle size={18} aria-hidden="true" />
          {error}
        </p>
      )}

      {data && (
        <section className="result">
          <p className="place">
            <span className="loc-label">loc /</span>
            {place}
          </p>

          <div className={`countdown panel${remaining >= 14.5 ? " just-reconfigured" : ""}`}>
            <span className="live-badge" role="img" aria-label="live">
              <span className="live-dot" />
            </span>
            {reshuffled && (
              <span className="reshuffle-stamp" aria-hidden="true">
                links reshuffled
              </span>
            )}
            {/* Digits tick 10×/s — hidden from screen readers; the static
                sentence below carries the same information without the firehose. */}
            <div aria-hidden="true">
              <span className="count">{remaining.toFixed(1)}</span>
              <span className="unit">s</span>
            </div>
            <p className="sr-only">
              Starlink reshuffles satellite links every 15 seconds; a live countdown
              to the next reshuffle is shown.
            </p>
            <p className="count-label">until Starlink reshuffles its satellite links</p>
            <p className="schedule">
              happens like clockwork at :{data.schedule_seconds.join(", :")} past every
              minute (UTC); brief lag spikes tend to land on these moments
            </p>
            <div className="cycle-track" aria-hidden="true">
              <div
                className="cycle-fill"
                style={{ transform: `scaleX(${Math.min(remaining / 15, 1)})` }}
              />
            </div>
          </div>

          <h2 className="sec-head">Your sky right now</h2>
          <div className="stats panel">
            <Stat label="Satellites overhead" value={String(data.sats_visible)} />
            <Stat
              label="Highest satellite"
              value={data.max_elevation_deg != null ? `${data.max_elevation_deg.toFixed(0)}°` : "—"}
            />
            <Stat
              label="Closest satellite"
              value={data.min_range_km != null ? `${data.min_range_km.toFixed(0)} km` : "—"}
            />
            <Stat
              label="Rain right now"
              value={data.weather ? `${data.weather.precip_mm_h.toFixed(1)} mm/h` : "—"}
            />
            <Stat
              label="Cloud cover"
              value={data.weather ? `${data.weather.cloud_cover_pct.toFixed(0)}%` : "—"}
            />
          </div>

          {data.max_elevation_deg != null && <SkyGauge elevationDeg={data.max_elevation_deg} />}

          <div className="forecast">
            <h2 className="sec-head">Your next 48 hours</h2>
            {forecast ? (
              <>
                <ForecastSummary horizon={forecast.horizon} />
                <ForecastChart horizon={forecast.horizon} />
                <p className="basis">
                  {BASIS_LABEL[forecast.basis] ?? forecast.basis}
                  {forecast.model_version ? ` (${modelFreshness(forecast.model_version)})` : ""}
                </p>
              </>
            ) : forecastPending ? (
              <>
                <div className="skeleton chart-skeleton" aria-hidden="true" />
                <p className="muted skeleton-note">
                  Crunching your forecast… the first load can take a little while.
                </p>
              </>
            ) : (
              <p className="muted">
                No forecast for this spot just yet. The model is still gathering enough
                measurements to say something honest, so check back soon.
              </p>
            )}
          </div>

          {regionMap && regionMap.cells.length > 0 && (
            <div className="region">
              <h2 className="sec-head">How your region is doing</h2>
              <RegionMap data={regionMap} marker={{ lat: data.lat, lon: data.lon }} />
              <p className="basis">
                Expected download speeds wherever we have data. Blank space means no
                measurements there yet; we'd rather show you a gap than invent one.
              </p>
            </div>
          )}

          <div className="contribute">
            <h2 className="sec-head">Help make the forecast better</h2>
            <div className="contribute-card panel">
            <p className="muted">
              Got 15 seconds? Run a quick, anonymous latency test right from this page.
              It sharpens the forecast for everyone near you, and all we receive is your
              rough ~250 km² area. Never your address, coordinates, or IP.
            </p>
            <button type="button" onClick={onContribute} disabled={contributing}>
              {contributing
                ? contribProgress != null
                  ? `Measuring… ${contribProgress}%`
                  : "Measuring…"
                : "Run the 15-second test"}
            </button>
            {contributing && contribProgress != null && (
              <div
                className="probe-track"
                role="progressbar"
                aria-valuenow={contribProgress}
                aria-valuemin={0}
                aria-valuemax={100}
                aria-label="Latency probe progress"
              >
                <div className="probe-fill" style={{ width: `${contribProgress}%` }} />
              </div>
            )}
            {contribMsg &&
              (contribOk ? (
                <p className="contrib-ok">
                  <CheckCircle2 size={16} aria-hidden="true" />
                  {contribMsg}
                </p>
              ) : (
                <p className="basis">{contribMsg}</p>
              ))}

            <div className="reporter-link">
              <h3>Running the dish reporter?</h3>
              <p className="muted">
                Point it at your grid cell{" "}
                <code>{cellHex(data.lat, data.lon)}</code>
                <CopyCell value={cellHex(data.lat, data.lon)} /> then paste the token it
                printed below, and your Dish Doctor check-up will show up right here.
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
          </div>

          {dishDoctor && (
            <section className="dish-doctor-section">
              <h2 className="sec-head">Dish Doctor</h2>
              <DishDoctor data={dishDoctor} />
            </section>
          )}

          <footer className="notes">
            <p>
              &ldquo;Satellites overhead&rdquo; counts every Starlink satellite more than
              25° above your horizon (the ones your dish could plausibly talk to). Which
              one is actually serving you is SpaceX&rsquo;s secret, so we don&rsquo;t
              pretend to know it.
            </p>
            {data.gp_fetched_at && (
              <p>Satellite positions from CelesTrak, last updated {timeAgo(data.gp_fetched_at)}.</p>
            )}
            <p className="attribution">
              Place names © OpenStreetMap contributors (Nominatim). Weather by Open-Meteo.
            </p>
          </footer>
        </section>
      )}
      </div>
    </main>
  );
}

// Sky-dome protractor: your horizon-to-horizon sky in cross-section, the 0–25°
// zones a Starlink dish can't use shaded out, and the best satellite plotted at
// its true elevation. Makes the "highest satellite" number legible at a glance
// and explains the 25° terminal mask without a paragraph (F3: it's still a
// supply proxy — the copy stays "best", never "your").
function SkyGauge({ elevationDeg }: { elevationDeg: number }) {
  const W = 360;
  const H = 122;
  const cx = 180;
  const cy = 102;
  const R = 78;
  const MASK = 25; // Starlink terminal elevation mask (§4.1)

  const pt = (deg: number, r = R): [number, number] => [
    cx + r * Math.cos((deg * Math.PI) / 180),
    cy - r * Math.sin((deg * Math.PI) / 180),
  ];
  const e = Math.max(0, Math.min(90, elevationDeg));
  const [ex, ey] = pt(e);
  const [labelX, labelY] = pt(e, R + 17);
  const [m1x, m1y] = pt(MASK);
  const [m2x, m2y] = pt(180 - MASK);

  return (
    <div className="sky-gauge panel">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        role="img"
        aria-label={`The best satellite is ${e.toFixed(0)} degrees above the horizon; the dish can only use satellites above ${MASK} degrees.`}
      >
        {/* usable sky: faint fill between the two mask boundaries */}
        <path
          d={`M ${cx},${cy} L ${m1x},${m1y} A ${R} ${R} 0 0 0 ${m2x},${m2y} Z`}
          className="gauge-usable"
        />
        {/* the 0–25° zones the dish can't use, both horizons */}
        <path
          d={`M ${cx},${cy} L ${cx + R},${cy} A ${R} ${R} 0 0 0 ${m1x},${m1y} Z`}
          className="gauge-mask"
        />
        <path
          d={`M ${cx},${cy} L ${cx - R},${cy} A ${R} ${R} 0 0 1 ${m2x},${m2y} Z`}
          className="gauge-mask"
        />
        <line x1={cx} y1={cy} x2={m1x} y2={m1y} className="gauge-mask-edge" />
        <line x1={cx} y1={cy} x2={m2x} y2={m2y} className="gauge-mask-edge" />

        {/* horizon + dome */}
        <line x1={8} y1={cy} x2={W - 8} y2={cy} className="gauge-horizon" />
        <path
          d={`M ${cx - R},${cy} A ${R} ${R} 0 0 1 ${cx + R},${cy}`}
          className="gauge-dome"
          fill="none"
        />
        {[30, 60, 90, 120, 150].map((a) => {
          const [t1x, t1y] = pt(a, R);
          const [t2x, t2y] = pt(a, R + 5);
          return <line key={a} x1={t1x} y1={t1y} x2={t2x} y2={t2y} className="gauge-tick" />;
        })}

        {/* best satellite: ray + dot + elevation readout */}
        <line x1={cx} y1={cy} x2={ex} y2={ey} className="gauge-ray" />
        <circle cx={ex} cy={ey} r={4} className="gauge-sat" />
        <text x={labelX} y={labelY + 3} textAnchor="middle" className="gauge-readout">
          {e.toFixed(0)}°
        </text>

        <text x={10} y={cy + 14} className="gauge-label">
          horizon
        </text>
        <text x={W - 10} y={cy + 14} textAnchor="end" className="gauge-label">
          horizon
        </text>
        {/* skip the zenith label when the readout would sit on top of it */}
        {e < 68 && (
          <text x={cx} y={14} textAnchor="middle" className="gauge-label">
            straight up
          </text>
        )}
      </svg>
      <p className="gauge-caption">
        The best satellite right now, drawn on your sky. The shaded zones near the
        horizon are below {MASK}°, too low for a dish to use.
      </p>
    </div>
  );
}

// One plain-English sentence on top of the chart: the calmest stretch in the
// next 48 h (lowest expected median latency over a 3-hour window) and a heads-up
// if rain is coming. Derived entirely from the forecast payload — it phrases the
// model's own numbers, it doesn't add claims.
function ForecastSummary({ horizon }: { horizon: ForecastHour[] }) {
  const series = horizon.filter((h) => h.latency != null);
  if (series.length < 6) return null;

  const fmtDayHour = (d: Date) =>
    d.toLocaleString(undefined, { weekday: "short", hour: "numeric" });
  const fmtHour = (d: Date) => d.toLocaleString(undefined, { hour: "numeric" });

  let bestStart = 0;
  let bestScore = Infinity;
  for (let i = 0; i + 3 <= series.length; i++) {
    const score =
      series[i].latency!.q50 + series[i + 1].latency!.q50 + series[i + 2].latency!.q50;
    if (score < bestScore) {
      bestScore = score;
      bestStart = i;
    }
  }
  const windowStart = new Date(series[bestStart].hour);
  const windowEnd = new Date(Date.parse(series[bestStart + 2].hour) + 3_600_000);

  const firstRain = series.find((h) => h.weather.precip_mm_h > 0);

  return (
    <p className="forecast-summary">
      Calmest stretch ahead: <strong>{fmtDayHour(windowStart)}–{fmtHour(windowEnd)}</strong>{" "}
      your time, if you have something latency-sensitive to plan.
      {firstRain
        ? ` Rain expected around ${fmtDayHour(new Date(firstRain.hour))}, so things may get a little choppy.`
        : " No rain in sight for the next two days."}
    </p>
  );
}

// Tiny clipboard affordance for the grid-cell id the dish reporter needs.
function CopyCell({ value }: { value: string }) {
  const [copied, setCopied] = useState(false);
  async function onCopy() {
    try {
      await navigator.clipboard.writeText(value);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      /* clipboard blocked — the id is selectable text right next to the button */
    }
  }
  return (
    <button type="button" className="copy-btn" onClick={onCopy} aria-label="Copy grid cell id">
      <Copy size={12} aria-hidden="true" />
      {copied ? "copied!" : "copy"}
    </button>
  );
}

function Stat({ label, value }: { label: string; value: string }) {
  return (
    <div className="stat">
      <span className="stat-label">{label}</span>
      <span className="stat-value">{value}</span>
    </div>
  );
}

// Live UTC clock in the status strip — the whole product is about a UTC
// schedule, so the reference clock earns its header slot.
function UtcClock() {
  const [now, setNow] = useState(() => new Date());
  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000);
    return () => clearInterval(id);
  }, []);
  const hh = String(now.getUTCHours()).padStart(2, "0");
  const mm = String(now.getUTCMinutes()).padStart(2, "0");
  const ss = String(now.getUTCSeconds()).padStart(2, "0");
  return (
    <span className="utc-clock">
      <span className="utc-label">utc</span>
      {hh}:{mm}:{ss}
    </span>
  );
}
