from decimal import Decimal as D

import pandas as pd

import backtester
import indicators
import strategy as v1_strategy
from engine.domain.models import SymbolRules
from engine.risk.manager import RiskManager
from engine.strategy.strategy_service import MarketContext, MultiFactorStrategy
from tests.conftest import make_cfg, synthetic_ohlcv


def test_v1_strategy_thresholds_unchanged():
    c = make_cfg()
    assert (c.EMA_FAST, c.EMA_MID, c.EMA_SLOW, c.RSI_PERIOD, c.RSI_OVERSOLD, c.RSI_OVERBOUGHT) == (20, 50, 200, 14, 35, 70)
    assert (c.MACD_FAST, c.MACD_SLOW, c.MACD_SIGNAL) == (12, 26, 9)
    assert (c.SCORE_BUY_THRESHOLD, c.SCORE_SELL_THRESHOLD) == (2.5, -2.5)
    assert (c.RISK_PER_TRADE_PCT, c.STOP_LOSS_PCT, c.DAILY_LOSS_LIMIT_PCT) == (1.0, 3.0, 5.0)


def test_strategy_service_output_identical_to_v1_analyze():
    cfg = make_cfg()
    svc = MultiFactorStrategy(cfg)
    for seed in range(12):
        df = indicators.add_all_indicators(synthetic_ohlcv(seed=seed, drift=0.002 * (seed % 3 - 1)), cfg)
        for cut in (260, 290, 320):
            frame = df.iloc[:cut]
            for md, sent in ((None, None), ({"btc_dominance": 58.0}, {"score": 0.3, "label": "positive", "count": 5})):
                a = v1_strategy.analyze(frame, "BTC/USDT", cfg, market_data=md, sentiment=sent)
                b = svc.evaluate(MarketContext("BTC/USDT", frame, md, sent))
                assert (a.action, a.score, a.reasons) == (b.action, b.score, b.reasons)


def test_backtester_still_runs_unchanged():
    out = backtester.run_backtest(synthetic_ohlcv(n=400, seed=3), "BTC/USDT", make_cfg())
    assert "stats" in out and isinstance(out["trades"], pd.DataFrame)


def test_daily_loss_halt_and_reset(paper):
    rm = RiskManager(paper.store, paper.cfg)
    assert rm.update_daily(D("1000"), "2026-09-22") is False
    assert rm.update_daily(D("949"), "2026-09-22") is True
    assert rm.entry_checks(0) == ["daily loss limit reached"]
    assert rm.update_daily(D("949"), "2026-09-23") is False


def test_sizing_respects_v1_formula_rounding_and_min_notional(paper):
    rm = RiskManager(paper.store, paper.cfg)
    rules = SymbolRules("BTC/USDT", "BTCUSDT", "BTC", "USDT", step_size=D("0.001"), min_notional=D("5"))
    qty, why = rm.size_entry(D("1000"), D("100"), rules, quote_free=D("1000"))
    assert qty == D("3.333") and why is None                 # 1000*1% / 3% / 100, floored to step
    qty, why = rm.size_entry(D("1000"), D("100"), rules, quote_free=D("50"))
    assert qty == D("0.5")                                    # capped by spendable quote
    qty, why = rm.size_entry(D("10"), D("100"), rules, quote_free=D("10"))
    assert qty == 0 and "minimums" in why


def test_max_open_positions_rule(paper):
    assert RiskManager(paper.store, paper.cfg).entry_checks(1) == ["open positions 1 >= MAX_OPEN_POSITIONS 1"]
