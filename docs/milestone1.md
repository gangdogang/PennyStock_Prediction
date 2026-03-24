# Milestone 1

Milestone 1 is the initial working implementation milestone for the US penny-stock radar.

## What This Delivers

- A Python package scaffold under `src/penny_stock_radar/`
- A working settings layer with `.env` support
- SQLite schema initialization
- A CLI for database setup and universe building
- A Nasdaq Trader symbol seed provider
- A `yfinance`-based metadata enrichment path
- JSON export for sample universe snapshots
- Contract tests for config, database, and universe filtering

## What It Does Not Deliver Yet

- No filing scanner
- No setup scoring engine
- No premarket monitor
- No regular-session decision engine
- No backtest or replay engine
- No dashboard
- No live tick provider integration

## Why This Matters

This milestone proves the project can:

- initialize cleanly
- load configuration predictably
- persist scan results
- produce a real universe snapshot from public sources

It also keeps the data-reality constraint explicit: `yfinance` is useful for discovery and metadata enrichment, but not for premarket tape-level truth.

Milestone 2 now extends that foundation with SEC filing scanning, setup scoring, and watchlist building.

## Milestone 2 Preview

Milestone 2 should continue with:

- hardening the filing scanner
- improving setup scoring
- refining watchlist ranking
- preparing the premarket monitor abstraction
