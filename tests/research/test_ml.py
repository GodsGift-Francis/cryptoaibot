import hashlib

import joblib
import numpy as np
import pandas as pd
import pytest

import indicators
from engine.strategy import ai_strategy as ai
from engine.strategy import ml_features as mf
from research import evaluate as ev
from research import ml
from research import strategies as st


def market(seed, amp, n=9 * 730):
    rng = np.random.default_rng(seed)
    drift = amp * np.sin(np.arange(n) * 2 * np.pi / 700)
    c = 30000 * np.exp(np.cumsum(drift + rng.normal(0, 0.006, n)))
    o = np.concatenate([[c[0]], c[:-1]])
    ts = pd.date_range("2026-01-01", periods=n, freq="1h", tz="UTC")
    return pd.DataFrame({"timestamp": ts, "open": o, "high": np.maximum(o, c) * 1.002, "low": np.minimum(o, c) * 0.998,
                         "close": c, "volume": rng.uniform(1, 10, n)})


@pytest.fixture(scope="module")
def cache(tmp_path_factory):
    d = tmp_path_factory.mktemp("cache")
    original, st.CACHE_DIR = st.CACHE_DIR, str(d)
    yield d
    st.CACHE_DIR = original


@pytest.fixture(scope="module")
def planted(cache):
    df = market(1, 0.0015)
    return df, ml.feature_matrix(df)


@pytest.fixture(scope="module")
def noise(cache):
    df = market(2, 0.0)
    return df, ml.feature_matrix(df)


def test_labels_start_at_next_open_and_end_unknown():
    df = market(3, 0.0, n=200)
    y = ml.labels(df, horizon=24, edge=0.005)
    o = df["open"].to_numpy()
    for i in (0, 50, 174):
        assert y[i] == float(o[i + 25] / o[i + 1] - 1 > 0.005)
    assert np.isnan(y[-25:]).all() and not np.isnan(y[174])


def test_purge_drops_rows_whose_label_reaches_the_test_period():
    y = np.zeros(1000)
    idx = ml.rows(0, 800, y, purge_before=800, horizon=24)
    assert idx.max() + 1 + 24 < 800 and len(idx) == 775


def _wf(df, X, **kw):
    dev, _, _ = ev.split(df)
    y = ml.labels(dev)
    v1 = st.thresholds(st.v1_scores(df)[: len(dev)], 2.5, -2.5)
    return ml.walk_forward(dev, X.iloc[: len(dev)].reset_index(drop=True), y, v1, st.warmup_bars() - 1, **kw)


def test_no_false_edge_on_pure_noise(noise):
    df, X = noise
    _, _, auc, _ = _wf(df, X)
    _, _, canary, _ = _wf(df, X, shuffle_seed=7)
    assert 0.44 < auc < 0.56, auc
    assert 0.44 < canary < 0.56, canary


def test_finds_a_real_pattern_and_canary_stays_clean(planted):
    df, X = planted
    _, _, auc, _ = _wf(df, X)
    _, _, canary, _ = _wf(df, X, shuffle_seed=7)
    assert auc > 0.6, auc
    assert 0.44 < canary < 0.56, canary


def _artifact(df, X, mode="veto", thr=0.55):
    y = ml.labels(df)
    idx = ml.rows(st.warmup_bars() - 1, len(df), y)
    m = ml.model().fit(X.iloc[idx], y[idx])
    import sklearn
    return {"model": m, "meta": {"mode": mode, "threshold": thr, "exit_p": 0.5, "edge": ml.EDGE, "features": mf.FEATURES,
                                 "feature_version": mf.FEATURE_VERSION, "sklearn": sklearn.__version__}}


def live_cfg(mode):
    c = st.cfg_copy(STRATEGY_MODE=mode, ML_MODEL_SHA256="")
    return c


def test_live_probability_equals_research_probability(planted):
    df, X = planted
    art = _artifact(df, X)
    strat = ai.AIStrategy(live_cfg("veto"), artifact=art)
    cfg = strat.cfg
    w = st.warmup_bars()
    for i in (1500, 3000, len(df) - 1):
        live_frame = indicators.add_all_indicators(df.iloc[i - w + 1:i + 1].reset_index(drop=True), cfg)   # what the engine passes
        live_p = strat.probability(live_frame)
        research_p = art["model"].predict_proba(X.iloc[[i]][mf.FEATURES])[0, 1]
        assert live_p == pytest.approx(research_p, abs=1e-12)


