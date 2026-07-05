"""OrbitCast forecast models (CLAUDE.md D5, §6).

Six LightGBM quantile boosters ({latency, download_throughput} x q{10,50,90}),
a deterministic schedule overlay for the 15 s microstructure, and a hierarchical
fallback that keeps the product usable everywhere on day one (§6.3). No PyTorch,
no MPS — GBMs are CPU-native and train in minutes at this data scale (D6).
"""
