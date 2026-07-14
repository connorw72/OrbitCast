# OrbitCast

Starlink sky view, latency/throughput forecasts, and per-dish diagnostics for any
location. See [docs/](docs/) and the in-app methodology page for how it works.

## Running locally

**Prerequisites:** Docker (OrbStack on macOS) and Node 22+.

### Backend

```sh
docker compose up
```

- API: http://localhost:8000 (health check at `/healthz`)
- Dagster UI: http://localhost:3000
- Postgres runs internally on the compose network

### Frontend

```sh
cd frontend
npm install
npm run dev
```

Open http://localhost:5173. The app talks to the API at `http://localhost:8000` by
default; override with `VITE_API_BASE` in `frontend/.env` (see `.env.example`).

`npm run build` type-checks and emits a static bundle to `frontend/dist/`.