def test_veto_never_adds_a_buy_and_ml_mode_maps_probabilities():
    v1 = np.array([1, 1, 0, -1, 0, 1])
    p = np.array([0.9, 0.3, 0.9, 0.9, np.nan, np.nan])
    assert list(ml.veto_signals(v1, p, 0.55)) == [1, 0, 0, -1, 0, 0]
    assert list(ml.ml_signals(np.array([0.7, 0.52, 0.4, np.nan]), 0.6)) == [1, 0, -1, 0]


def test_model_with_wrong_hash_is_never_unpickled(tmp_path, monkeypatch, planted):
    df, X = planted
    path = tmp_path / "model.joblib"
    joblib.dump(_artifact(df, X), path)
    called = []
    monkeypatch.setattr(joblib, "load", lambda *a, **k: called.append(1))
    with pytest.raises(ai.ModelRejected, match="does not match"):
        ai.load_verified(str(path), "0" * 64)
    assert not called
    monkeypatch.undo()
    good = hashlib.sha256(path.read_bytes()).hexdigest()
    assert ai.load_verified(str(path), good)["meta"]["mode"] == "veto"


def test_mode_mismatch_and_config_validation(tmp_path, planted):
    df, X = planted
    with pytest.raises(ai.ModelRejected, match="validated for mode"):
        ai.AIStrategy(live_cfg("ml"), artifact=_artifact(df, X, mode="veto"))
    from tests.conftest import make_cfg
    with pytest.raises(RuntimeError, match="ML_MODEL_PATH"):
        make_cfg(STRATEGY_MODE="ml", ML_MODEL_PATH="", ML_MODEL_SHA256="").validate_runtime()
    with pytest.raises(RuntimeError, match="STRATEGY_MODE"):
        make_cfg(STRATEGY_MODE="yolo").validate_runtime()


def test_engine_cycle_runs_with_ai_strategy(tmp_path, planted):
    from datetime import timedelta
    from engine.market_data.fetcher import MarketDataService
    from tests.conftest import Harness
    df, X = planted
    path = tmp_path / "model.joblib"
    joblib.dump(_artifact(df, X, mode="ml", thr=0.0), path)                        # threshold 0: always wants in
    h = Harness(tmp_path, "PAPER", STRATEGY_MODE="ml", ML_MODEL_PATH=str(path),
                ML_MODEL_SHA256=hashlib.sha256(path.read_bytes()).hexdigest())
    h.s.cycle.strategy = ai.build_strategy(h.cfg)                                  # the production loader path
    now = df.timestamp.iloc[-1].to_pydatetime() + timedelta(minutes=1)
    h.clock.set(now)

    def fetch(symbol, tf, limit):
        out = df[df.timestamp <= now].tail(limit).reset_index(drop=True)
        out["timestamp"] = out["timestamp"].dt.tz_localize(None)
        return out

    h.s.cycle.market = MarketDataService(h.cfg, h.clock, fetch_ohlcv=fetch, fetch_global=lambda: None, fetch_news=lambda: [])
    r = h.s.cycle.run_once()
    assert r.signal is not None, r.action_taken
    assert r.signal.action == "BUY" and r.signal.reasons[0].startswith("AI p(")
    assert r.action_taken == "BUY"


def test_walk_forward_never_trains_on_labels_that_reach_the_period_it_predicts(planted, monkeypatch):
    df, X = planted
    fits, real = [], ml.model

    class Spy:
        def __init__(self):
            self.m = real()

        def fit(self, Xf, yf):
            fits.append(int(Xf.index.max()))
            self.m.fit(Xf, yf)
            return self

        def predict_proba(self, Xp):
            fits.append(("predict", int(Xp.index.min())))
            return self.m.predict_proba(Xp)

    monkeypatch.setattr(ml, "model", Spy)
    _wf(df, X)
    last_fit = None
    checked = 0
    for ev_ in fits:
        if isinstance(ev_, tuple):
            assert last_fit + 1 + ml.HORIZON < ev_[1], f"trained through {last_fit}, predicted from {ev_[1]}"
            checked += 1
        else:
            last_fit = ev_
    assert checked >= 6                                  # validation + test predictions across folds
