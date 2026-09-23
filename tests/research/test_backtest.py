"""Sprint 1 exit criteria: the backtester is correct, causal, and trades like the live engine."""
import math
from datetime import timedelta

import numpy as np
import pandas as pd
import pytest

import strategy as v1_strategy
import indicators
from research import backtest as bt
from research import strategies as st
from tests.conftest import Harness, synthetic_ohlcv


def flat(n=48, px=100.0, start="2026-01-01"):
    ts = pd.date_range(start, periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": px, "high": px, "low": px, "close": px, "volume": 1.0})


def test_round_trip_costs_exactly_fees_and_slippage():
    df = flat()
    sig = np.zeros(len(df)); sig[2], sig[10] = 1, -1
    r = bt.run(df, sig, bt.BTConfig(sizing="all_in", stop_pct=None, fee_rate=0.001, slippage_bps=10))
    t = r.trades.iloc[0]
    assert t.entry_time == df.timestamp[3] and t.exit_time == df.timestamp[11]      # next-bar-open fills
    assert t.entry_price == pytest.approx(100.1) and t.exit_price == pytest.approx(99.9)
    notional = 1000 / 1.001
    expected = notional / 100.1 * 99.9 * 0.999
    assert r.metrics["final_equity"] == pytest.approx(expected)


def test_stop_fills_at_gap_open_not_at_stop():
    df = flat(10)
    df.loc[5, ["open", "high", "low", "close"]] = [90.0, 91.0, 89.0, 90.0]          # gaps through a 3% stop
    sig = np.zeros(10); sig[1] = 1
    r = bt.run(df, sig, bt.BTConfig(fee_rate=0, slippage_bps=0, stop_pct=3.0))
    t = r.trades.iloc[0]
    assert t.reason == "stop" and t.exit_price == pytest.approx(90.0)                # not 97


def test_stop_can_trigger_on_entry_bar():
    df = flat(10)
    df.loc[2, "low"] = 95.0
    sig = np.zeros(10); sig[1] = 1
    r = bt.run(df, sig, bt.BTConfig(fee_rate=0, slippage_bps=0, stop_pct=3.0))
    assert r.trades.iloc[0].reason == "stop" and r.trades.iloc[0].exit_time == df.timestamp[2]
    assert r.trades.iloc[0].exit_price == pytest.approx(97.0)


def test_v1_sizing_matches_v1_formula():
    df = flat(10)
    sig = np.zeros(10); sig[1] = 1
    r = bt.run(df, sig, bt.BTConfig(fee_rate=0, slippage_bps=0, risk_pct=1, stop_pct=3))
    assert r.trades.iloc[0].qty * 100 == pytest.approx(1000 * 0.01 / 0.03)           # 333.33 notional


def test_daily_halt_blocks_entries_then_resets_next_day():
    df = flat(72, start="2026-01-01")
    df.loc[3:5, ["open", "high", "low", "close"]] = 90.0                              # -10% while all-in
    df.loc[6:, ["open", "high", "low", "close"]] = 90.0
    sig = np.zeros(72); sig[1] = 1; sig[4] = -1; sig[10] = 1; sig[30] = 1
    r = bt.run(df, sig, bt.BTConfig(sizing="all_in", stop_pct=None, fee_rate=0, slippage_bps=0, daily_loss_pct=5))
    entries = list(r.trades.entry_time)
    assert df.timestamp[11] not in entries                                            # same day: halted
    assert df.timestamp[31] in entries                                                # next day: allowed


def test_sharpe_is_annualised_with_bars_per_year():
    eq = pd.Series([100, 101, 100.5, 102, 101.5], index=pd.date_range("2026", periods=5, freq="1h", tz="UTC"))
    m = bt.metrics(eq, pd.DataFrame(columns=["pnl", "return", "bars"]), bt.BTConfig())
    r = np.diff(eq.values) / eq.values[:-1]
    assert m["sharpe"] == pytest.approx(r.mean() / r.std(ddof=1) * math.sqrt(8760))


def test_v1_scores_are_causal_and_equal_live_analyze():
    df = synthetic_ohlcv(n=420, seed=11)
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    full = st.v1_scores(df, cache=False)
    part = st.v1_scores(df.iloc[:360].reset_index(drop=True), cache=False)
    np.testing.assert_array_equal(full[:360], part)                                   # appending future bars changes nothing
    cfg = st.cfg_copy()
    i = 350
    frame = indicators.add_all_indicators(df.iloc[i - 298:i + 1], cfg)          # 299 closed candles, as live
    assert full[i] == v1_strategy.analyze(frame, "BTC/USDT", cfg).score


def regime_market(n, seed, amp=0.003, period=180, vol=0.006):
    """Alternating up/down trends with noise; continuous (open = previous close)."""
    rng = np.random.default_rng(seed)
    drift = amp * np.sin(np.arange(n) * 2 * np.pi / period)
    closes = 30000 * np.exp(np.cumsum(drift + rng.normal(0, vol, n)))
    opens = np.concatenate([[closes[0]], closes[:-1]])
    highs = np.maximum(opens, closes) * (1 + rng.uniform(0, 0.004, n))
    lows = np.minimum(opens, closes) * (1 - rng.uniform(0, 0.004, n))
    ts = pd.date_range(end="2026-09-22T12:00:00", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": opens, "high": highs, "low": lows, "close": closes, "volume": 1.0})


def engine_trades(tmp_path, df):
    """Replay the candles through the REAL engine (PAPER) one hourly cycle at a time."""
    from engine.market_data.fetcher import MarketDataService
    from engine.strategy.strategy_service import MultiFactorStrategy
    h = Harness(tmp_path, "PAPER", PAPER_FEE_RATE=0)
    h.cfg.STARTING_PAPER_BALANCE = 1000
    state = {"now": None}
    scores = {}

    def fetch(symbol, tf, limit):
        now = state["now"]
        closed = df[df.timestamp + timedelta(hours=1) <= now].tail(limit - 1)
        forming = df[df.timestamp <= now].tail(1).copy()
        o = forming["open"].iloc[0]
        forming[["high", "low", "close"]] = o                                        # no intrabar lookahead
        out = pd.concat([closed, forming]).reset_index(drop=True)
        out["timestamp"] = out["timestamp"].dt.tz_localize(None)
        return out

    h.s.cycle.market = MarketDataService(h.cfg, h.clock, fetch_ohlcv=fetch, fetch_global=lambda: None, fetch_news=lambda: [])
    h.s.cycle.strategy = MultiFactorStrategy(h.cfg)
    for i in range(300, len(df)):
        for minute in (1, 16):          # production loops every 15 min: a stop exit and a re-entry can share a candle
            state["now"] = df.timestamp[i].to_pydatetime() + timedelta(minutes=minute)
            h.clock.set(state["now"])
            res = h.s.cycle.run_once()
            if minute == 1 and res.signal is not None:
                scores[i - 1] = res.signal.score                             # engine evaluated closed candle i-1
    orders = [o for o in h.store.orders("test-bot") if o.status.value == "FILLED"]
    return [(o.side, float(o.average_fill_price), float(o.executed_quantity)) for o in sorted(orders, key=lambda o: o.id)], scores


def test_backtest_trades_match_the_live_engine(tmp_path):
    df = regime_market(900, seed=3)                                                  # 5 stop exits + 1 signal exit
    research_scores = st.v1_scores(df, cache=False)
    sig = st.thresholds(research_scores, 2.5, -2.5)
    sig[:299] = 0
    r = bt.run(df, sig, bt.BTConfig(fee_rate=0, slippage_bps=0, stop_pct=3.0, risk_pct=1.0, daily_loss_pct=5.0))
    back = []
    for t in r.trades.itertuples():
        if t.reason == "end":
            continue
        back += [("BUY", t.entry_price, t.qty), ("SELL", t.exit_price, t.qty)]
    live, live_scores = engine_trades(tmp_path, df)
    assert len(live_scores) > 500
    mismatched = {i: (s, research_scores[i]) for i, s in live_scores.items() if s != research_scores[i]}
    assert not mismatched, f"engine and research scores differ at {len(mismatched)} bars: {list(mismatched.items())[:3]}"
    if len(r.trades) and r.trades.iloc[-1].reason == "end":
        live = live[:-1]                                                             # engine still holds it
    assert len(back) >= 6, f"scenario too quiet: {len(back)} fills"
    assert [s for s, _, _ in live] == [s for s, _, _ in back]
    for (ls, lp, lq), (bs, bp, bq) in zip(live, back):
        assert lp == pytest.approx(bp, abs=0.011), (ls, lp, bp)                      # engine rounds to 0.01 tick
        assert lq == pytest.approx(bq, rel=1e-4)
