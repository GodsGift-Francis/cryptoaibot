"""Option 2: hold BTC, but step aside in sustained downtrends.

Five tests said BTC direction is not predictable from candles or order flow, and that trading costs
dominate any faint signal. So this stops trying to out-guess the market and targets something the
evidence supports: buy-and-hold made +95% with a -50% drawdown, and a strategy that keeps most of the
upside with materially less of the pain is both useful and achievable.

Rule (deliberately boring, few parameters, few trades):
    hold BTC while close >= MA * (1 - buffer); exit after `confirm` consecutive closes below it;
    re-enter after `confirm` consecutive closes back above. No stop-loss: the trend exit IS the risk control.

Judged against buy-and-hold on drawdown-adjusted terms, not on beating the market:
    capture (share of buy-and-hold return), drawdown reduction, Calmar, and switch count.
"""
from __future__ import annotations

import argparse
import itertools
import json
import os
from datetime import datetime, timezone

import numpy as np
import pandas as pd

from research import backtest as bt
from research import data as rdata
from research import evaluate as ev
from research import strategies as st
from research import sweep

GRID = {"ma": [50, 100, 150, 200], "confirm": [1, 2, 3], "buffer": [0.0, 0.01, 0.02]}
TIMEFRAMES = ["4h", "1d"]
GATES = {"min_capture": 0.70,          # keep >= 70% of buy-and-hold's return
         "max_dd_ratio": 0.60,         # cut drawdown to <= 60% of buy-and-hold's
         "beat_luck_bar": True,        # on Calmar, against the best of N random strategies
         "positive_under_2x_costs": True}
COSTS = ev.COSTS.with_(sizing="all_in", stop_pct=None)


def signals(df: pd.DataFrame, ma: int, confirm: int, buffer: float) -> np.ndarray:
    """+1 hold / -1 flat, with confirmation to avoid whipsawing on a single close."""
    close = df["close"].to_numpy(float)
    line = pd.Series(close).rolling(ma).mean().to_numpy()
    above = close >= line * (1 - buffer)
    below = close < line * (1 - buffer)
    run_up = pd.Series(above).rolling(confirm).sum().to_numpy() == confirm
    run_dn = pd.Series(below).rolling(confirm).sum().to_numpy() == confirm
    sig = np.zeros(len(df))
    state = 0
    for i in range(len(df)):
        if np.isnan(line[i]):
            continue
        if state <= 0 and run_up[i]:
            state, sig[i] = 1, 1
        elif state == 1 and run_dn[i]:
            state, sig[i] = -1, -1
    return sig


def run_variant(df, params, cfg, start, end=None, initial=None):
    end = len(df) if end is None else end
    sub = df.iloc[start:end].reset_index(drop=True)
    sig = signals(df, **params)[start:end]
    c = cfg.with_(**({"initial": initial} if initial is not None else {}))
    return bt.run(sub, sig, c)


def capture_ratio(strategy_return: float, hold_return: float) -> float:
    """Share of buy-and-hold's gain that the strategy kept. When holding LOSES money the ratio is
    meaningless, so score it by how much of the loss was avoided instead: >= 1 means it did at least
    as well as holding, which is exactly what drawdown control is supposed to achieve in a downtrend."""
    if hold_return > 0:
        return strategy_return / hold_return
    if hold_return < 0:
        return 1.0 + (strategy_return - hold_return) / abs(hold_return)
    return 1.0 if strategy_return >= 0 else 0.0


def compare(result: bt.BTResult, hold: bt.BTResult) -> dict:
    m, h = result.metrics, hold.metrics
    capture = capture_ratio(m["total_return"], h["total_return"])
    dd_ratio = m["max_drawdown"] / h["max_drawdown"] if h["max_drawdown"] < 0 else float("nan")
    return {**m, "capture": capture, "dd_ratio": dd_ratio,
            "hold_return": h["total_return"], "hold_max_drawdown": h["max_drawdown"], "hold_calmar": h["calmar"]}


def random_exposure_control(df, cfg, start, exposure: float, switches: int, sims=200, seed=0) -> dict:
    """The honest control for a risk overlay: strategies that are flat for the SAME fraction of time,
    with the SAME number of switches, but at RANDOM moments. If the rule cannot beat these, its exits
    are not timing anything - it is just holding less, which anyone can do by holding less."""
    sub = df.iloc[start:].reset_index(drop=True)
    n = len(sub)
    rng = np.random.default_rng(seed)
    calmars, returns, dds = [], [], []
    for _ in range(sims):
        sig = np.zeros(n)
        points = np.sort(rng.choice(np.arange(1, n - 1), size=min(max(switches, 2), n - 2), replace=False))
        state = 1
        sig[0] = 1
        for i in points:                      # alternate in/out at random times
            state = -state
            sig[i] = state
        r = bt.run(sub, sig, cfg)
        if abs(r.metrics["exposure"] - exposure) > 0.25:     # keep only comparable time-in-market
            continue
        calmars.append(r.metrics["calmar"])
        returns.append(r.metrics["total_return"])
        dds.append(r.metrics["max_drawdown"])
    if not calmars:
        return {"calmar_p95": float("inf"), "calmar_median": float("nan"), "return_p95": float("inf"),
                "dd_median": float("nan"), "samples": 0}
    return {"calmar_p95": float(np.percentile(calmars, 95)), "calmar_median": float(np.median(calmars)),
            "return_p95": float(np.percentile(returns, 95)), "dd_median": float(np.median(dds)),
            "samples": len(calmars)}


