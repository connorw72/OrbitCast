import type { DishDoctor as DishDoctorData } from "./api";

const BASIS_LABEL: Record<string, string> = {
  cell: "your cell",
  region: "regional data",
  latitude_prior: "a latitude-band prior (no local data yet)",
};

// Dish Doctor verdict card (CLAUDE.md §6.4). Framing is deliberate (F9): the
// dish's own obstruction fraction is surfaced FIRST as a candidate explanation,
// and the verdict is phrased as evidence against the model's expectation — never
// as an accusation of faulty hardware.
export default function DishDoctor({ data }: { data: DishDoctorData }) {
  const obstruction =
    data.median_obstruction_pct != null ? `${data.median_obstruction_pct.toFixed(1)}%` : "—";

  return (
    <div className="dish-doctor">
      <h2>Dish Doctor</h2>

      {/* Obstruction first, always — before any verdict implying the hardware. */}
      <p className="dd-obstruction">
        Median sky obstruction: <strong>{obstruction}</strong>
        {data.median_obstruction_pct != null && data.median_obstruction_pct >= 1 && (
          <span className="muted">
            {" "}
            — obstructions alone can explain lower throughput; clear the view before
            suspecting the dish.
          </span>
        )}
      </p>

      {data.verdict === "insufficient_data" && (
        <p className="muted">
          Not enough <strong>download</strong> readings yet ({data.n_evaluated} of 20
          needed). The verdict is judged against download throughput, which comes from
          the dish reporter — browser latency readings don't feed it.
        </p>
      )}

      {data.verdict === "healthy" && (
        <p className="dd-verdict dd-ok">
          Your dish is performing in line with the expectation for your conditions
          ({data.below_q10_count} of {data.n_evaluated} readings below the low band).
        </p>
      )}

      {data.verdict === "underperforming" && (
        <p className="dd-verdict dd-warn">
          Evidence of underperformance: your median download is{" "}
          <strong>
            {data.effect_size_pct != null ? `${data.effect_size_pct.toFixed(0)}%` : "—"} below
          </strong>{" "}
          the expectation for your area and conditions.
        </p>
      )}

      {data.verdict !== "insufficient_data" && (
        <dl className="dd-evidence">
          <div>
            <dt>Readings evaluated</dt>
            <dd>{data.n_evaluated}</dd>
          </div>
          <div>
            <dt>Below the low band</dt>
            <dd>
              {data.below_q10_count} across {data.distinct_hours_below} hours of day
            </dd>
          </div>
          <div>
            <dt>Judged against</dt>
            <dd>{BASIS_LABEL[data.basis] ?? data.basis}</dd>
          </div>
        </dl>
      )}
    </div>
  );
}
