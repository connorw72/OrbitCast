// Methodology page (CLAUDE.md §7.4, F8): every data source, every limitation,
// and the basis-labeling scheme, stated plainly. This page is the trust argument
// for the whole product — keep it honest before making it impressive.
export default function Methodology() {
  return (
    <article className="page">
      <h1>How OrbitCast works</h1>
      <p className="muted">
        Everything below is the whole story — including what this tool cannot know.
        If you find an error, please open an issue.
      </p>

      <h2>The three things OrbitCast shows</h2>

      <h3>1. Sky view — deterministic, no ML</h3>
      <p>
        Satellite counts and elevations are computed from published orbital elements:
        CelesTrak&rsquo;s <em>supplemental</em> GP set for Starlink, which is derived from
        SpaceX&rsquo;s own ephemerides and is more accurate than the general catalog. We
        refresh it at most every two hours and propagate positions with the SGP4 model
        (via skyfield). &ldquo;Satellites overhead&rdquo; counts satellites above a 25°
        elevation mask — the mask Starlink terminals actually use.
      </p>
      <p>
        The reconfiguration countdown is not a prediction. Multiple independent
        measurement studies have shown that Starlink reallocates terminal–satellite
        links on a fixed global schedule: 12, 27, 42, and 57 seconds past every UTC
        minute. Latency spikes cluster at those instants. The countdown is pure clock
        arithmetic against that published schedule.
      </p>

      <h3>2. Forecast — quantile ML with uncertainty</h3>
      <p>
        Latency and download-throughput forecasts come from LightGBM gradient-boosted
        trees trained with quantile objectives (10th, 50th, and 90th percentiles), so
        the band you see is the model&rsquo;s own uncertainty, not decoration. Features
        are deliberately few: time of day and week, satellite supply overhead (count,
        best elevation), latitude, precipitation (current and forecast), a terrestrial
        broadband context signal, and the recent measurement history of your area.
      </p>
      <p>
        The 15-second spike microstructure is <em>not</em> learned by the model — it is a
        known schedule (above), overlaid deterministically. Training an ML model to
        rediscover a documented clock would be theater.
      </p>
      <p>
        Models are retrained weekly and promoted only if they pass an evaluation gate
        on held-out future data: the quantile band must achieve 78–82% empirical
        coverage, and the median forecast must beat a persistence baseline (&ldquo;same
        place, same hour last week&rdquo;). If a model can&rsquo;t beat persistence, it
        doesn&rsquo;t ship.
      </p>

      <h3>3. Dish Doctor — a statistical test, not a black box</h3>
      <p>
        If you contribute measurements, Dish Doctor compares them against the
        model&rsquo;s 10th-percentile expectation for your area and time. A healthy
        connection falls below that line about 10% of the time by definition; yours is
        flagged only when a one-sided binomial test rejects that at p &lt; 0.01,
        sustained across at least three different hours of the day — so one bad evening
        can&rsquo;t flag you. Your dish&rsquo;s own obstruction fraction is always shown
        first, because a tree line is a far more common explanation than faulty
        hardware. The verdict is evidence, not an accusation.
      </p>

      <h2>Data sources</h2>
      <ul>
        <li>
          <strong>CelesTrak supplemental GP data</strong> — Starlink orbital elements,
          fetched at most once per two hours.
        </li>
        <li>
          <strong>M-Lab NDT</strong> — public speed tests filtered to Starlink&rsquo;s
          network (AS14593): download/upload rates and minimum RTT.
        </li>
        <li>
          <strong>RIPE Atlas</strong> — continuous ping measurements from the ~100
          public Atlas probes hosted on Starlink connections worldwide.
        </li>
        <li>
          <strong>WetLinks dataset</strong> — six months of co-located Starlink and
          weather-station measurements at two European sites (TMA 2024), used offline
          to calibrate the rain-degradation response.
        </li>
        <li>
          <strong>Ookla Open Data</strong> — used <em>only</em> as a terrestrial-broadband
          context feature. It has no per-ISP breakdown and is never presented as
          Starlink performance (see limitations).
        </li>
        <li>
          <strong>Open-Meteo</strong> — precipitation and cloud forecasts, plus ERA5
          reanalysis for historical training joins.
        </li>
        <li>
          <strong>Crowdsourced measurements</strong> — the browser latency probe and the
          opt-in dish reporter. These carry the highest weight in training because they
          are the only per-dish ground truth. See the privacy page for exactly what they
          contain.
        </li>
      </ul>

      <h2>The &ldquo;basis&rdquo; label</h2>
      <p>
        Every forecast tells you what it stands on. <strong>Your cell</strong>: enough
        measurements exist in your ~20 km-scale area. <strong>Regional data</strong>: your
        area is sparse, so the forecast borrows from the surrounding region.
        <strong> Latitude-band prior</strong>: no nearby measurements at all — you are
        seeing the average behavior of the constellation at your latitude, nothing more.
        The map draws hexes only where a signal exists; blank space means &ldquo;no
        data,&rdquo; and we would rather show you that than invent smoothness.
      </p>

      <h2>Limitations — read these</h2>
      <ul>
        <li>
          <strong>We cannot know which satellite serves you.</strong> Assignment is
          internal to SpaceX. Satellite counts and elevations are supply proxies —
          that is why the copy says &ldquo;satellites overhead,&rdquo; never &ldquo;your
          satellite.&rdquo;
        </li>
        <li>
          <strong>M-Lab locations are fuzzy for Starlink users.</strong> Starlink uses
          carrier-grade NAT, so speed-test IPs often geolocate to the gateway city, not
          the user. We therefore aggregate M-Lab data at a coarse regional level and
          never pretend it has street-level precision.
        </li>
        <li>
          <strong>Ookla data is not Starlink data.</strong> It mixes every fixed-line ISP
          in a tile. It informs the model about the terrestrial alternatives in an area
          — nothing else.
        </li>
        <li>
          <strong>The 15-second schedule is SpaceX&rsquo;s to change.</strong> It is
          documented by independent measurements, not by SpaceX. We continuously
          re-check spike alignment against contributed data and will degrade the
          countdown honestly if the schedule drifts.
        </li>
        <li>
          <strong>Orbital data can go stale.</strong> If CelesTrak is unreachable we keep
          serving from the last fetch; positions drift slowly, so counts stay useful
          for days, but we do not hide the fetch timestamp — it is in the page footer.
        </li>
        <li>
          <strong>The rain-fade curve is calibrated on two European sites</strong> over
          one winter season. Its transfer to, say, tropical convective rain is
          unverified until crowdsourced data covers heavy-rain events elsewhere.
        </li>
        <li>
          <strong>Sparse regions get honest fallbacks, not fake precision.</strong> The
          basis label above exists precisely so a forecast in rural Mongolia doesn&rsquo;t
          masquerade as a measured one.
        </li>
        <li>
          <strong>Dish Doctor can be wrong.</strong> An obstructed dish will flag as
          underperforming — that is why obstruction is surfaced first. Rain windows are
          currently scored against dry-weather expectations, a known false-positive
          source we are working to remove.
        </li>
        <li>
          <strong>This is a solo, free project on a single small server.</strong> Nightly
          backups exist and the stack rebuilds in minutes, but it has no
          high-availability pretensions.
        </li>
      </ul>

      <p className="muted">
        Source code, evaluation reports, and this page&rsquo;s history are public in the
        project repository.
      </p>
    </article>
  );
}
