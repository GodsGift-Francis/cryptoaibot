# V1 Production Foundation changelog

- Added explicit PAPER / TESTNET / LIVE execution modes.
- Added execution abstraction with paper and Binance/CCXT executors.
- Added authenticated Laravel control-plane client from Python.
- Added heartbeat, cycle and trade ingestion endpoints.
- Added Laravel dashboard with pause/resume/emergency stop.
- Added Telegram webhook and allow-list based authorization.
- Added database migrations for bot instances, signals, trades and events.
- Added Laravel scheduler health check.
- Added systemd worker and nginx deployment templates.
- Changed daily risk accounting concept from cash balance to equity.
- Moved stop-loss priority ahead of new strategy entries.
- Kept Streamlit as a transition dashboard.
- Did not invent the optimizer/walk-forward modules described in the original README because those files were absent from the supplied archive.
