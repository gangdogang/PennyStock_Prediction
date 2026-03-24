# Milestones 3-6

These milestones move the project from watchlist generation into session simulation and decision support.

## What This Delivers

- `market_data.py`
  - replay CSV loader
  - mock tick generator
- `live_market.py`
  - bounded Polygon/Alpaca REST snapshot, latest trade, and latest quote wrappers
  - null-provider fallback when API keys are missing
- `premarket_monitor.py`
  - PM VWAP
  - PM high/low
  - dollar volume
  - RVOL
  - TPS
  - size CV
  - spread-based quality scoring
- `regular_session_engine.py`
  - anchored VWAP
  - opening-range breakout checks
  - `ENTER / WATCH / AVOID`
- `replay_evaluator.py`
  - continuation / fade / fakeout-style aggregate labeling
  - expectancy
  - profit factor
  - precision@k
- `social_monitor.py`
  - CSV fallback social mention analysis
  - mention velocity
  - unique authors
  - cross-platform sync
- `dashboard.py`
  - Streamlit launch helper
  - optional UI extra

## Recommended Flow

```bash
./scripts/run_full_pipeline.sh
./scripts/psradar analyze-social --mentions-csv sample_outputs/social_mentions.sample.csv
./scripts/psradar show-live-market
./scripts/psradar dashboard
```

## Current Boundary

This is still replay-first and mock-first.

The code is intentionally honest about what it can and cannot do today:

- It can generate a defensible pipeline for research and replay.
- It can score premarket quality and regular-session follow-through.
- It cannot yet claim true live-tape accuracy from `yfinance`.
- It now has live adapter scaffolding plus a bounded CLI lookup path for latest trade/quote/snapshot data.
- It is still not wired into the main scoring pipeline and does not implement websocket or full tape monitoring.
