# OrbitCast Dagster image (webserver + daemon in one container, CLAUDE.md §5.5,
# §7.1). Shares the python:3.12-slim base with the API. Built from the repo root.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS build
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock ./
COPY core ./core
COPY pipelines ./pipelines
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --package orbitcast-pipelines

FROM python:3.12-slim
WORKDIR /app
COPY --from=build /app /app
ENV PATH="/app/.venv/bin:$PATH" \
    DAGSTER_HOME=/app/dagster_home
RUN mkdir -p /app/dagster_home
EXPOSE 3000
# `dagster dev` runs the webserver and the daemon (schedules) together.
CMD ["dagster", "dev", "-m", "orbitcast_pipelines.defs", "-h", "0.0.0.0", "-p", "3000"]