def select(df, cfg, start, end):
    """Pick by Calmar (return per unit of drawdown) - the metric this product is actually for."""
    best, best_key = None, None
    for ma, confirm, buffer in itertools.product(GRID["ma"], GRID["confirm"], GRID["buffer"]):
        p = {"ma": ma, "confirm": confirm, "buffer": buffer}
        m = run_variant(df, p, cfg, start, end).metrics
        if m["trades"] < 1:
            continue
        key = (m["calmar"], m["total_return"])
        if best_key is None or key > best_key:
            best, best_key = p, key
    return best or {"ma": 200, "confirm": 2, "buffer": 0.0}


def walk_forward(df, cfg, warm):
    months = ev.month_bounds(df)
    folds, equity, hold_equity = [], 1000.0, 1000.0
    for k in range(6, len(months)):                       # 6 months of training minimum
        _, te_start, te_end = months[k]
        te_start = max(te_start, warm)
        if months[k - 1][2] - warm < 30 or te_end - te_start < 2:
            continue
        p = select(df, cfg, warm, months[k - 1][2])
        r = run_variant(df, p, cfg, te_start, te_end, initial=equity)
        h = bt.run(df.iloc[te_start:te_end].reset_index(drop=True), st.buy_hold(df.iloc[te_start:te_end], 0),
                   cfg.with_(initial=hold_equity))
        folds.append({"month": months[k][0], "params": p, "return": r.metrics["total_return"],
                      "hold_return": h.metrics["total_return"], "trades": r.metrics["trades"]})
        equity, hold_equity = r.metrics["final_equity"], h.metrics["final_equity"]
    return folds, equity, hold_equity


def run(symbol="BTCUSDT", loader=rdata.load, out_dir=ev.OUT_DIR, timeframes=TIMEFRAMES, others=("ETHUSDT", "SOLUSDT")) -> dict:
    os.makedirs(out_dir, exist_ok=True)
    df1h = loader(symbol)
    variants = []
    for tf in timeframes:
        df_all, cfg = sweep.prepare(df1h, tf)
        cfg = cfg.with_(sizing="all_in", stop_pct=None)
        dev, _, _ = ev.split(df_all)
        warm = max(GRID["ma"]) + 5
        folds, eq, hold_eq = walk_forward(dev, cfg, warm)
        if not folds:
            continue
        oos_start = ev.month_bounds(dev)[6][1]
        equity = pd.Series([f["return"] for f in folds])
        chosen = select(dev, cfg, warm, len(dev))
        full = compare(run_variant(dev, chosen, cfg, warm), bt.run(dev.iloc[warm:].reset_index(drop=True),
                                                                   st.buy_hold(dev.iloc[warm:], 0), cfg))
        stressed = run_variant(dev, chosen, ev.STRESS.with_(sizing="all_in", stop_pct=None), warm)
        variants.append({
            "timeframe": tf, "chosen": chosen, "oos_return": eq / 1000 - 1, "oos_hold_return": hold_eq / 1000 - 1,
            "oos_capture": capture_ratio(eq / 1000 - 1, hold_eq / 1000 - 1),
            "oos_months": len(folds), "oos_trades": sum(f["trades"] for f in folds),
            "oos_monthly_sharpe": float(equity.mean() / equity.std(ddof=1) * np.sqrt(12)) if len(equity) > 1 and equity.std() else 0.0,
            "in_sample": full, "stressed_return": stressed.metrics["total_return"], "folds": folds,
            "oos_start_index": oos_start,
        })

    best = max(variants, key=lambda v: v["in_sample"]["calmar"]) if variants else None
    luck = gates = None
    if best:
        df_all, cfg = sweep.prepare(df1h, best["timeframe"])
        cfg = cfg.with_(sizing="all_in", stop_pct=None)
        dev, _, _ = ev.split(df_all)
        ins = best["in_sample"]
        luck = random_exposure_control(dev, cfg, max(GRID["ma"]) + 5, ins["exposure"], max(ins["trades"], 2))
        gates = {
            "keeps_enough_upside": bool(ins["capture"] >= GATES["min_capture"]),
            "cuts_drawdown": bool(ins["dd_ratio"] <= GATES["max_dd_ratio"]),
            "better_calmar_than_holding": bool(ins["calmar"] > ins["hold_calmar"]),
            "oos_capture_holds_up": bool(best["oos_capture"] >= GATES["min_capture"]),
            "exit_timing_beats_random": bool(ins["calmar"] > luck["calmar_p95"]),
            "positive_under_2x_costs": bool(best["stressed_return"] > 0),
        }
    cross = {}
    for sym in others:
        try:
            other = loader(sym)
        except (FileNotFoundError, OSError):
            cross[sym] = None
            continue
        if not best:
            break
        df_all, cfg = sweep.prepare(other, best["timeframe"])
        cfg = cfg.with_(sizing="all_in", stop_pct=None)
        dev, _, _ = ev.split(df_all)
        warm = max(GRID["ma"]) + 5
        cross[sym] = compare(run_variant(dev, best["chosen"], cfg, warm),
                             bt.run(dev.iloc[warm:].reset_index(drop=True), st.buy_hold(dev.iloc[warm:], 0), cfg))

    out = {"generated_at": datetime.now(timezone.utc).isoformat(), "symbol": symbol, "variants": variants,
           "best": best, "luck_bar": luck, "gates": gates, "cross_asset": cross,
           "verdict": ("GO: run the one-shot holdout" if gates and all(gates.values()) else "NO-GO")}
    json.dump(out, open(os.path.join(out_dir, "overlay_results.json"), "w", encoding="utf-8"), indent=2, default=str)
    open(os.path.join(out_dir, "OVERLAY_REPORT.md"), "w", encoding="utf-8").write(report(out))
    return out


