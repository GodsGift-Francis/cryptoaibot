import numpy as np
import pandas as pd
import pytest

from research import data as rd
from research import evaluate as ev
from research import strategies as st
from research import sweep


def hourly(n=26304, seed=4, amp=0.0006, period=2400):
    rng = np.random.default_rng(seed)
    drift = amp * np.sin(np.arange(n) * 2 * np.pi / period)
    c = 30000 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    vol = rng.uniform(1, 10, n)
    ts = pd.date_range("2023-09-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002,
                         "low": np.minimum(o, c) * 0.998, "close": c, "volume": vol, "quote_volume": vol * c,
                         "trades": rng.integers(50, 500, n).astype(float), "taker_base": vol * rng.uniform(0.3, 0.7, n)})


def test_resample_preserves_ohlc_semantics_and_sums_flow():
    df = hourly(n=48)
    r = rd.resample(df, "4h")
    assert len(r) == 12
    first = df.iloc[:4]
    assert r.open.iloc[0] == first.open.iloc[0] and r.close.iloc[0] == first.close.iloc[-1]
    assert r.high.iloc[0] == first.high.max() and r.low.iloc[0] == first.low.min()
    assert r.volume.iloc[0] == pytest.approx(first.volume.sum())
    assert r.taker_base.iloc[0] == pytest.approx(first.taker_base.sum())
    assert (r.taker_base <= r.volume).all()


def test_resample_drops_incomplete_final_bucket():
    assert len(rd.resample(hourly(n=50), "4h")) == 12          # 48 complete hours, 2 left over
    assert rd.bars_per_year("1h") == 8760 and rd.bars_per_year("1d") == 365


def test_stops_scale_with_bar_size():
    assert max(sweep.STOPS["1h"]) < max(sweep.STOPS["4h"]) < max(sweep.STOPS["1d"])
    assert sweep.grid_for("1d")["stop"] == sweep.STOPS["1d"]


def test_sharpe_is_annualised_per_timeframe():
    _, cfg_1h = sweep.prepare(hourly(n=500), "1h")
    _, cfg_1d = sweep.prepare(hourly(n=500), "1d")
    assert cfg_1h.bars_per_year == 8760 and cfg_1d.bars_per_year == 365


def test_luck_bar_uses_the_ranking_metric_and_scales_with_variant_count(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path / "cache"))
    df, cfg = sweep.prepare(hourly(n=9000), "4h")
    warm = st.warmup_bars() - 1
    few = sweep.best_of_n_random(df, cfg, warm, trades=30, stop=5.0, n_variants=1, tf="4h", sims=60)
    many = sweep.best_of_n_random(df, cfg, warm, trades=30, stop=5.0, n_variants=20, tf="4h", sims=60)
    assert set(few) == {"sharpe_p95", "sharpe_median", "return_p95", "return_median"}
    assert many["sharpe_median"] > few["sharpe_median"]        # more attempts -> luckier best result
    assert many["return_median"] > few["return_median"]


def test_sweep_runs_and_only_flags_a_candidate_that_beats_the_luck_bar(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path / "cache"))
    r = sweep.run("BTCUSDT", loader=lambda s: hourly(), out_dir=str(tmp_path), timeframes=["1d"], horizons=[72])
    assert r["variants_tested"] == 3                            # rules + ai-ml + ai-veto
    assert (tmp_path / "SWEEP_REPORT.md").exists()
    best, luck = r["best"], r["luck_bar"]
    beat = best["oos_sharpe"] > luck["sharpe_p95"] and best["oos_return"] > luck["return_p95"]
    assert r["verdict"].startswith("CANDIDATE") == beat
    assert "not a GO" in (tmp_path / "SWEEP_REPORT.md").read_text(encoding="utf-8")


def test_short_folds_are_skipped_not_crashed(tmp_path, monkeypatch):
    """Daily bars leave few bars per month; folds shorter than the warmup must be skipped cleanly."""
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path / "cache"))
    df, cfg = sweep.prepare(hourly(n=12000), "1d")
    dev, _, _ = ev.split(df)
    scores = st.v1_scores(dev, cache=False)
    folds, wf = ev.walk_forward(dev, scores, st.warmup_bars() - 1, grid=sweep.grid_for("1d"), cfg=cfg)
    assert isinstance(wf["oos_total_return"], float)
