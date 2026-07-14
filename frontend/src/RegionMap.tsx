import { cellToBoundary } from "h3-js";

import type { MapCell, RegionMap as RegionMapData } from "./api";

// Inline SVG hex map (no map/deck dependency — keeps the static bundle
// self-contained, matching ForecastChart). Renders the res-4 cells the API
// returns as H3 hexagons on an equirectangular projection, coloured by the
// forecast metric, with cells resting on a latitude-band prior drawn faintly and
// dashed so the map never implies measured data where there is none (CLAUDE.md
// §6.3, §7.4, F7). We plot only cells we have signal for — never a dense global
// grid (§5.3).

const W = 720;
const MAX_H = 420;

interface Props {
  data: RegionMapData;
  // The looked-up location, drawn as a reticle so "your region" is literal.
  // Coordinates stay client-side (D12); this never leaves the browser.
  marker?: { lat: number; lon: number };
}

interface Projected {
  cell: MapCell;
  points: [number, number][]; // boundary as [lat, lng]
}

const BASIS_LABEL: Record<string, string> = {
  cell: "measured right here",
  region: "based on nearby data",
  latitude_prior: "rough estimate (no local data yet)",
};

// Friendly names for the metrics the API serves; fall back to the raw key.
const METRIC_LABEL: Record<string, string> = {
  dl_q50: "expected download (Mbps)",
  latency_q50: "expected latency (ms)",
};

// Two-stop sequential ramp (low → high). The legend shows the numeric range so
// the direction is explicit regardless of metric (higher throughput is better,
// lower latency is better).
function ramp(t: number): string {
  const lo = [58, 42, 0]; // dark amber
  const hi = [255, 176, 0]; // amber phosphor (--amber)
  const c = lo.map((l, i) => Math.round(l + (hi[i] - l) * t));
  return `rgb(${c[0]}, ${c[1]}, ${c[2]})`;
}

export default function RegionMap({ data, marker }: Props) {
  if (data.cells.length === 0) return null;

  // Decimal-string id → H3 hex (ids exceed JS Number precision) → boundary.
  const projected: Projected[] = data.cells.map((cell) => ({
    cell,
    points: cellToBoundary(BigInt(cell.cell).toString(16)) as [number, number][],
  }));

  const lats = projected.flatMap((p) => p.points.map(([lat]) => lat));
  const lngs = projected.flatMap((p) => p.points.map(([, lng]) => lng));
  const minLat = Math.min(...lats);
  const maxLat = Math.max(...lats);
  const minLng = Math.min(...lngs);
  const maxLng = Math.max(...lngs);
  const lngSpan = maxLng - minLng || 1;
  const latSpan = maxLat - minLat || 1;

  const PAD = 12;
  const innerW = W - PAD * 2;
  const innerH = Math.min(MAX_H, (innerW * latSpan) / lngSpan) - PAD * 2;
  const H = innerH + PAD * 2;

  const px = (lng: number) => PAD + ((lng - minLng) / lngSpan) * innerW;
  const py = (lat: number) => PAD + innerH - ((lat - minLat) / latSpan) * innerH;

  const values = data.cells.map((c) => c.value);
  const vMin = Math.min(...values);
  const vMax = Math.max(...values);
  const vSpan = vMax - vMin || 1;

  // Only draw the reticle when the location falls inside the mapped area — the
  // map covers cells with data, which may not include the viewer at all.
  const showMarker =
    marker != null &&
    marker.lat >= minLat &&
    marker.lat <= maxLat &&
    marker.lon >= minLng &&
    marker.lon <= maxLng;
  const markerX = showMarker ? px(marker.lon) : 0;
  const markerY = showMarker ? py(marker.lat) : 0;
  // Flip the label to the left edge when the reticle sits near the right one.
  const labelLeft = showMarker && markerX > W - 60;

  return (
    <div className="region-map">
      <svg viewBox={`0 0 ${W} ${H}`} role="img" aria-label="Regional forecast hex map">
        {projected.map(({ cell, points }) => {
          const d = points.map(([lat, lng], i) => `${i === 0 ? "M" : "L"}${px(lng)},${py(lat)}`).join(" ") + " Z";
          const t = (cell.value - vMin) / vSpan;
          const prior = cell.basis === "latitude_prior";
          return (
            <path
              key={cell.cell}
              d={d}
              fill={ramp(t)}
              fillOpacity={prior ? 0.35 : 0.85}
              stroke="rgba(255,255,255,0.35)"
              strokeWidth={cell.basis === "cell" ? 1.5 : 0.75}
              strokeDasharray={prior ? "3 2" : undefined}
            >
              <title>
                {`${cell.value.toFixed(1)} · ${BASIS_LABEL[cell.basis] ?? cell.basis} · ${cell.n} area${cell.n === 1 ? "" : "s"} with data`}
              </title>
            </path>
          );
        })}

        {showMarker && (
          <g className="map-marker" pointerEvents="none">
            <line x1={markerX - 14} x2={markerX - 5} y1={markerY} y2={markerY} />
            <line x1={markerX + 5} x2={markerX + 14} y1={markerY} y2={markerY} />
            <line x1={markerX} x2={markerX} y1={markerY - 14} y2={markerY - 5} />
            <line x1={markerX} x2={markerX} y1={markerY + 5} y2={markerY + 14} />
            <rect x={markerX - 4} y={markerY - 4} width={8} height={8} fill="none" />
            <text
              x={labelLeft ? markerX - 18 : markerX + 18}
              y={markerY + 3.5}
              textAnchor={labelLeft ? "end" : "start"}
            >
              you
            </text>
          </g>
        )}
      </svg>

      <div className="map-legend">
        <span className="legend-label">{METRIC_LABEL[data.metric] ?? data.metric}</span>
        <span className="legend-min">{vMin.toFixed(0)}</span>
        <span className="legend-ramp" aria-hidden="true" />
        <span className="legend-max">{vMax.toFixed(0)}</span>
        <span className="legend-note">dashed hexes are rough estimates (no local data yet)</span>
      </div>
    </div>
  );
}
