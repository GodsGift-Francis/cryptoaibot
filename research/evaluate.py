"""Sprints 2-4: honest measurement, walk-forward tuning, robustness, one-shot holdout.

    python -m research.evaluate                 # sprints 2-4 on the development months (holdout untouched)
    python -m research.evaluate --holdout       # sprint 4 final step: ONE run on the held-back months

Gates are fixed here, before any real data is seen. A config is only recommended if it passes all of them.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import os
from dataclasses import asdict
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research import backtest as bt
from research import data as rdata
from research import strategies as st

OUT_DIR = os.path.join(os.path.dirname(__file__), "out")
HOLDOUT_MONTHS = 3
GRID = {"buy": [1.5, 2.0, 2.5, 3.0, 3.5], "sell": [-1.5, -2.0, -2.5, -3.0, -3.5], "stop": [2.0, 3.0, 4.0, 5.0, 6.0]}
V1_DEFAULT = {"buy": 2.5, "sell": -2.5, "stop": 3.0}
COSTS = bt.BTConfig()                          # 0.1% fee + 5 bps slippage per side
STRESS = COSTS.with_(fee_rate=0.002, slippage_bps=10)
GATES = {
    "min_oos_trades": 20,                     # 1 year of data: anything less is noise
    "oos_positive_after_costs": True,
    "oos_sharpe_beats_best_competitor": True,
    "beats_random_percentile": 95,            # total return vs random entries with the same exits
    "positive_under_2x_costs": True,
    "neighbour_stability_min": 0.6,           # share of adjacent grid configs profitable on dev
}


# ------------------------------------------------------------------ splitting
def split(df: pd.DataFrame, holdout_months: int = HOLDOUT_MONTHS):
    months = df["timestamp"].dt.strftime("%Y-%m")
    uniq = sorted(months.unique())
    hold = set(uniq[-holdout_months:])
    dev_mask = ~months.isin(hold)
    return df[dev_mask].reset_index(drop=True), df[~dev_mask].reset_index(drop=True), uniq


def month_bounds(df: pd.DataFrame) -> list[tuple[str, int, int]]:
    months = df["timestamp"].dt.strftime("%Y-%m").to_numpy()
    out, start = [], 0
    for i in range(1, len(months) + 1):
        if i == len(months) or months[i] != months[start]:
            out.append((months[start], start, i))
            start = i
    return out


# ------------------------------------------------------------------ building blocks
def v1_run(df, scores, p, cfg=COSTS, start=0, end=None, initial=None):
    end = len(df) if end is None else end
    sub = df.iloc[start:end].reset_index(drop=True)
    sig = st.thresholds(scores[start:end], p["buy"], p["sell"])
    c = cfg.with_(stop_pct=p["stop"], **({"initial": initial} if initial is not None else {}))
    return bt.run(sub, sig, c)


def competitors(df, start):
    all_in = COSTS.with_(sizing="all_in", stop_pct=None)
    sub = df.iloc[start:].reset_index(drop=True)
    return {
        "buy_hold": bt.run(sub, st.buy_hold(sub, 0), all_in),
        "dca_weekly": bt.run_dca(sub, COSTS),
        "sma_cross_50_200": bt.run(sub, st.sma_cross(df, start)[start:], all_in),
        "rsi_mean_reversion": bt.run(sub, st.rsi_mr(df, start)[start:], COSTS.with_(sizing="all_in", stop_pct=5.0)),
        "donchian_breakout": bt.run(sub, st.donchian(df, start)[start:], all_in),
    }


def random_benchmark(df, start, n_entries, hold_bars, p, seeds=300):
    sub = df.iloc[start:].reset_index(drop=True)
    cfg = COSTS.with_(stop_pct=p["stop"], max_hold_bars=max(int(hold_bars), 1))
    return np.array([bt.run(sub, st.random_entries(len(sub), 0, max(n_entries, 1), s), cfg).metrics["total_return"]
                     for s in range(seeds)])


def select(df, scores, start, end, min_trades=3):
    best, best_key = None, None
    for b, s, stop in itertools.product(GRID["buy"], GRID["sell"], GRID["stop"]):
        p = {"buy": b, "sell": s, "stop": stop}
        m = v1_run(df, scores, p, start=start, end=end).metrics
        if m["trades"] < min_trades:
            continue
        key = (m["sharpe"], m["total_return"])
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best or dict(V1_DEFAULT)


def walk_forward(df, scores, warm):
    """3-month train / 1-month test, rolling monthly. OOS months are compounded."""
    months = month_bounds(df)
    folds, equity, trades = [], 1000.0, 0
    oos_start = df["timestamp"].iloc[months[3][1]] if len(months) > 3 else df["timestamp"].iloc[-1]
    for k in range(3, len(months)):
        tr_start = max(months[k - 3][1], warm)
        te_name, te_start, te_end = months[k]
        p = select(df, scores, tr_start, months[k - 1][2])
        r = v1_run(df, scores, p, start=max(te_start, warm), end=te_end, initial=equity)
        d = v1_run(df, scores, V1_DEFAULT, start=max(te_start, warm), end=te_end, initial=1000.0)
        folds.append({"test_month": te_name, "params": p, **{k2: r.metrics[k2] for k2 in ("total_return", "max_drawdown", "trades")},
                      "v1_default_return": d.metrics["total_return"]})
        equity = r.metrics["final_equity"]
        trades += r.metrics["trades"]
    rets = [f["total_return"] for f in folds]
    sharpe = float(np.mean(rets) / np.std(rets, ddof=1) * np.sqrt(12)) if len(rets) > 1 and np.std(rets) > 0 else 0.0
    return folds, {"oos_start": oos_start, "oos_total_return": equity / 1000 - 1, "oos_trades": trades, "oos_monthly_sharpe": sharpe,
                   "oos_worst_month": min(rets) if rets else 0.0}


def neighbour_stability(df, scores, p, start):
    def steps(key):
        vals = GRID[key]
        i = vals.index(p[key])
        return [vals[j] for j in (i - 1, i, i + 1) if 0 <= j < len(vals)]
    neigh = [dict(buy=b, sell=s, stop=x) for b, s, x in itertools.product(steps("buy"), steps("sell"), steps("stop")) if (b, s, x) != (p["buy"], p["sell"], p["stop"])]
    ok = [v1_run(df, scores, q, start=start).metrics["total_return"] > 0 for q in neigh]
    return float(np.mean(ok)) if ok else 0.0


def monte_carlo_dd(trade_returns, n=5000, seed=0):
    if len(trade_returns) < 2:
        return 0.0
    rng = np.random.default_rng(seed)
    worst = []
    for _ in range(n):
        eq = np.cumprod(1 + rng.permutation(trade_returns))
        peak = np.maximum.accumulate(np.concatenate([[1.0], eq]))
        worst.append(((np.concatenate([[1.0], eq]) - peak) / peak).min())
    return float(np.percentile(worst, 5))


def monthly_returns(equity: pd.Series, since=None) -> pd.Series:
    """Month-by-month returns. The first month in the window is measured from the last value BEFORE
    the window, so a window of N months always yields N returns (same count as the walk-forward)."""
    m = equity.resample("ME").last().pct_change()
    if since is None:
        return m.dropna()
    first = equity[equity.index < since]
    if len(first):
        m = m[m.index >= since]
        m.iloc[0] = equity[(equity.index >= since) & (equity.index <= m.index[0])].iloc[-1] / first.iloc[-1] - 1
    return m[m.index >= since].dropna()


def _monthly_sharpe(res: bt.BTResult, since=None) -> float:
    m = monthly_returns(res.equity, since)
    return float(m.mean() / m.std(ddof=1) * np.sqrt(12)) if len(m) > 1 and m.std() > 0 else 0.0


# ------------------------------------------------------------------ sprints 2-4 (development data only)
def evaluate(symbols=("BTCUSDT", "ETHUSDT", "SOLUSDT"), loader=rdata.load, out_dir=OUT_DIR, seeds=300) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    main = symbols[0]
    df_all = loader(main)
    dev, hold, months = split(df_all)
    warm = st.warmup_bars() - 1
    scores = st.v1_scores(df_all)[: len(dev)]         # causal: dev scores never see holdout candles

    # Sprint 2 - measure V1 honestly against competitors and luck
    v1 = v1_run(dev, scores, V1_DEFAULT, start=warm)
    comp = competitors(dev, warm)
    hold_bars = float(v1.trades["bars"].median()) if len(v1.trades) else 24
    rnd = random_benchmark(dev, warm, v1.metrics["trades"], hold_bars, V1_DEFAULT, seeds)
    v1_pct = float((rnd < v1.metrics["total_return"]).mean() * 100)

    # Sprint 3 - walk-forward tuning
    folds, wf = walk_forward(dev, scores, warm)
    chosen = select(dev, scores, warm, len(dev))
    tuned = v1_run(dev, scores, chosen, start=warm)
    rnd_t = random_benchmark(dev, warm, tuned.metrics["trades"],
                             float(tuned.trades["bars"].median()) if len(tuned.trades) else 24, chosen, seeds)
    tuned_pct = float((rnd_t < tuned.metrics["total_return"]).mean() * 100)

    # Sprint 4 - robustness
    stressed = v1_run(dev, scores, chosen, cfg=STRESS, start=warm)
    stability = neighbour_stability(dev, scores, chosen, warm)
    mc_dd = monte_carlo_dd(tuned.trades["return"].to_numpy()) if len(tuned.trades) else 0.0
    others = {}
    for sym in symbols[1:]:
        try:
            o_dev, _, _ = split(loader(sym))
            o_scores = st.v1_scores(o_dev)
            others[sym] = {"chosen": v1_run(o_dev, o_scores, chosen, start=warm).metrics,
                           "buy_hold": competitors(o_dev, warm)["buy_hold"].metrics}
        except FileNotFoundError:
            others[sym] = None

    # competitors scored over the SAME out-of-sample window as the walk-forward
    oos_start = wf["oos_start"]
    comp_oos = {k: {"monthly_sharpe": _monthly_sharpe(v, oos_start),
                    "return": float(v.equity[v.equity.index >= oos_start].iloc[-1] /
                                     v.equity[v.equity.index < oos_start].iloc[-1] - 1)} for k, v in comp.items()}
    best_comp_name = max(comp_oos, key=lambda k: comp_oos[k]["monthly_sharpe"])
    best_comp = comp_oos[best_comp_name]["monthly_sharpe"]
    full_alloc = v1_run(dev, scores, chosen, cfg=COSTS.with_(sizing="all_in"), start=warm)
    gates = {
        "min_oos_trades": wf["oos_trades"] >= GATES["min_oos_trades"],
        "oos_positive_after_costs": wf["oos_total_return"] > 0,
        "oos_sharpe_beats_best_competitor": wf["oos_monthly_sharpe"] > best_comp,
        "beats_random_percentile": tuned_pct >= GATES["beats_random_percentile"],
        "positive_under_2x_costs": stressed.metrics["total_return"] > 0,
        "neighbour_stability": stability >= GATES["neighbour_stability_min"],
    }
    gates = {k: bool(v) for k, v in gates.items()}
    result = {
        "generated_at": datetime.now(timezone.utc).isoformat(), "symbol": main,
        "dev_period": [str(dev.timestamp.iloc[0]), str(dev.timestamp.iloc[-1])],
        "holdout_period": [str(hold.timestamp.iloc[0]), str(hold.timestamp.iloc[-1])] if len(hold) else None,
        "costs": asdict(COSTS), "v1_default": v1.metrics, "v1_default_random_percentile": v1_pct,
        "competitors": {k: {**v.metrics, "monthly_sharpe": _monthly_sharpe(v)} for k, v in comp.items()},
        "walk_forward": {"summary": wf, "folds": folds, "grid_size": int(np.prod([len(v) for v in GRID.values()]))},
        "chosen_params": chosen, "chosen_dev": tuned.metrics, "chosen_random_percentile": tuned_pct,
        "chosen_2x_costs": stressed.metrics, "neighbour_stability": stability, "monte_carlo_dd_p5": mc_dd,
        "other_symbols": others, "best_competitor": {"name": best_comp_name, "monthly_sharpe": best_comp},
        "competitors_oos": comp_oos, "chosen_full_allocation": full_alloc.metrics,
        "gates": gates, "verdict": "GO: run the one-shot holdout" if all(gates.values()) else "NO-GO",
    }
    json.dump(result, open(os.path.join(out_dir, "dev_results.json"), "w", encoding="utf-8"), indent=2, default=str)
    open(os.path.join(out_dir, "REPORT.md"), "w", encoding="utf-8").write(report(result))
    return result


# ------------------------------------------------------------------ one-shot holdout
def holdout(symbol="BTCUSDT", loader=rdata.load, out_dir=OUT_DIR, force=False) -> dict:
    dev_path = os.path.join(out_dir, "dev_results.json")
    lock = os.path.join(out_dir, "HOLDOUT_LOCK.json")
    if not os.path.exists(dev_path):
        raise SystemExit("run `python -m research.evaluate` first")
    dev_res = json.load(open(dev_path, encoding="utf-8"))
    if os.path.exists(lock) and not force:
        raise SystemExit(f"holdout already used ({json.load(open(lock, encoding='utf-8'))['used_at']}). Re-running it on tuned params "
                         "turns it into training data. Pass --force only if you accept that; it is recorded.")
    df_all = loader(symbol)
    dev, hold, _ = split(df_all)
    scores = st.v1_scores(df_all)[len(dev):]
    p = dev_res["chosen_params"]
    r = v1_run(hold, scores, p)
    bh = bt.run(hold, st.buy_hold(hold, 0), COSTS.with_(sizing="all_in", stop_pct=None))
    worst_dev_dd = dev_res["chosen_dev"]["max_drawdown"]
    passed = r.metrics["total_return"] > 0 and r.metrics["max_drawdown"] >= 1.5 * worst_dev_dd
    res = {"used_at": datetime.now(timezone.utc).isoformat(), "params": p, "forced": bool(force),
           "params_hash": hashlib.sha256(json.dumps(p, sort_keys=True).encode()).hexdigest()[:12],
           "holdout": r.metrics, "buy_hold": bh.metrics, "passed": passed,
           "verdict": "GO: forward-test in PAPER/TESTNET" if passed and dev_res["verdict"].startswith("GO") else "NO-GO"}
    json.dump(res, open(lock, "w", encoding="utf-8"), indent=2, default=str)
    with open(os.path.join(out_dir, "REPORT.md"), "a", encoding="utf-8") as f:
        f.write(f"\n## Holdout (one shot, {res['used_at'][:10]}{' FORCED' if force else ''})\n\n"
                f"| | Chosen config | Buy & hold |\n|---|---|---|\n"
                f"| Return | {pct(r.metrics['total_return'])} | {pct(bh.metrics['total_return'])} |\n"
                f"| Max drawdown | {pct(r.metrics['max_drawdown'])} | {pct(bh.metrics['max_drawdown'])} |\n"
                f"| Trades | {r.metrics['trades']} | 1 |\n\n**Final verdict: {res['verdict']}**\n")
    return res


# ------------------------------------------------------------------ report
def pct(x):
    return f"{x * 100:+.1f}%"


def report(r: dict) -> str:
    rows = [("V1 default (2.5/-2.5, 3% stop)", r["v1_default"], None)]
    rows += [(k, v, v["monthly_sharpe"]) for k, v in r["competitors"].items()]
    rows.append((f"V1 tuned {r['chosen_params']} (in-sample)", r["chosen_dev"], None))
    rows.append(("V1 tuned, full allocation (in-sample)", r["chosen_full_allocation"], None))
    lines = [f"# Algorithm evaluation — {r['symbol']}", "",
             f"Development period {r['dev_period'][0][:10]} → {r['dev_period'][1][:10]}; holdout "
             f"{(r['holdout_period'] or ['-','-'])[0][:10]} → {(r['holdout_period'] or ['-','-'])[1][:10]} (untouched).",
             f"Costs: {r['costs']['fee_rate']*100:.2f}% fee + {r['costs']['slippage_bps']:.0f} bps slippage per side. "
             "BTC dominance / sentiment excluded (no reliable history).", "",
             "## Scorecard (development data)", "",
             "| Strategy | Return | Max DD | Sharpe | Calmar | Trades | Win rate | Exposure |",
             "|---|---|---|---|---|---|---|---|"]
    for name, m, _ in rows:
        lines.append(f"| {name} | {pct(m['total_return'])} | {pct(m['max_drawdown'])} | {m['sharpe']:.2f} | "
                     f"{m['calmar']:.2f} | {m['trades']} | {m['win_rate']*100:.0f}% | {m['exposure']*100:.0f}% |")
    wf = r["walk_forward"]["summary"]
    lines += ["", f"V1 default beats {r['v1_default_random_percentile']:.0f}% of random-entry runs; tuned config beats "
              f"{r['chosen_random_percentile']:.0f}%.", "",
              f"## Walk-forward (out-of-sample, {r['walk_forward']['grid_size']} configs searched per fold)", "",
              f"Compounded OOS return **{pct(wf['oos_total_return'])}**, {wf['oos_trades']} trades, monthly Sharpe "
              f"{wf['oos_monthly_sharpe']:.2f}, worst month {pct(wf['oos_worst_month'])}. Best competitor: "
              f"{r['best_competitor']['name']} (monthly Sharpe {r['best_competitor']['monthly_sharpe']:.2f}).", "",
              "Competitors over the same out-of-sample months: " + ", ".join(
                  f"{k} {pct(v['return'])} (Sharpe {v['monthly_sharpe']:.2f})" for k, v in r["competitors_oos"].items()) + ".", "",
              "| Test month | Params | OOS return | V1 default | Trades |", "|---|---|---|---|---|"]
    for f in r["walk_forward"]["folds"]:
        lines.append(f"| {f['test_month']} | {f['params']['buy']}/{f['params']['sell']}/{f['params']['stop']}% | "
                     f"{pct(f['total_return'])} | {pct(f['v1_default_return'])} | {f['trades']} |")
    lines += ["", "## Robustness", "",
              f"- 2× costs: {pct(r['chosen_2x_costs']['total_return'])}",
              f"- Neighbouring configs profitable: {r['neighbour_stability']*100:.0f}%",
              f"- Monte Carlo 5th-percentile drawdown: {pct(r['monte_carlo_dd_p5'])}"]
    for sym, o in r["other_symbols"].items():
        lines.append(f"- {sym}: " + ("no data" if o is None else
                     f"{pct(o['chosen']['total_return'])} vs buy & hold {pct(o['buy_hold']['total_return'])}"))
    lines += ["", "## Gates", ""] + [f"- {'PASS' if v else 'FAIL'} — {k}" for k, v in r["gates"].items()]
    lines += ["", f"**Verdict: {r['verdict']}**", "",
              "One year of data: treat every number above as provisional. A GO here earns a forward test, not live money."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--holdout", action="store_true")
    ap.add_argument("--force", action="store_true")
    a = ap.parse_args()
    syms = tuple(s.strip().upper() for s in a.symbols.split(","))
    if a.holdout:
        print(json.dumps(holdout(syms[0], force=a.force), indent=2, default=str))
    else:
        res = evaluate(syms)
        print(open(os.path.join(OUT_DIR, "REPORT.md"), encoding="utf-8").read())
