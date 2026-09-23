"""Builds the executor for the configured TRADING_MODE. The only place that chooses paper vs exchange."""
from engine.execution.base import Executor


def build_executor(cfg, store, position_service, clock) -> Executor:
    if cfg.TRADING_MODE == "PAPER":
        from engine.execution.paper import PaperExecutor
        return PaperExecutor(store, cfg.PAPER_FEE_RATE, lambda: position_service.paper_balances(cfg.SYMBOL), clock)
    if cfg.TRADING_MODE == "LIVE" and not cfg.LIVE_TRADING_CONFIRMED:
        raise RuntimeError("Refusing to build a LIVE executor without LIVE_TRADING_CONFIRMED=true")
    from engine.exchanges.binance.rest_client import CcxtBinanceRestClient
    from engine.execution.binance import BinanceExecutor
    rest = CcxtBinanceRestClient(cfg.API_KEY, cfg.API_SECRET, testnet=cfg.TRADING_MODE == "TESTNET",
                                 timeout_seconds=cfg.REQUEST_TIMEOUT_SECONDS)
    return BinanceExecutor(rest, cfg.BINANCE_ACCOUNT_LABEL, [cfg.SYMBOL])