def report(r: dict) -> str:
    lines = [f"# Drawdown-controlled BTC exposure — {r['symbol']}", "",
             "Goal is NOT to beat buy-and-hold. It is to keep most of the upside with materially less drawdown.",
             f"Costs: {ev.COSTS.fee_rate*100:.2f}% fee + {ev.COSTS.slippage_bps:.0f} bps slippage per side.", ""]
    if not r["variants"]:
        return "\n".join(lines + ["No variant produced enough folds to evaluate.", "", "**Verdict: NO-GO**", ""])
    lines += ["| Timeframe | Rule | In-sample return | vs hold | Max DD | vs hold | Calmar | Hold Calmar | Trades |",
              "|---|---|---|---|---|---|---|---|---|"]
    for v in r["variants"]:
        i, c = v["in_sample"], v["chosen"]
        lines.append(f"| {v['timeframe']} | MA{c['ma']}, confirm {c['confirm']}, buffer {c['buffer']*100:.0f}% | "
                     f"{ev.pct(i['total_return'])} | {ev.pct(i['hold_return'])} | {ev.pct(i['max_drawdown'])} | "
                     f"{ev.pct(i['hold_max_drawdown'])} | {i['calmar']:.2f} | {i['hold_calmar']:.2f} | {i['trades']} |")
    b = r["best"]
    lines += ["", "## Walk-forward (out-of-sample, parameters re-chosen every month)", "",
              f"Best timeframe **{b['timeframe']}** over {b['oos_months']} months: strategy "
              f"{ev.pct(b['oos_return'])} vs buy-and-hold {ev.pct(b['oos_hold_return'])} "
              f"(capture {b['oos_capture']*100:.0f}%), {b['oos_trades']} switches.",
              f"Under 2x costs: {ev.pct(b['stressed_return'])}."]
    if r["luck_bar"] and r["luck_bar"]["samples"]:
        lk = r["luck_bar"]
        lines += ["", "## Does the exit timing matter?", "",
                  f"Control: {lk['samples']} strategies flat for the same share of time, same number of switches, at "
                  f"RANDOM moments. They reach Calmar {lk['calmar_median']:.2f} typically and {lk['calmar_p95']:.2f} at "
                  f"the 95th percentile, with a typical drawdown of {ev.pct(lk['dd_median'])}. The rule scores "
                  f"{b['in_sample']['calmar']:.2f} with {ev.pct(b['in_sample']['max_drawdown'])}. If it cannot beat the "
                  "95th percentile, the exits are not timing anything - the same result comes from simply holding less."]
    if r["cross_asset"]:
        lines += ["", "## Same rule, other assets (in-sample)", "",
                  "| Asset | Return | vs hold | Max DD | vs hold |", "|---|---|---|---|---|"]
        for sym, m in r["cross_asset"].items():
            lines.append(f"| {sym} | – | – | – | – |" if not m else
                         f"| {sym} | {ev.pct(m['total_return'])} | {ev.pct(m['hold_return'])} | "
                         f"{ev.pct(m['max_drawdown'])} | {ev.pct(m['hold_max_drawdown'])} |")
    if r["gates"]:
        lines += ["", "## Gates", ""] + [f"- {'PASS' if v else 'FAIL'} — {k}" for k, v in r["gates"].items()]
    lines += ["", f"**Verdict: {r['verdict']}**", "",
              "A GO earns the one-shot holdout, then a PAPER/TESTNET forward test. Not live money."]
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbol", default="BTCUSDT")
    ap.add_argument("--timeframes", default=",".join(TIMEFRAMES))
    a = ap.parse_args()
    run(a.symbol.upper(), timeframes=a.timeframes.split(","))
    print(open(os.path.join(ev.OUT_DIR, "OVERLAY_REPORT.md"), encoding="utf-8").read())
