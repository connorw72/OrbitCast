import { useRef, useState } from "react";

import type { Band, ForecastHour } from "./api";

// Inline SVG 48h forecast chart (no chart dependency — keeps the static bundle
// self-contained). A latency/download toggle picks the plotted band; both come
// from the same forecast payload. Shows the q10–q90 band ("likely range"), the
// q50 median ("typical"), and rain shading over hours with precipitation
// (CLAUDE.md §7.4). Evening-local congestion shows up as the recurring nightly
// hump in the median. A pointer crosshair reveals exact values per hour; the
// legend keeps the encodings readable without relying on color alone.

const W = 720;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 40 };

type Metric = "latency" | "dl";

const METRIC_META: Record<Metric, { toggle: string; axis: string; unit: string }> = {
  latency: { toggle: "Latency", axis: "expected latency (ms)", unit: "ms" },
  dl: { toggle: "Download", axis: "expected download (Mbps)", unit: "Mbps" },
};

type ChartHour = ForecastHour & { band: Band };

interface Props {
  horizon: ForecastHour[];
}

export default function ForecastChart({ horizon }: Props) {
  const svgRef = useRef<SVGSVGElement>(null);
  const [hoverIdx, setHoverIdx] = useState<number | null>(null);

  // Either target may be null until it has labels (throughput waits on M-Lab);
  // offer the toggle only when both exist, and chart whichever we have.
  const hasLatency = horizon.some((h) => h.latency != null);
  const hasDl = horizon.some((h) => h.dl != null);
  const [chosen, setMetric] = useState<Metric>(hasLatency ? "latency" : "dl");
  // A later lookup may lack the chosen metric (state persists across horizons);
  // fall back to whichever band exists rather than rendering nothing.
  const metric: Metric =
    horizon.some((h) => h[chosen] != null) ? chosen : hasLatency ? "latency" : "dl";

  const series = horizon
    .map((h) => ({ ...h, band: h[metric] }))
    .filter((h): h is ChartHour => h.band != null);
  if (series.length < 2) return null;

  const meta = METRIC_META[metric];
  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const lows = series.map((h) => h.band.q10);
  const highs = series.map((h) => h.band.q90);
  const yMin = Math.min(...lows);
  const yMax = Math.max(...highs);
  const ySpan = yMax - yMin || 1;

  const x = (i: number) => PAD.left + (i / (series.length - 1)) * innerW;
  const y = (v: number) => PAD.top + innerH - ((v - yMin) / ySpan) * innerH;

  // q10–q90 band polygon: forward along q90, back along q10.
  const bandPath =
    series.map((h, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(h.band.q90)}`).join(" ") +
    " " +
    series
      .map((_, i) => `L${x(series.length - 1 - i)},${y(series[series.length - 1 - i].band.q10)}`)
      .join(" ") +
    " Z";

  const medianPath = series
    .map((h, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(h.band.q50)}`)
    .join(" ");

  // Rain shading: a faint column for each hour with precipitation.
  const colW = innerW / (series.length - 1);
  const rain = series
    .map((h, i) => ({ i, mm: h.weather.precip_mm_h }))
    .filter((r) => r.mm > 0);

  // Night tint (local 21:00–06:00) plus a hairline + weekday label at each local
  // midnight — orients "the nightly hump" and makes 48 hours scannable by day.
  const localHours = series.map((h) => new Date(h.hour).getHours());
  const night = localHours
    .map((hr, i) => ({ i, hr }))
    .filter(({ hr }) => hr >= 21 || hr < 6);
  const midnights = localHours
    .map((hr, i) => ({ i, hr }))
    .filter(({ hr, i }) => hr === 0 && i > 0)
    .map(({ i }) => ({
      i,
      day: new Date(series[i].hour).toLocaleDateString(undefined, { weekday: "short" }),
    }));

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  // Map a pointer position to the nearest series index in viewBox space.
  function onPointerMove(e: React.PointerEvent<SVGSVGElement>) {
    const svg = svgRef.current;
    if (!svg) return;
    const rect = svg.getBoundingClientRect();
    const vx = ((e.clientX - rect.left) / rect.width) * W;
    const i = Math.round(((vx - PAD.left) / innerW) * (series.length - 1));
    setHoverIdx(Math.max(0, Math.min(series.length - 1, i)));
  }

  const hover = hoverIdx != null ? series[hoverIdx] : null;

  // Tooltip geometry: flip to the left of the crosshair near the right edge.
  const TIP_W = 128;
  const TIP_H = 40;
  const tipX =
    hoverIdx != null
      ? x(hoverIdx) + TIP_W + 12 > W - PAD.right
        ? x(hoverIdx) - TIP_W - 12
        : x(hoverIdx) + 12
      : 0;

  return (
    <div className="forecast-chart-card panel">
      {hasLatency && hasDl && (
        <div className="metric-toggle" role="group" aria-label="Forecast metric">
          {(Object.keys(METRIC_META) as Metric[]).map((m) => (
            <button
              key={m}
              type="button"
              aria-pressed={metric === m}
              onClick={() => {
                setMetric(m);
                setHoverIdx(null);
              }}
            >
              {METRIC_META[m].toggle}
            </button>
          ))}
        </div>
      )}

      <svg
        ref={svgRef}
        viewBox={`0 0 ${W} ${H}`}
        className="forecast-chart"
        role="img"
        aria-label={`48-hour ${metric === "latency" ? "latency" : "download speed"} forecast`}
        onPointerMove={onPointerMove}
        onPointerLeave={() => setHoverIdx(null)}
      >
        {night.map((n) => (
          <rect
            key={`night-${n.i}`}
            x={x(n.i) - colW / 2}
            y={PAD.top}
            width={colW}
            height={innerH}
            className="night-band"
          />
        ))}

        {rain.map((r) => (
          <rect
            key={`rain-${r.i}`}
            x={x(r.i) - colW / 2}
            y={PAD.top}
            width={colW}
            height={innerH}
            className="rain-band"
          />
        ))}

        {yTicks.map((v) => (
          <g key={`y-${v}`}>
            <line x1={PAD.left} x2={W - PAD.right} y1={y(v)} y2={y(v)} className="grid" />
            <text x={PAD.left - 6} y={y(v) + 4} className="axis-label" textAnchor="end">
              {v.toFixed(0)}
            </text>
          </g>
        ))}

        {midnights.map((m) => (
          <g key={`mid-${m.i}`}>
            <line
              x1={x(m.i)}
              x2={x(m.i)}
              y1={PAD.top}
              y2={PAD.top + innerH}
              className="day-line"
            />
            <text x={x(m.i) + 5} y={PAD.top + 11} className="day-label">
              {m.day}
            </text>
          </g>
        ))}

        <path d={bandPath} className="band" />
        <path d={medianPath} className="median" fill="none" />

        {[0, 12, 24, 36, series.length - 1].map((i) => (
          <text key={`x-${i}`} x={x(i)} y={H - 8} className="axis-label" textAnchor="middle">
            {new Date(series[i].hour).getHours()}:00
          </text>
        ))}

        <text x={PAD.left} y={12} className="axis-title">
          {meta.axis}
        </text>

        {hover && hoverIdx != null && (
          <g pointerEvents="none">
            <line
              x1={x(hoverIdx)}
              x2={x(hoverIdx)}
              y1={PAD.top}
              y2={PAD.top + innerH}
              className="crosshair"
            />
            <circle cx={x(hoverIdx)} cy={y(hover.band.q50)} r={4} className="hover-dot" />
            <rect
              x={tipX}
              y={PAD.top + 4}
              width={TIP_W}
              height={TIP_H}
              rx={2}
              className="tooltip-box"
            />
            <text x={tipX + 10} y={PAD.top + 20} className="tooltip-text">
              {new Date(hover.hour).getHours()}:00 · {hover.band.q50.toFixed(0)} {meta.unit}
            </text>
            <text x={tipX + 10} y={PAD.top + 36} className="tooltip-text muted-line">
              likely {hover.band.q10.toFixed(0)}–{hover.band.q90.toFixed(0)}
              {hover.weather.precip_mm_h > 0 ? " · rain" : ""}
            </text>
          </g>
        )}
      </svg>

      <div className="chart-legend">
        <span className="key">
          <span className="swatch band-key" aria-hidden="true" /> likely range (80% of the time)
        </span>
        <span className="key">
          <span className="swatch median-key" aria-hidden="true" /> typical
        </span>
        {night.length > 0 && (
          <span className="key">
            <span className="swatch night-key" aria-hidden="true" /> night
          </span>
        )}
        {rain.length > 0 && (
          <span className="key">
            <span className="swatch rain-key" aria-hidden="true" /> rain expected
          </span>
        )}
      </div>
    </div>
  );
}
