# OrbitCast dish reporter image (CLAUDE.md §4.3.1, C1). Deliberately standalone —
# the reporter is a single stdlib-only file plus its two gRPC deps, so users can
# audit exactly what runs on their LAN. Context: this directory. Multi-arch
# (linux/arm64 + linux/amd64) is handled by buildx in CI.
FROM python:3.12-slim
RUN pip install --no-cache-dir "grpcio>=1.60,<2" "yagrc>=1.1,<2"
WORKDIR /app
COPY orbitcast_reporter.py .
ENTRYPOINT ["python", "orbitcast_reporter.py"]
