// Privacy page (CLAUDE.md D12, F8): plain language, no legalese. The claims here
// are load-bearing — every one must stay true of the code. If a feature would
// falsify a sentence on this page, the feature is wrong, not the sentence.
export default function Privacy() {
  return (
    <article className="page">
      <h1>Privacy</h1>
      <p className="muted">
        The short version: no account, no email, no coordinates, no IP addresses
        stored, no ads, no analytics. Here is the long version.
      </p>

      <h2>Your location</h2>
      <p>
        When you type a town, your browser converts it to a hexagonal grid cell about
        250 km² in size (an H3 &ldquo;res-5&rdquo; cell — roughly 20 km across) before
        anything is sent to our server. The server only ever sees that cell. It cannot
        tell your house from any other point in a ~250 km² area, and we store nothing
        finer — there is no finer data to leak, subpoena, or breach.
      </p>
      <p>
        One consequence to know about: the town-name lookup itself is done by
        OpenStreetMap&rsquo;s Nominatim service, directly from your browser, under
        their privacy policy. Our server never sees what you typed.
      </p>

      <h2>No accounts</h2>
      <p>
        If you contribute measurements, you get an anonymous random token. No email, no
        username, no password. We store only a cryptographic hash of the token —
        enough to recognize it, not enough to reconstruct it. There is nothing linking
        a token to a person, and we cannot identify you from it even if asked to.
      </p>

      <h2>What a contributed measurement contains</h2>
      <p>
        Browser probe: a timestamp, your grid cell, and round-trip latency numbers.
        Dish reporter (opt-in, runs on your own network): the same, plus download and
        upload throughput, your dish&rsquo;s obstruction fraction, and its hardware
        revision (e.g. &ldquo;rev4&rdquo;). That is the complete list. The reporter is a
        single readable Python file, so you can audit this claim rather than trust it.
      </p>
      <p>
        Measurements are used for two things: your own Dish Doctor verdict, and —
        aggregated to hourly medians per cell — training the public forecast model.
      </p>

      <h2>IP addresses</h2>
      <p>
        We never write IP addresses to disk. Your IP is used transiently in memory for
        rate limiting (to stop floods) and then forgotten. Logs do not contain it.
      </p>

      <h2>No tracking</h2>
      <p>
        There are no analytics scripts, no cookies, no fingerprinting, and no ads on
        this site. The only thing stored in your browser is your own anonymous token
        (in localStorage, so your Dish Doctor works across visits). Clear it any time;
        nothing breaks except that link.
      </p>

      <h2>Weather lookups</h2>
      <p>
        Weather comes from Open-Meteo, requested by our server for the <em>center of
        your grid cell</em> — never for your coordinates, which we don&rsquo;t have.
      </p>

      <h2>Deleting your data</h2>
      <p>
        Stop the reporter or stop probing, and no new data arrives. Because
        measurements are tied only to an anonymous token, deleting your token from
        localStorage orphans them permanently. If you want the rows themselves gone,
        open an issue with your token and we will delete them — that token is the only
        proof of ownership that exists.
      </p>
    </article>
  );
}
