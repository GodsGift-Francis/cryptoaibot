import numpy as np
import pandas as pd

from research import evaluate as ev


def test_zero_carried_equity_is_not_reset_to_initial():
    df = pd.DataFrame({"timestamp": pd.date_range("2026-01-01", periods=10, freq="1h", tz="UTC"),
                       "open": 100., "high": 100., "low": 100., "close": 100., "volume": 1.})
    assert ev.v1_run(df, np.zeros(10), ev.V1_DEFAULT, initial=0.0).metrics["final_equity"] == 0.0


def test_monthly_returns_count_every_month_in_window():
    idx = pd.date_range("2025-12-01", "2026-06-30 23:00", freq="1h", tz="UTC")
    eq = pd.Series(1000 * 1.1 ** pd.factorize(idx.strftime("%Y-%m"))[0], index=idx)
    m = ev.monthly_returns(eq, pd.Timestamp("2026-01-01", tz="UTC"))
    assert len(m) == 6 and np.allclose(m.values, 0.1)
