import numpy as np
import pandas as pd
import pytest

from research import backtest as bt
from research import evaluate as ev
from research import overlay
from research import strategies as st
from research import sweep


def market(seed=1, n=26304, legs=((9000, 0.00009), (8000, -0.0001), (9304, 0.00006))):
    rng = np.random.default_rng(seed)
    trend = np.concatenate([np.full(k, d) for k, d in legs])[:n]
    c = 30000 * np.exp(np.cumsum(trend + rng.normal(0, 0.006, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    vol = rng.uniform(1, 10, n)
    ts = pd.date_range("2023-09-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002,
                         "low": np.minimum(o, c) * 0.998, "close": c, "volume": vol, "quote_volume": vol * c,
                         "trades": rng.integers(50, 500, n).astype(float), "taker_base": vol * rng.uniform(0.3, 0.7, n)})


def test_signals_need_confirmation_and_alternate():
    close = np.concatenate([np.full(60, 100.0), [95], np.full(5, 101.0), np.full(20, 80.0)])
    df = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=len(close), freq="1h", tz="UTC"),
                       "open": close, "high": close, "low": close, "close": close, "volume": 1.0})
    one = overlay.signals(df, ma=20, confirm=1, buffer=0.0)
    three = overlay.signals(df, ma=20, confirm=3, buffer=0.0)
    assert one[60] == -1                       # single close below the average exits immediately
    assert three[60] == 0                      # with confirmation, one bad close is ignored
    events = [s for s in three if s != 0]
    assert all(a != b for a, b in zip(events, events[1:]))     # never two exits or two entries in a row


def test_capture_ratio_handles_losing_buy_and_hold():
    assert overlay.capture_ratio(0.50, 1.00) == pytest.approx(0.5)
    assert overlay.capture_ratio(-0.10, -0.50) == pytest.approx(1.8)     # lost far less than holding
    assert overlay.capture_ratio(-0.60, -0.50) == pytest.approx(0.8)     # lost more than holding
    assert overlay.capture_ratio(0.10, 0.0) == 1.0
    assert not np.isnan(overlay.capture_ratio(0.1, -0.2))                # never nan -> never a silent gate failure


def test_random_exposure_control_matches_time_in_market():
    df, cfg = sweep.prepare(market(n=9000), "4h")
    cfg = cfg.with_(sizing="all_in", stop_pct=None)
    ctrl = overlay.random_exposure_control(df, cfg, 210, exposure=0.6, switches=20, sims=60)
    assert ctrl["samples"] > 5 and np.isfinite(ctrl["calmar_p95"])


def test_control_rejects_a_rule_whose_exits_are_random(monkeypatch, tmp_path):
    """A rule with no timing skill must NOT pass the exit-timing gate."""
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path))
    df, cfg = sweep.prepare(market(n=12000), "4h")
    cfg = cfg.with_(sizing="all_in", stop_pct=None)
    start = 210
    rng = np.random.default_rng(3)
    sub = df.iloc[start:].reset_index(drop=True)
    sig = np.zeros(len(sub))
    sig[0] = 1
    state = 1
    for i in np.sort(rng.choice(np.arange(1, len(sub) - 1), size=30, replace=False)):
        state = -state
        sig[i] = state
    r = bt.run(sub, sig, cfg)
    ctrl = overlay.random_exposure_control(df, cfg, start, r.metrics["exposure"], max(r.metrics["trades"], 2), sims=120)
    assert r.metrics["calmar"] <= ctrl["calmar_p95"], "a random rule must not clear the 95th-percentile bar"


def test_overlay_run_produces_report_and_gates(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path / "cache"))
    r = overlay.run("BTCUSDT", loader=lambda s: market(seed={"BTCUSDT": 1, "ETHUSDT": 2}.get(s, 3)),
                    out_dir=str(tmp_path), timeframes=["4h"], others=("ETHUSDT",))
    assert set(r["gates"]) == {"keeps_enough_upside", "cuts_drawdown", "better_calmar_than_holding",
                               "oos_capture_holds_up", "exit_timing_beats_random", "positive_under_2x_costs"}
    text = (tmp_path / "OVERLAY_REPORT.md").read_text(encoding="utf-8")
    assert "not to beat buy-and-hold" in text.lower() or "NOT to beat buy-and-hold" in text
    assert r["verdict"].startswith(("GO", "NO-GO"))
    assert r["best"]["in_sample"]["max_drawdown"] > r["best"]["in_sample"]["hold_max_drawdown"]   # less deep


def test_walk_forward_is_out_of_sample(tmp_path, monkeypatch):
    """Parameters for each test month must come only from earlier months."""
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path))
    df, cfg = sweep.prepare(market(n=20000), "4h")
    cfg = cfg.with_(sizing="all_in", stop_pct=None)
    dev, _, _ = ev.split(df)
    seen = []
    real_select = overlay.select

    def spy(d, c, start, end):
        seen.append(end)
        return real_select(d, c, start, end)

    monkeypatch.setattr(overlay, "select", spy)
    folds, _, _ = overlay.walk_forward(dev, cfg, max(overlay.GRID["ma"]) + 5)
    months = ev.month_bounds(dev)
    for fold, train_end in zip(folds, seen):
        test_start = [m[1] for m in months if m[0] == fold["month"]][0]
        assert train_end <= test_start
