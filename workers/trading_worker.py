"""Trading worker: evaluates the strategy on schedule and submits orders via OrderService.

It never holds a WebSocket connection. In TESTNET/LIVE it trusts account state
only while the reconciliation worker reports HEALTHY (and fresh).

    python -m workers.trading_worker
"""
from __future__ import annotations

import signal
import sys
import time

import config
from engine import bootstrap
from engine.infrastructure import logging as slog

_running = True


def _stop(*_):
    global _running
    _running = False


def run_cycle(s) -> None:
    result = s.cycle.run_once()
    s.log.info("cycle complete", extra={"action": result.action_taken, "reconciliation": result.reconciliation,
                                        "price": result.price, "signal": getattr(result.signal, "action", None),
                                        "score": getattr(result.signal, "score", None),
                                        "blocked": result.blocked_reasons, "correlation_id": result.correlation_id})


def heartbeat(s) -> None:
    try:
        s.control_plane.heartbeat(bootstrap.health_snapshot(s, "trading"))
    except Exception as exc:  # noqa: BLE001 - heartbeat loss is visible in Laravel as a stale worker
        s.log.warning("heartbeat failed", extra={"error": str(exc)[:200]})


def main() -> int:
    slog.configure(config.LOG_LEVEL)
    config.validate_runtime(config)
    s = bootstrap.build(config, "trading")
    signal.signal(signal.SIGTERM, _stop)
    signal.signal(signal.SIGINT, _stop)
    s.log.info("trading worker started", extra={"symbol": config.SYMBOL, "timeframe": config.TIMEFRAME})
    next_cycle = next_beat = 0.0
    while _running:
        now = time.monotonic()
        if now >= next_beat:
            heartbeat(s)
            next_beat = now + config.HEARTBEAT_INTERVAL_SECONDS
        if now >= next_cycle:
            try:
                run_cycle(s)
            except Exception:  # noqa: BLE001 - one bad cycle must not kill the worker
                s.log.exception("cycle failed")
            next_cycle = now + config.LOOP_INTERVAL_SECONDS
        try:
            s.outbox.flush()
        except Exception:  # noqa: BLE001
            s.log.exception("outbox flush failed")
        time.sleep(1)
    s.log.info("trading worker stopped")
    return 0


if __name__ == "__main__":
    sys.exit(main())
