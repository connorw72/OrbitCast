import { AlertTriangle, CheckCircle2 } from "lucide-react";

import type { DishDoctor as DishDoctorData } from "./api";

const BASIS_LABEL: Record<string, string> = {
  cell: "measurements from your area",
  region: "measurements from your wider region",
  latitude_prior: "typical behavior at your latitude (no local data yet)",
};

// Dish Doctor verdict card (CLAUDE.md §6.4). Framing is deliberate (F9): the
// dish's own obstruction fraction is surfaced FIRST as a candidate explanation,
// and the verdict is phrased as evidence against the model's expectation — never
// as an accusation of faulty hardware.
export default function DishDoctor({ data }: { data: DishDoctorData }) {
  const obstruction =
    data.median_obstruction_pct != null ? `${data.median_obstruction_pct.toFixed(1)}%` : "—";

  return (
    <div className="dish-doctor panel">
      {/* Obstruction first, always — before any verdict implying the hardware. */}
      <p className="dd-obstruction">
        Your dish&rsquo;s view of the sky is <strong>{obstruction}</strong> blocked
        (median).
        {data.median_obstruction_pct != null && data.median_obstruction_pct >= 1 && (
          <span className="muted">
            {" "}
            Even a little obstruction (a branch, a chimney) can explain slower
            speeds all by itself. Worth clearing the view before blaming the dish.
          </span>
        )}
      </p>

      {data.verdict === "insufficient_data" && (
        <p className="muted">
          Not enough <strong>download</strong> readings to say anything fair yet
          ({data.n_evaluated} of the 20 needed). Download speed only comes from the
          dish reporter; browser tests measure latency, so they don&rsquo;t count
          toward this one.
        </p>
      )}

      {data.verdict === "healthy" && (
        <div className="dd-verdict dd-ok">
          <CheckCircle2 size={18} aria-hidden="true" />
          <p>
            Good news: your dish is performing right in line with what we&rsquo;d
            expect for your area and conditions. ({data.below_q10_count} of{" "}
            {data.n_evaluated} readings ran unusually slow, which is a normal amount.)
          </p>
        </div>
      )}

      {data.verdict === "underperforming" && (
        <div className="dd-verdict dd-warn">
          <AlertTriangle size={18} aria-hidden="true" />
          <p>
            Your dish does look like it&rsquo;s underperforming: your typical download
            speed is{" "}
            <strong>
              {data.effect_size_pct != null ? `${data.effect_size_pct.toFixed(0)}%` : "—"} below
            </strong>{" "}
            what we&rsquo;d expect for your area and conditions. Check the obstruction
            number above first; it&rsquo;s the most common culprit.
          </p>
        </div>
      )}

      {data.verdict !== "insufficient_data" && (
        <dl className="dd-evidence">
          <div>
            <dt>Readings checked</dt>
            <dd>{data.n_evaluated}</dd>
          </div>
          <div>
            <dt>Unusually slow</dt>
            <dd>
              {data.below_q10_count}, spread over {data.distinct_hours_below} hour
              {data.distinct_hours_below === 1 ? "" : "s"} of the day
            </dd>
          </div>
          <div>
            <dt>Compared with</dt>
            <dd>{BASIS_LABEL[data.basis] ?? data.basis}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}
