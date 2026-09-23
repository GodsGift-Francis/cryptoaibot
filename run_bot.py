"""DEPRECATED (V1.1): use `python -m workers.trading_worker` (and, for TESTNET/LIVE,
`python -m workers.reconciliation_worker`). This shim starts the trading worker."""
import sys
import warnings

if __name__ == "__main__":
    warnings.warn("run_bot.py is deprecated; use `python -m workers.trading_worker`", DeprecationWarning)
    from workers.trading_worker import main
    sys.exit(main())
