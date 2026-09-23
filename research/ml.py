"""AI layer research: a gradient-boosted model that predicts whether the next 24h beats trading costs.

    python -m research.ml               # purged walk-forward on the development months -> research/out/ML_REPORT.md
    python -m research.ml --holdout     # ONE run of the saved model on the held-back months, then locked

Two ways to use the model, both evaluated:
  veto  - V1 decides; the model may BLOCK a V1 buy when p(up) < threshold   (lower risk, fewer trades)
  ml    - the model decides: buy when p >= threshold, sell when p < 0.5     (the model is the strategy)

Leakage defences: causal features (same function as live), labels start at the NEXT bar's open,
training rows purged when their label window reaches the test period, thresholds chosen on a purged
validation month inside the training data, and a shuffled-label canary that must score AUC ~0.5.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from datetime import datetime, timezone

import joblib
import numpy as np
import pandas as pd
import sklearn
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.metrics import roc_auc_score

from engine.strategy import ml_features as mf
from research import backtest as bt
from research import data as rdata
from research import evaluate as ev
from research import strategies as st

HORIZON = 24                                    # bars ahead the label looks
EDGE = 2 * (ev.COSTS.fee_rate + ev.COSTS.slippage_bps / 1e4) + 0.002   # beat round-trip costs + 0.2%
THRESHOLDS = [0.5, 0.55, 0.6, 0.65]
EXIT_P = 0.5
MODEL_DIR = os.path.join(ev.OUT_DIR, "model")
ML_GATES = {"min_pooled_auc": 0.52, "canary_auc_band": (0.45, 0.55), "min_oos_trades": 20}


# ------------------------------------------------------------------ data
def feature_matrix(df: pd.DataFrame, cfg=None, cache: bool = True) -> pd.DataFrame:
    cfg = cfg or st.cfg_copy()
    w = st.warmup_bars(cfg)
    key = hashlib.sha256(pd.util.hash_pandas_object(df[["timestamp", "close", "volume"]], index=False).values.tobytes()
                         + f"{mf.FEATURE_VERSION}{w}".encode()).hexdigest()[:24]
    path = os.path.join(st.CACHE_DIR, f"features_{key}.pkl")
    if cache and os.path.exists(path):
        return pd.read_pickle(path)
    missing = [c for c in mf.FLOW if c not in df.columns]
    if missing:
        raise SystemExit(f"This data file predates the order-flow features (missing {missing}).\n"
                         "Re-download it once:  python -m research --months 36 --symbols BTCUSDT")
    raw = df[mf.RAW + mf.FLOW].reset_index(drop=True)
    rows = [None] * len(raw)
    for i in range(w - 1, len(raw)):
        rows[i] = mf.window_features(raw.iloc[i - w + 1:i + 1], cfg)
    X = pd.DataFrame([r if r is not None else {k: np.nan for k in mf.FEATURES} for r in rows], columns=mf.FEATURES)
    if cache:
        os.makedirs(st.CACHE_DIR, exist_ok=True)
        X.to_pickle(path)
    return X


def labels(df: pd.DataFrame, horizon: int = HORIZON, edge: float = EDGE) -> np.ndarray:
    """1 if buying at the next bar's open and selling `horizon` bars later beats `edge`. NaN where unknown."""
    o = df["open"].to_numpy(float)
    y = np.full(len(o), np.nan)
    n = len(o)
    for i in range(n - horizon - 1):
        y[i] = float(o[i + 1 + horizon] / o[i + 1] - 1 > edge)
    return y


def model():
    return HistGradientBoostingClassifier(max_depth=3, learning_rate=0.05, max_iter=200, min_samples_leaf=50,
                                          l2_regularization=1.0, random_state=0)


def rows(start, end, y, purge_before=None, horizon=HORIZON):
    """Row indices in [start, end) with a known label; purge rows whose label window reaches `purge_before`."""
    idx = np.arange(start, end)
    idx = idx[~np.isnan(y[idx])]
    if purge_before is not None:
        idx = idx[idx + 1 + horizon < purge_before]
    return idx


# ------------------------------------------------------------------ signals
def ml_signals(p: np.ndarray, thr: float, exit_p: float = EXIT_P) -> np.ndarray:
    sig = np.zeros(len(p))
    sig[p >= thr] = 1
    sig[p < exit_p] = -1
    sig[np.isnan(p)] = 0
    return sig


