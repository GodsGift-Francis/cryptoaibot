"""One command for sprints 0-4:  python -m research  [--symbols BTCUSDT,ETHUSDT,SOLUSDT] [--months 12]

Downloads + verifies data, runs the development evaluation, writes research/out/REPORT.md.
The holdout stays untouched; run `python -m research.evaluate --holdout` once, only if the verdict is GO."""
import argparse
import os

from research import data, evaluate

ap = argparse.ArgumentParser()
ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
ap.add_argument("--months", type=int, default=12)
ap.add_argument("--skip-download", action="store_true")
a = ap.parse_args()
syms = tuple(s.strip().upper() for s in a.symbols.split(","))
if not a.skip_download:
    for s in syms:
        data.download(s, a.months)
evaluate.evaluate(syms)
print(open(os.path.join(evaluate.OUT_DIR, "REPORT.md")).read())
