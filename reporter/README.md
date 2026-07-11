# OrbitCast dish reporter

Run this on the network your Starlink dish is on and it will poll the dish's own
status endpoint (`192.168.100.1:9200`, the same local gRPC API the Starlink app
uses) once per second and contribute anonymous performance samples — latency,
throughput, obstruction fraction, hardware revision — to OrbitCast. That feed is
what powers your personal [Dish Doctor](../docs/) verdict and sharpens the
forecast for your area. Privacy: your location is sent only as an ~250 km² H3
cell that you pick yourself; the script never reads or transmits coordinates,
IP addresses, or anything identifying (see the privacy page). It is one
auditable Python file with two dependencies.

## Install

**Docker (recommended):**

```sh
docker run -d --restart unless-stopped \
  -e ORBITCAST_API_URL=https://api.orbitcast.example \
  -e ORBITCAST_CELL=<your H3 cell, shown in the web app> \
  -e ORBITCAST_TOKEN=<your token> \
  ghcr.io/connorw72/orbitcast-reporter:latest
```

**Plain Python (3.12+):**

```sh
pip install grpcio yagrc
python orbitcast_reporter.py --api-url https://api.orbitcast.example --cell <cell>
```

Omit the token on first run and one is minted and printed once — save it, and
paste it into the web app's Dish Doctor panel to see your verdict. All flags:
`--dish` (default `192.168.100.1:9200`), `--interval` (default 1 s),
`--batch-size` (default 60 → one upload per minute).
