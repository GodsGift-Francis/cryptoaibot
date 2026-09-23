"""DEPRECATED (V1.1). Kept only so old imports keep working.

The V1 monolithic cycle lived here. It is replaced by
engine.application.trading_cycle.TradingCycle, run by workers/trading_worker.py.
`run_once()` now executes ONE fully gated V1.1 cycle (control plane, reconciliation,
risk and OrderService all apply). It never bypasses the execution layer.
"""
import warnings

import config as default_cfg


def run_once(cfg=default_cfg, live_market_data=True):
    warnings.warn("paper_trader.run_once is deprecated; use workers.trading_worker", DeprecationWarning, stacklevel=2)
    from engine import bootstrap
    cfg.validate_runtime(cfg)
    s = bootstrap.build(cfg, "legacy-run-once")
    result = s.cycle.run_once()
    s.outbox.flush()
    return result
