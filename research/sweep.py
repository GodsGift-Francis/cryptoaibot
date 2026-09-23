"""Timeframe + horizon sweep: does the edge live somewhere other than 1-hour bars?

    python -m research.sweep                    # uses the 1h data already downloaded

Runs the SAME pipeline across 1h / 4h / 1d candles and 12h / 24h / 72h prediction horizons, for both
the rules strategy (V1) and the AI. Costs scale with trade count, so slower bars are the cheapest
lever left after order flow failed to add anything at 1h.

Honesty problem this file exists to solve: trying many variants guarantees one looks good by luck.
So the best variant is not compared against zero — it is compared against the BEST OF THE SAME NUMBER
of random-entry strategies. Beating that is the bar; beating zero is not.
"""
from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research import backtest as bt
from research import data as rdata
from research import evaluate as ev
from research import ml
from research import strategies as st

TIMEFRAMES = ["1h", "4h", "1d"]
HORIZON_HOURS = [12, 24, 72]
# stops must scale with bar size: a 2% stop on daily candles is inside one day's noise
STOPS = {"1h": [2.0, 3.0, 4.0, 5.0, 6.0], "4h": [3.0, 5.0, 7.0, 9.0, 12.0], "1d": [5.0, 8.0, 12.0, 16.0, 20.0]}


def grid_for(tf: str) -> dict:
    return {"buy": ev.GRID["buy"], "sell": ev.GRID["sell"], "stop": STOPS[tf]}


def prepare(df1h: pd.DataFrame, tf: str):
    df = df1h if tf == "1h" else rdata.resample(df1h, tf)
    cfg = ev.COSTS.with_(bars_per_year=rdata.bars_per_year(tf))
    return df.reset_index(drop=True), cfg


def run_rules(df, cfg, tf, warm):
    scores = st.v1_scores(df)
    folds, wf = ev.walk_forward(df, scores, warm, grid=grid_for(tf), cfg=cfg)
    default = ev.v1_run(df, scores, ev.V1_DEFAULT, cfg=cfg.with_(stop_pct=STOPS[tf][1]), start=warm)
    return scores, {"variant": f"rules {tf}", "timeframe": tf, "kind": "rules",
                    "oos_return": wf["oos_total_return"], "oos_sharpe": wf["oos_monthly_sharpe"],
                    "oos_trades": wf["oos_trades"], "in_sample_return": default.metrics["total_return"],
                    "months": len(folds)}


def run_ai(df, cfg, tf, warm, scores, horizon_hours):
    bars = max(int(horizon_hours / (pd.Timedelta(rdata._rule(tf)) / pd.Timedelta("1h"))), 1)
    dev_labels = ml.labels(df, horizon=bars)
    X = ml.feature_matrix(df)
    v1_sig = st.thresholds(scores, ev.V1_DEFAULT["buy"], ev.V1_DEFAULT["sell"])
    stop = STOPS[tf][1]
    folds, summary, auc, _ = ml.walk_forward(df, X, dev_labels, v1_sig, warm, horizon=bars, cfg=cfg, stop=stop)
    _, _, canary, _ = ml.walk_forward(df, X, dev_labels, v1_sig, warm, shuffle_seed=7, horizon=bars, cfg=cfg, stop=stop)
    out = []
    for mode in ("ml", "veto"):
        s = summary[mode]
        out.append({"variant": f"ai-{mode} {tf} {horizon_hours}h", "timeframe": tf, "kind": f"ai-{mode}",
                    "horizon_hours": horizon_hours, "auc": auc, "canary_auc": canary,
                    "oos_return": s["oos_total_return"], "oos_sharpe": s["oos_monthly_sharpe"],
                    "oos_trades": s["oos_trades"], "oos_full_alloc_return": s["oos_full_alloc_return"],
                    "months": len(folds)})
    return out


def best_of_n_random(df, cfg, warm, trades, stop, n_variants, tf="1h", sims=200, seed=0):
    """Distribution of the BEST result across `n_variants` random strategies, on the SAME metric the
    sweep ranks by (monthly Sharpe) plus return. If the best real variant cannot beat this, the sweep
    found luck, not edge."""
    sub = df.iloc[warm:].reset_index(drop=True)
    hold = max(int(24 / (pd.Timedelta(rdata._rule(tf)) / pd.Timedelta("1h"))), 1)      # ~24h holding period at any bar size
    c = cfg.with_(stop_pct=stop, max_hold_bars=hold)
    pool = []
    for s in range(sims):
        r = bt.run(sub, st.random_entries(len(sub), 0, max(trades, 1), s), c)
        pool.append((r.metrics["total_return"], float(ev._monthly_sharpe(r))))
    rng = np.random.default_rng(seed)
    idx = rng.integers(0, len(pool), size=(sims, n_variants))
    best_ret = [max(pool[i][0] for i in row) for row in idx]
    best_sharpe = [max(pool[i][1] for i in row) for row in idx]
    return {"sharpe_p95": float(np.percentile(best_sharpe, 95)), "sharpe_median": float(np.median(best_sharpe)),
            "return_p95": float(np.percentile(best_ret, 95)), "return_median": float(np.median(best_ret))}


