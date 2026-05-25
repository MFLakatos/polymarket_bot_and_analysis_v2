"""
BTC 5-Minute Reversal Dataset Builder
======================================
Splits BTC 1-second price history into non-overlapping 5-minute (300 s) windows.

For each second t in [1, 298] within a window:
  - delta_usd       = price[t] - price[0]   ← signed move from window start
  - remaining_sec   = 300 - t               ← seconds left in window
  - flip            = 1 if the delta sign ever reverses at any second in [t+1, 299]

Persists the resulting dataset to data/crypto/BTC/reversal_dataset.parquet.
This file is consumed by reversal_model.py to build the fast query model.
"""
from __future__ import annotations

import time
from pathlib import Path

import numpy as np
import pandas as pd
import requests

# ── Settings ──────────────────────────────────────────────────────────────────
SYMBOL         = "BTCUSDT"
WINDOW_SECONDS = 300          # 5 minutes
DEFAULT_HOURS  = 10_000       # ~1.1 years of 1-second data
DEFAULT_OUTPUT = "data/crypto/BTC/reversal_dataset.parquet"
CACHE_FILE_1S  = "data/crypto/BTC/btc_1s.parquet"


# ── Binance 1s downloader ─────────────────────────────────────────────────────

def download_1s_prices(hours: int = DEFAULT_HOURS, limit: int = 1000) -> pd.DataFrame:
    """Download 1-second BTC close prices from Binance. Pages backwards."""
    total_seconds = hours * 3_600
    pages_needed  = total_seconds // limit + 2
    url           = "https://api.binance.com/api/v3/klines"
    frames: list[pd.DataFrame] = []
    end_time: int | None = None

    print(f"Downloading ~{hours}h of 1s BTC data (~{total_seconds:,} rows, up to {pages_needed} pages)...")

    for page in range(pages_needed):
        params: dict = {"symbol": SYMBOL, "interval": "1s", "limit": limit}
        if end_time is not None:
            params["endTime"] = end_time
        try:
            resp = requests.get(url, params=params, timeout=15)
            resp.raise_for_status()
            raw = resp.json()
        except requests.RequestException as exc:
            print(f"  Page {page}: network error – {exc}. Stopping early.")
            break
        if not raw:
            break

        df = pd.DataFrame(raw, columns=[
            "open_time", "open", "high", "low", "close", "volume",
            "close_time", "qav", "trades", "tb_base", "tb_quote", "ignore",
        ])
        df["close"]     = df["close"].astype(float)
        df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
        frames.append(df[["open_time", "close"]])
        end_time = int(df["open_time"].iloc[0].timestamp() * 1_000) - 1

        if (page + 1) % 20 == 0:
            print(f"  {page + 1}/{pages_needed} pages ({sum(len(f) for f in frames):,} rows)...")
        time.sleep(0.08)

    if not frames:
        raise RuntimeError("No data downloaded.")

    result = (
        pd.concat(frames)
        .drop_duplicates("open_time")
        .sort_values("open_time")
        .reset_index(drop=True)
    )
    print(f"Downloaded {len(result):,} rows ({result['open_time'].iloc[0]} → {result['open_time'].iloc[-1]})")
    return result


def load_or_download_1s(hours: int = DEFAULT_HOURS) -> pd.Series:
    """Load 1s prices from cache, or download and cache."""
    cache = Path(CACHE_FILE_1S)
    if cache.exists():
        print(f"Loading 1s cache from {cache}...")
        price = pd.read_parquet(cache)["close"]
    else:
        raw_df = download_1s_prices(hours=hours)
        price = (
            raw_df.set_index("open_time")["close"]
            .sort_index()
            .resample("1s").last()
            .ffill()
        )
        cache.parent.mkdir(parents=True, exist_ok=True)
        price.to_frame("close").to_parquet(cache)
        print(f"Cached {len(price):,} 1s rows → {cache}")
    return price


# ── Dataset builder ───────────────────────────────────────────────────────────

def build_reversal_dataset(price: pd.Series, window_seconds: int = WINDOW_SECONDS) -> pd.DataFrame:
    """
    Split price series into non-overlapping windows and label each second.

    Returns DataFrame with columns:
      delta_usd        – signed USD move from window start
      remaining_seconds – seconds left in window
      flip             – 1 if direction reverses before window ends
    """
    prices = price.values
    n_windows = len(prices) // window_seconds
    windows = [
        prices[s : s + window_seconds]
        for s in range(0, n_windows * window_seconds, window_seconds)
    ]
    print(f"Built {len(windows):,} complete {window_seconds}s windows from {len(prices):,} seconds.")

    records: list[dict] = []
    for seg in windows:
        p0 = seg[0]
        for t in range(1, window_seconds - 1):   # t = 1 … 298
            delta = seg[t] - p0
            if delta == 0.0:
                continue
            sign_now      = np.sign(delta)
            future_deltas = seg[t + 1:] - p0
            future_signs  = np.sign(future_deltas)
            records.append({
                "delta_usd":         delta,
                "remaining_seconds": window_seconds - t,
                "flip":              int(np.any(future_signs != sign_now)),
            })

    df = pd.DataFrame(records)
    print(f"Dataset: {len(df):,} samples | overall reversal rate: {df['flip'].mean():.3f}")
    return df


# ── Entry point ───────────────────────────────────────────────────────────────

def build(hours: int = DEFAULT_HOURS, output: str = DEFAULT_OUTPUT) -> Path:
    """Download 1s data (or load from cache) and build the reversal dataset."""
    price = load_or_download_1s(hours=hours)
    df = build_reversal_dataset(price)

    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    df.to_parquet(out, index=False)
    print(f"\nReversal dataset saved → {out}  ({len(df):,} rows)")
    return out


if __name__ == "__main__":
    build()