def veto_signals(v1_sig: np.ndarray, p: np.ndarray, thr: float) -> np.ndarray:
    sig = v1_sig.copy()
    sig[(v1_sig > 0) & ~(p >= thr)] = 0             # model may only block buys; exits stay V1's
    return sig


def _signals(mode, p, v1_sig, thr):
    return ml_signals(p, thr) if mode == "ml" else veto_signals(v1_sig, p, thr)


def _bt(df, sig, start, end, cfg=ev.COSTS, initial=None, stop=None):
    sub = df.iloc[start:end].reset_index(drop=True)
    c = cfg.with_(stop_pct=stop or ev.V1_DEFAULT["stop"], **({"initial": initial} if initial is not None else {}))
    return bt.run(sub, sig[start:end], c)


def choose_threshold(mode, df, X, y, v1_sig, fit_idx, val_start, val_end):
    """Fit on fit_idx, pick the threshold with the best Sharpe on the (purged) validation month."""
    if len(fit_idx) < 200 or len(np.unique(y[fit_idx])) < 2:
        return 0.55
    m = model().fit(X.iloc[fit_idx], y[fit_idx])
    p = np.full(len(df), np.nan)
    p[val_start:val_end] = m.predict_proba(X.iloc[val_start:val_end])[:, 1]
    best, best_s = 0.55, -np.inf
    for thr in THRESHOLDS:
        r = _bt(df, _signals(mode, p, v1_sig, thr), val_start, val_end)
        if r.metrics["trades"] >= 1 and r.metrics["sharpe"] > best_s:
            best, best_s = thr, r.metrics["sharpe"]
    return best


# ------------------------------------------------------------------ walk-forward
def walk_forward(df, X, y, v1_sig, warm, shuffle_seed=None, horizon=HORIZON, cfg=ev.COSTS, stop=None,
                 val_months=3):
    months = ev.month_bounds(df)
    y_fit = y.copy()
    if shuffle_seed is not None:                   # leakage canary
        known = ~np.isnan(y_fit)
        y_fit[known] = np.random.default_rng(shuffle_seed).permutation(y_fit[known])
    probs = np.full(len(df), np.nan)
    folds, eq = [], {"ml": 1000.0, "veto": 1000.0}
    eq_stress = {"ml": 1000.0, "veto": 1000.0}
    eq_full = {"ml": 1000.0, "veto": 1000.0}                  # same signals, all-in sizing (profit comparison)
    trades = {"ml": 0, "veto": 0}
    for k in range(3, len(months)):
        name, te_start, te_end = months[k]
        te_start = max(te_start, warm)
        va_start, va_end = months[max(k - val_months, 1)][1], months[k - 1][2]   # 3 validation months, not 1
        if te_end - te_start < 2:
            continue
        train_idx = rows(warm, te_start, y_fit, purge_before=te_start, horizon=horizon)
        if len(train_idx) < 200 or len(np.unique(y_fit[train_idx])) < 2:
            continue
        inner_idx = rows(warm, va_start, y_fit, purge_before=va_start, horizon=horizon)
        thr = {mode: choose_threshold(mode, df, X, y_fit, v1_sig, inner_idx, va_start, va_end) for mode in ("ml", "veto")}
        m = model().fit(X.iloc[train_idx], y_fit[train_idx])
        probs[te_start:te_end] = m.predict_proba(X.iloc[te_start:te_end])[:, 1]
        test_known = rows(te_start, te_end, y, horizon=horizon)
        auc = (roc_auc_score(y[test_known], probs[test_known])
               if len(test_known) and len(np.unique(y[test_known])) == 2 else np.nan)
        fold = {"test_month": name, "auc": auc, "train_rows": int(len(train_idx)), "thresholds": thr}
        for mode in ("ml", "veto"):
            sig = _signals(mode, probs, v1_sig, thr[mode])
            r = _bt(df, sig, te_start, te_end, initial=eq[mode])
            rs = _bt(df, sig, te_start, te_end, cfg=ev.STRESS, initial=eq_stress[mode])
            rf = _bt(df, sig, te_start, te_end, cfg=ev.COSTS.with_(sizing="all_in"), initial=eq_full[mode])
            eq_full[mode] = rf.metrics["final_equity"]
            fold[f"{mode}_return"] = r.metrics["total_return"]
            fold[f"{mode}_trades"] = r.metrics["trades"]
            eq[mode], eq_stress[mode] = r.metrics["final_equity"], rs.metrics["final_equity"]
            trades[mode] += r.metrics["trades"]
        folds.append(fold)
    oos = ~np.isnan(probs) & ~np.isnan(y)
    pooled = roc_auc_score(y[oos], probs[oos]) if oos.sum() and len(np.unique(y[oos])) == 2 else np.nan
    summary = {mode: {"oos_total_return": eq[mode] / 1000 - 1, "oos_trades": trades[mode],
                      "oos_2x_costs_return": eq_stress[mode] / 1000 - 1,
                      "oos_full_alloc_return": eq_full[mode] / 1000 - 1,
                      "oos_monthly_sharpe": _sharpe([f[f"{mode}_return"] for f in folds])} for mode in ("ml", "veto")}
    return folds, summary, float(pooled), probs