def run(symbol="BTCUSDT", loader=rdata.load, out_dir=ev.OUT_DIR, timeframes=TIMEFRAMES, horizons=HORIZON_HOURS) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    df1h_all = loader(symbol)
    results = []
    for tf in timeframes:
        df_all, cfg = prepare(df1h_all, tf)
        dev, hold, _ = ev.split(df_all)
        warm = st.warmup_bars() - 1
        if len(dev) < warm + 200:
            results.append({"variant": f"(skipped) {tf}", "timeframe": tf, "kind": "skipped",
                            "note": f"only {len(dev)} development bars, need {warm + 200}"})
            continue
        scores, rules = run_rules(dev, cfg, tf, warm)
        results.append(rules)
        for h in horizons:
            results.extend(run_ai(dev, cfg, tf, warm, scores, h))

    tested = [r for r in results if r["kind"] != "skipped"]
    ranked = sorted(tested, key=lambda r: r["oos_sharpe"], reverse=True)
    best = ranked[0] if ranked else None
    verdict, luck = "NO-GO", None
    if best:
        df_all, cfg = prepare(df1h_all, best["timeframe"])
        dev, _, _ = ev.split(df_all)
        luck = best_of_n_random(dev, cfg, st.warmup_bars() - 1, best["oos_trades"],
                                STOPS[best["timeframe"]][1], len(tested), tf=best["timeframe"])
        if best["oos_sharpe"] > luck["sharpe_p95"] and best["oos_return"] > luck["return_p95"]:
            verdict = f"CANDIDATE: {best['variant']} - re-validate it alone before trusting it"
    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "symbol": symbol,
           "variants_tested": len(tested), "results": results, "best": best,
           "luck_bar": luck, "verdict": verdict}
    json.dump(out, open(os.path.join(out_dir, "sweep_results.json"), "w", encoding="utf-8"), indent=2, default=str)
    open(os.path.join(out_dir, "SWEEP_REPORT.md"), "w", encoding="utf-8").write(report(out))
    return out


def report(r: dict) -> str:
    lines = [f"# Timeframe / horizon sweep — {r['symbol']}", "",
             f"{r['variants_tested']} variants, all out-of-sample and after costs "
             f"({ev.COSTS.fee_rate*100:.2f}% fee + {ev.COSTS.slippage_bps:.0f} bps slippage per side).", "",
             "| Variant | OOS return | Monthly Sharpe | Trades | AUC | Canary |", "|---|---|---|---|---|---|"]
    for x in sorted(r["results"], key=lambda z: z.get("oos_sharpe", -99), reverse=True):
        if x["kind"] == "skipped":
            lines.append(f"| {x['variant']} | – | – | – | – | {x['note']} |")
            continue
        auc = f"{x['auc']:.3f}" if "auc" in x else "–"
        can = f"{x['canary_auc']:.3f}" if "canary_auc" in x else "–"
        lines.append(f"| {x['variant']} | {ev.pct(x['oos_return'])} | {x['oos_sharpe']:.2f} | {x['oos_trades']} | {auc} | {can} |")
    if r["best"]:
        b, luck = r["best"], r["luck_bar"]
        lines += ["", "## Multiple-testing check", "",
                  f"Best variant: **{b['variant']}** - Sharpe {b['oos_sharpe']:.2f}, return {ev.pct(b['oos_return'])}.",
                  f"Trying {r['variants_tested']} variants means the best of them looks good by luck alone. The best of "
                  f"{r['variants_tested']} RANDOM strategies reaches Sharpe {luck['sharpe_median']:.2f} / "
                  f"{ev.pct(luck['return_median'])} typically, and Sharpe {luck['sharpe_p95']:.2f} / "
                  f"{ev.pct(luck['return_p95'])} at the 95th percentile.",
                  "", f"To count as a candidate the best variant must beat BOTH 95th-percentile bars "
                      f"(Sharpe {b['oos_sharpe']:.2f} vs {luck['sharpe_p95']:.2f}, "
                      f"return {ev.pct(b['oos_return'])} vs {ev.pct(luck['return_p95'])})."]
    lines += ["", f"**Verdict: {r['verdict']}**", "",
              "A CANDIDATE here is not a GO. It must be re-run on its own through `research.evaluate` / `research.ml`",
              "and pass those gates and the one-shot holdout before any forward test."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    ap.add_argument("--horizons", default=",".join(map(str, HORIZON_HOURS)))
    a = ap.parse_args()
    res = run(a.symbol.upper(), timeframes=a.timeframes.split(","), horizons=[int(h) for h in a.horizons.split(",")])
    print(open(os.path.join(ev.OUT_DIR, "SWEEP_REPORT.md"), encoding="utf-8").read())
