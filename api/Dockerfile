# OrbitCast API image. Built from the repo root as context so the whole uv
# workspace (core + api) is available for resolution. Multi-arch: arm64 native
# for the dev Mac / EC2 host, amd64 added in CI (CLAUDE.md C1, §7.1).
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
# Manifests + sources needed to resolve the workspace lockfile.
COPY pyproject.toml uv.lock ./
COPY core ./core
COPY ml ./ml
COPY api ./api
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package orbitcast-api

# Runtime: plain python:3.12-slim (same bookworm base as the uv image, so the
# copied .venv interpreter path stays valid).
FROM python:3.12-slim
WORKDIR /app
# libgomp1: OpenMP runtime required by LightGBM (in-process inference, §7.1).
RUN apt-get update \
    && apt-get install -y --no-install-recommends libgomp1 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH"
EXPOSE 8000
CMD ["uvicorn", "orbitcast_api.main:app", "--host", "0.0.0.0", "--port", "8000"]