def _sharpe(rets):
    return float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(12)) if len(rets) > 1 and np.std(rets) > 0 else 0.0


# ------------------------------------------------------------------ evaluation
def evaluate(symbol="BTCUSDT", loader=rdata.load, out_dir=ev.OUT_DIR, seeds=200) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    df_all = loader(symbol)
    dev, hold, _ = ev.split(df_all)
    warm = st.warmup_bars() - 1
    X = feature_matrix(df_all).iloc[: len(dev)].reset_index(drop=True)
    y = labels(dev)                                             # dev labels never read holdout prices
    v1_sig = st.thresholds(st.v1_scores(df_all)[: len(dev)], ev.V1_DEFAULT["buy"], ev.V1_DEFAULT["sell"])

    folds, summary, pooled_auc, probs = walk_forward(dev, X, y, v1_sig, warm)
    _, _, canary_auc, _ = walk_forward(dev, X, y, v1_sig, warm, shuffle_seed=7)

    # the same out-of-sample window for V1 and competitors
    months = ev.month_bounds(dev)
    oos_start_i = max(months[3][1], warm)
    oos_start = dev["timestamp"].iloc[oos_start_i]
    v1_oos = _bt(dev, v1_sig, oos_start_i, len(dev))
    comp = ev.competitors(dev, warm)
    comp_oos = {k: {"monthly_sharpe": float(ev._monthly_sharpe(v, oos_start)),
                    "return": float(v.equity[v.equity.index >= oos_start].iloc[-1] / v.equity[v.equity.index < oos_start].iloc[-1] - 1)}
                for k, v in comp.items()}
    best_comp = max(comp_oos, key=lambda k: comp_oos[k]["monthly_sharpe"])
    v1_oos_sharpe = float(ev._monthly_sharpe(v1_oos, oos_start))

    results = {}
    for mode in ("ml", "veto"):
        s = summary[mode]
        n_tr = max(s["oos_trades"], 1)
        oos_sub = dev.iloc[oos_start_i:].reset_index(drop=True)
        rnd = np.array([bt.run(oos_sub, st.random_entries(len(oos_sub), 0, n_tr, sd), ev.COSTS.with_(max_hold_bars=HORIZON)).metrics["total_return"]
                        for sd in range(seeds)])
        pct_rank = float((rnd < s["oos_total_return"]).mean() * 100)
        gates = {
            "pooled_auc_above_chance": bool(pooled_auc >= ML_GATES["min_pooled_auc"]),
            "leakage_canary_clean": bool(ML_GATES["canary_auc_band"][0] <= canary_auc <= ML_GATES["canary_auc_band"][1]),
            "min_oos_trades": bool(s["oos_trades"] >= ML_GATES["min_oos_trades"]),
            "oos_positive_after_costs": bool(s["oos_total_return"] > 0),
            "beats_v1_default_oos": bool(s["oos_total_return"] > v1_oos.metrics["total_return"]
                                         and s["oos_monthly_sharpe"] > v1_oos_sharpe),
            "oos_sharpe_beats_best_competitor": bool(s["oos_monthly_sharpe"] > comp_oos[best_comp]["monthly_sharpe"]),
            "beats_random_percentile": bool(pct_rank >= ev.GATES["beats_random_percentile"]),
            "positive_under_2x_costs": bool(s["oos_2x_costs_return"] > 0),
            # the stated goal: out-earn the best competitor, compared at full allocation
            "profit_beats_best_competitor": bool(s["oos_full_alloc_return"] > max(v["return"] for v in comp_oos.values())),
        }
        results[mode] = {**s, "random_percentile": pct_rank, "gates": gates, "go": all(gates.values()),
                         "threshold": float(np.median([f["thresholds"][mode] for f in folds])) if folds else 0.55}

    passing = [m for m in ("veto", "ml") if results[m]["go"]]
    chosen = max(passing, key=lambda m: results[m]["oos_monthly_sharpe"]) if passing else None
    artifact = train_final(dev, X, y, warm, chosen, results[chosen]["threshold"], out_dir) if chosen else None

    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "horizon": HORIZON, "edge": EDGE,
           "dev_months": len(months),
           "features": mf.FEATURES, "pooled_auc": pooled_auc, "canary_auc": canary_auc, "folds": folds,
           "modes": results, "v1_default_oos": {**v1_oos.metrics, "monthly_sharpe": v1_oos_sharpe},
           "competitors_oos": comp_oos, "best_competitor": best_comp, "chosen_mode": chosen, "artifact": artifact,
           "verdict": f"GO ({chosen}): run the one-shot holdout" if chosen else "NO-GO"}
    json.dump(out, open(os.path.join(out_dir, "ml_results.json"), "w", encoding="utf-8"), indent=2, default=str)
    open(os.path.join(out_dir, "ML_REPORT.md"), "w", encoding="utf-8").write(report(out))
    return out


