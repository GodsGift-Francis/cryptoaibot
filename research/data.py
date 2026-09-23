"""Sprint 0 - reproducible market data from Binance's public archive (data.binance.vision).

* last N complete calendar months of spot klines (default 12, 1h)
* every zip verified against Binance's published SHA-256 .CHECKSUM file
* timestamps normalised to UTC (archive switched spot timestamps to microseconds in 2025)
* validated: no duplicates, no gaps, sane OHLC; a manifest records hashes for reproducibility

    python -m research.data --symbols BTCUSDT,ETHUSDT,SOLUSDT --months 12
"""
from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import urllib.request
import zipfile
from datetime import date, datetime, timezone

import numpy as np
import pandas as pd

BASE = "https://data.binance.vision/data/spot/monthly/klines"
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
COLS = ["open_time", "open", "high", "low", "close", "volume", "close_time", "quote_volume",
        "trades", "taker_base", "taker_quote", "ignore"]


def last_complete_months(n: int, today: date | None = None) -> list[str]:
    today = today or datetime.now(timezone.utc).date()
    y, m = today.year, today.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y:04d}-{m:02d}")
    return sorted(out)


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": "crypto-bot-research/1.1"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def to_utc(ts: pd.Series) -> pd.Series:
    """Archive timestamps are ms before 2025 and us from 2025 on; detect per value."""
    v = ts.astype("int64")
    us = v > 10 ** 14
    ms = np.where(us, v // 1000, v)
    return pd.to_datetime(ms, unit="ms", utc=True)


def parse_klines_csv(raw: bytes) -> pd.DataFrame:
    df = pd.read_csv(io.BytesIO(raw), header=None)
    if not str(df.iloc[0, 0]).lstrip("-").isdigit():      # some archive files carry a header row
        df = df.iloc[1:]
    df = df.iloc[:, :12]
    df.columns = COLS
    out = pd.DataFrame({"timestamp": to_utc(df["open_time"])})
    for c in ("open", "high", "low", "close", "volume"):
        out[c] = df[c].astype(float).values
    return out


def fetch_month(symbol: str, interval: str, month: str, fetch=_get) -> tuple[pd.DataFrame, str]:
    name = f"{symbol}-{interval}-{month}.zip"
    url = f"{BASE}/{symbol}/{interval}/{name}"
    blob = fetch(url)
    expected = fetch(url + ".CHECKSUM").decode().split()[0].strip().lower()
    actual = hashlib.sha256(blob).hexdigest()
    if actual != expected:
        raise ValueError(f"checksum mismatch for {name}: {actual} != {expected}")
    with zipfile.ZipFile(io.BytesIO(blob)) as z:
        return parse_klines_csv(z.read(z.namelist()[0])), actual


def validate(df: pd.DataFrame, interval_seconds: int) -> list[str]:
    problems = []
    if df["timestamp"].duplicated().any():
        problems.append(f"{int(df['timestamp'].duplicated().sum())} duplicate candles")
    step = df["timestamp"].diff().dt.total_seconds().dropna()
    gaps = step[step != interval_seconds]
    if len(gaps):
        problems.append(f"{len(gaps)} gaps/irregular steps (largest {gaps.max() / 3600:.1f}h)")
    bad = (df["high"] < df[["open", "close"]].max(axis=1)) | (df["low"] > df[["open", "close"]].min(axis=1)) | (df["low"] <= 0)
    if bad.any():
        problems.append(f"{int(bad.sum())} candles with inconsistent OHLC")
    return problems


def download(symbol: str, months: int = 12, interval: str = "1h", out_dir: str = DATA_DIR, fetch=_get,
             today: date | None = None) -> str:
    os.makedirs(out_dir, exist_ok=True)
    frames, manifest = [], {"symbol": symbol, "interval": interval, "source": BASE, "files": {}}
    for month in last_complete_months(months, today):
        df, sha = fetch_month(symbol, interval, month, fetch)
        frames.append(df)
        manifest["files"][month] = sha
        print(f"  {symbol} {month}: {len(df)} candles, sha256 ok")
    data = pd.concat(frames).drop_duplicates("timestamp").sort_values("timestamp").reset_index(drop=True)
    problems = validate(data, 3600 if interval == "1h" else int(pd.Timedelta(interval).total_seconds()))
    manifest.update({"candles": len(data), "first": str(data["timestamp"].iloc[0]), "last": str(data["timestamp"].iloc[-1]),
                     "validation": problems or ["ok"],
                     "sha256_csv": None})
    path = os.path.join(out_dir, f"{symbol}-{interval}.csv")
    data.to_csv(path, index=False)
    manifest["sha256_csv"] = hashlib.sha256(open(path, "rb").read()).hexdigest()
    json.dump(manifest, open(os.path.join(out_dir, f"{symbol}-{interval}.manifest.json"), "w"), indent=2)
    print(f"  -> {path}: {len(data)} candles {manifest['first']} .. {manifest['last']}; validation: {manifest['validation']}")
    return path


def load(symbol: str, interval: str = "1h", data_dir: str = DATA_DIR) -> pd.DataFrame:
    df = pd.read_csv(os.path.join(data_dir, f"{symbol}-{interval}.csv"))
    df["timestamp"] = pd.to_datetime(df["timestamp"], utc=True)
    return df


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--symbols", default="BTCUSDT,ETHUSDT,SOLUSDT")
    ap.add_argument("--months", type=int, default=12)
    a = ap.parse_args()
    for s in a.symbols.split(","):
        download(s.strip().upper(), a.months)
