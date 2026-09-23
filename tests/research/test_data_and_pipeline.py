import hashlib
import io
import os
import zipfile
from datetime import date

import numpy as np
import pandas as pd
import pytest

from research import data as rd
from research import evaluate as ev
from research import strategies as st


def _zip(rows):
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("k.csv", "\n".join(",".join(map(str, r)) for r in rows))
    return buf.getvalue()


def _rows(start_ms, n, micro=False):
    out = []
    for i in range(n):
        t = start_ms + i * 3_600_000
        t = t * 1000 if micro else t
        out.append([t, 100, 101, 99, 100.5, 1, t + 1, 1, 1, 1, 1, 0])
    return out


def test_last_complete_months():
    assert rd.last_complete_months(12, date(2026, 9, 22)) == [f"2025-{m:02d}" for m in range(9, 13)] + [f"2026-{m:02d}" for m in range(1, 9)]


def test_microsecond_and_millisecond_timestamps_normalise():
    ms = rd.parse_klines_csv("\n".join(",".join(map(str, r)) for r in _rows(1735689600000, 2)).encode())
    us = rd.parse_klines_csv("\n".join(",".join(map(str, r)) for r in _rows(1735689600000, 2, micro=True)).encode())
    assert list(ms.timestamp) == list(us.timestamp) and str(ms.timestamp[0]) == "2025-01-01 00:00:00+00:00"


def test_checksum_is_enforced():
    blob = _zip(_rows(1735689600000, 3))
    good = {".zip": blob, ".CHECKSUM": (hashlib.sha256(blob).hexdigest() + "  f.zip").encode()}
    bad = {".zip": blob, ".CHECKSUM": b"0" * 64}
    fetch = lambda table: (lambda url: table[".CHECKSUM"] if url.endswith(".CHECKSUM") else table[".zip"])
    df, _ = rd.fetch_month("BTCUSDT", "1h", "2025-01", fetch(good))
    assert len(df) == 3
    with pytest.raises(ValueError, match="checksum"):
        rd.fetch_month("BTCUSDT", "1h", "2025-01", fetch(bad))


def test_validation_flags_gaps_and_bad_candles():
    df = rd.parse_klines_csv("\n".join(",".join(map(str, r)) for r in _rows(1735689600000, 5)).encode())
    df = df.drop(index=2).reset_index(drop=True)
    df.loc[0, "low"] = 200
    problems = rd.validate(df, 3600)
    assert any("gap" in p for p in problems) and any("OHLC" in p for p in problems)


def synth(sym, months=8):
    rng = np.random.default_rng(len(sym))
    ts = pd.date_range("2026-01-01", "2026-08-31 23:00", freq="1h", tz="UTC")
    n = len(ts)
    c = 30000 * np.exp(np.cumsum(0.002 * np.sin(np.arange(n) * 2 * np.pi / 500) + rng.normal(0, 0.006, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998,
                         "close": c, "volume": 1.0})


def test_pipeline_end_to_end_and_holdout_is_one_shot(tmp_path, monkeypatch):
    monkeypatch.setattr(st, "CACHE_DIR", str(tmp_path / "cache"))
    r = ev.evaluate(("BTCUSDT", "ETHUSDT"), loader=synth, out_dir=str(tmp_path), seeds=20)
    assert set(r["gates"]) == {"min_oos_trades", "oos_positive_after_costs", "oos_sharpe_beats_best_competitor",
                               "beats_random_percentile", "positive_under_2x_costs", "neighbour_stability"}
    assert r["holdout_period"][0].startswith("2026-06-01")                  # last 3 months held back
    assert r["dev_period"][1].startswith("2026-05-31")
    assert os.path.exists(tmp_path / "REPORT.md") and r["verdict"] in ("NO-GO", "GO: run the one-shot holdout")
    ev.holdout("BTCUSDT", loader=synth, out_dir=str(tmp_path))
    with pytest.raises(SystemExit, match="already used"):
        ev.holdout("BTCUSDT", loader=synth, out_dir=str(tmp_path))


def test_dev_scores_never_see_holdout_candles():
    df = synth("BTCUSDT")
    dev, hold, _ = ev.split(df)
    full = st.v1_scores(df, cache=False)
    only_dev = st.v1_scores(dev, cache=False)
    np.testing.assert_array_equal(full[: len(dev)], only_dev)