def train_final(dev, X, y, warm, mode, thr, out_dir) -> dict:
    idx = rows(warm, len(dev), y)
    m = model().fit(X.iloc[idx], y[idx])
    os.makedirs(os.path.join(out_dir, "model"), exist_ok=True)
    meta = {"mode": mode, "threshold": thr, "exit_p": EXIT_P, "horizon": HORIZON, "edge": EDGE,
            "features": mf.FEATURES, "feature_version": mf.FEATURE_VERSION, "sklearn": sklearn.__version__,
            "trained_rows": int(len(idx)), "trained_until": str(dev["timestamp"].iloc[-1]),
            "created_at": datetime.now(timezone.utc).isoformat()}
    path = os.path.join(out_dir, "model", "model.joblib")
    joblib.dump({"model": m, "meta": meta}, path)
    meta["sha256"] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    meta["path"] = path
    json.dump(meta, open(os.path.join(out_dir, "model", "model.meta.json"), "w", encoding="utf-8"), indent=2)
    return meta


def holdout(symbol="BTCUSDT", loader=rdata.load, out_dir=ev.OUT_DIR, force=False) -> dict:
    res_path = os.path.join(out_dir, "ml_results.json")
    lock = os.path.join(out_dir, "ML_HOLDOUT_LOCK.json")
    if not os.path.exists(res_path):
        raise SystemExit("run `python -m research.ml` first")
    dev_res = json.load(open(res_path, encoding="utf-8"))
    if not dev_res.get("artifact"):
        raise SystemExit("no model passed the development gates; nothing to test on the holdout")
    if os.path.exists(lock) and not force:
        raise SystemExit(f"ML holdout already used ({json.load(open(lock, encoding='utf-8'))['used_at']}).")
    art = joblib.load(dev_res["artifact"]["path"])
    meta = art["meta"]
    df_all = loader(symbol)
    dev, hold, _ = ev.split(df_all)
    X = feature_matrix(df_all).iloc[len(dev):].reset_index(drop=True)
    p = art["model"].predict_proba(X[meta["features"]])[:, 1]
    v1_sig = st.thresholds(st.v1_scores(df_all)[len(dev):], ev.V1_DEFAULT["buy"], ev.V1_DEFAULT["sell"])
    sig = _signals(meta["mode"], p, v1_sig, meta["threshold"])
    r = _bt(hold, sig, 0, len(hold))
    v1 = _bt(hold, v1_sig, 0, len(hold))
    bh = bt.run(hold, st.buy_hold(hold, 0), ev.COSTS.with_(sizing="all_in", stop_pct=None))
    passed = r.metrics["total_return"] > 0 and r.metrics["total_return"] >= v1.metrics["total_return"]
    res = {"used_at": datetime.now(timezone.utc).isoformat(), "forced": bool(force), "mode": meta["mode"],
           "model_sha256": dev_res["artifact"]["sha256"], "holdout": r.metrics, "v1_default": v1.metrics,
           "buy_hold": bh.metrics, "passed": passed,
           "verdict": "GO: forward-test in PAPER/TESTNET" if passed else "NO-GO"}
    json.dump(res, open(lock, "w", encoding="utf-8"), indent=2, default=str)
    with open(os.path.join(out_dir, "ML_REPORT.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## Holdout (one shot, {res['used_at'][:10]}{' FORCED' if force else ''})\n\n"
                f"| | AI ({meta['mode']}) | V1 default | Buy & hold |\n|---|---|---|---|\n"
                f"| Return | {ev.pct(r.metrics['total_return'])} | {ev.pct(v1.metrics['total_return'])} | {ev.pct(bh.metrics['total_return'])} |\n"
                f"| Max drawdown | {ev.pct(r.metrics['max_drawdown'])} | {ev.pct(v1.metrics['max_drawdown'])} | {ev.pct(bh.metrics['max_drawdown'])} |\n"
                f"| Trades | {r.metrics['trades']} | {v1.metrics['trades']} | 1 |\n\n**Final verdict: {res['verdict']}**\n")
    return res


def report(r: dict) -> str:
    v1 = r["v1_default_oos"]
    lines = [f"# AI layer evaluation — {r['symbol']}", "",
             f"Model: gradient-boosted trees predicting P(next {r['horizon']}h return > {r['edge']*100:.2f}%). "
             f"{len(r['features'])} causal features computed exactly as the live engine sees them.", "",
             f"Pooled out-of-sample AUC **{r['pooled_auc']:.3f}** (0.5 = coin flip). Leakage canary (shuffled labels) "
             f"AUC {r['canary_auc']:.3f} — must be near 0.5.", "",
             "## Out-of-sample results (same months for everyone, after costs)", "",
             "| Strategy | Return (V1 sizing) | Return (full allocation) | Monthly Sharpe | Trades |", "|---|---|---|---|---|",
             f"| V1 default | {ev.pct(v1['total_return'])} | – | {v1['monthly_sharpe']:.2f} | {v1['trades']} |"]
    for mode, label in (("veto", "AI veto on V1"), ("ml", "AI-only")):
        m = r["modes"][mode]
        lines.append(f"| {label} (p ≥ {m['threshold']:.2f}) | {ev.pct(m['oos_total_return'])} | {ev.pct(m['oos_full_alloc_return'])} | "
                     f"{m['oos_monthly_sharpe']:.2f} | {m['oos_trades']} |")
    for k, v in r["competitors_oos"].items():
        lines.append(f"| {k} | – | {ev.pct(v['return'])} | {v['monthly_sharpe']:.2f} | – |")
    lines += ["", "| Test month | AUC | AI-only | AI veto | Thresholds (ml / veto) |", "|---|---|---|---|---|"]
    for f in r["folds"]:
        lines.append(f"| {f['test_month']} | {f['auc']:.3f} | {ev.pct(f['ml_return'])} | {ev.pct(f['veto_return'])} | "
                     f"{f['thresholds']['ml']} / {f['thresholds']['veto']} |")
    for mode, label in (("veto", "AI veto"), ("ml", "AI-only")):
        lines += ["", f"### Gates — {label}", ""] + [f"- {'PASS' if v else 'FAIL'} — {k}" for k, v in r["modes"][mode]["gates"].items()]
    lines += ["", f"**Verdict: {r['verdict']}**"]
    if r["artifact"]:
        lines += ["", f"Model saved: `{r['artifact']['path']}`", f"SHA-256: `{r['artifact']['sha256']}`",
                  "Pin both in the engine `.env` (see research/README.md) only after the holdout passes."]
    lines += ["", f"Development data covers {r['dev_months']} months. Provisional: a GO earns a forward test "
              "in PAPER/TESTNET, not live money."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    if a.holdout:
        print(json.dumps(holdout(a.symbol.upper(), force=a.force), indent=2, default=str))
    else:
        evaluate(a.symbol.upper())
        print(open(os.path.join(ev.OUT_DIR, "ML_REPORT.md"), encoding="utf-8").read())
