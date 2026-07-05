import type { ForecastHour } from "./api";

// Inline SVG 48h forecast chart (no chart dependency — keeps the static bundle
// self-contained). Shows the q10–q90 latency band, the q50 median line, and rain
// shading over hours with precipitation (CLAUDE.md §7.4). Evening-local congestion
// shows up as the recurring nightly hump in the median.

const W = 720;
const H = 220;
const PAD = { top: 16, right: 16, bottom: 28, left: 40 };

interface Props {
  horizon: ForecastHour[];
}

export default function ForecastChart({ horizon }: Props) {
  if (horizon.length === 0) return null;

  const innerW = W - PAD.left - PAD.right;
  const innerH = H - PAD.top - PAD.bottom;

  const lows = horizon.map((h) => h.latency.q10);
  const highs = horizon.map((h) => h.latency.q90);
  const yMin = Math.min(...lows);
  const yMax = Math.max(...highs);
  const ySpan = yMax - yMin || 1;

  const x = (i: number) => PAD.left + (i / (horizon.length - 1)) * innerW;
  const y = (v: number) => PAD.top + innerH - ((v - yMin) / ySpan) * innerH;

  // q10–q90 band polygon: forward along q90, back along q10.
  const bandPath =
    horizon.map((h, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(h.latency.q90)}`).join(" ") +
    " " +
    horizon
      .map((_, i) => `L${x(horizon.length - 1 - i)},${y(horizon[horizon.length - 1 - i].latency.q10)}`)
      .join(" ") +
    " Z";

  const medianPath = horizon
    .map((h, i) => `${i === 0 ? "M" : "L"}${x(i)},${y(h.latency.q50)}`)
    .join(" ");

  // Rain shading: a faint column for each hour with precipitation.
  const colW = innerW / (horizon.length - 1);
  const rain = horizon
    .map((h, i) => ({ i, mm: h.weather.precip_mm_h }))
    .filter((r) => r.mm > 0);

  const yTicks = [yMin, (yMin + yMax) / 2, yMax];

  return (
    <svg viewBox={`0 0 ${W} ${H}`} className="forecast-chart" role="img" aria-label="48-hour latency forecast">
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

      <path d={bandPath} className="band" />
      <path d={medianPath} className="median" fill="none" />

      {[0, 12, 24, 36, horizon.length - 1].map((i) => (
        <text key={`x-${i}`} x={x(i)} y={H - 8} className="axis-label" textAnchor="middle">
          {new Date(horizon[i].hour).getHours()}:00
        </text>
      ))}

      <text x={PAD.left} y={12} className="axis-title">
        latency ms (q10–q90)
      </text>
    </svg>
  );
}
