"""Binance kline downloader.

Downloads OHLCV (open/high/low/close/volume) candle data from Binance
for any symbol and interval, paginating backwards from the most recent
candle until the requested duration is covered.

Saves results as Parquet files for fast reloading.
"""
from __future__ import annotations

import time
from pathlib import Path
from typing import Optional

import pandas as pd
import requests

from crypto_data.config import CoinConfig, CryptoDataConfig, TimeframeConfig


class BinanceDownloader:
    """Downloads kline data from Binance REST API.

    Usage::

        cfg = load_crypto_config()
        dl = BinanceDownloader(cfg)
        df = dl.download("BTC", "1h")  # returns DataFrame, saves to disk
    """

    KLINE_COLS = [
        "open_time", "open", "high", "low", "close", "volume",
        "close_time", "quote_asset_volume", "num_trades",
        "taker_buy_base", "taker_buy_quote", "ignore",
    ]

    def __init__(self, config: CryptoDataConfig) -> None:
        self.config = config
        self._session = requests.Session()

    def download(
        self,
        coin_id: str,
        interval: str,
        force: bool = False,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """Download klines for *coin_id* at *interval* and persist to parquet.

        Args:
            coin_id: Coin identifier matching config (e.g. "BTC").
            interval: Binance interval string (e.g. "1h", "5m", "1d").
            force: Re-download even if local file already exists.
            verbose: Print progress updates.

        Returns:
            DataFrame with columns: open_time, open, high, low, close, volume.
        """
        coin = self.config.get_coin(coin_id)
        if coin is None:
            raise ValueError(f"Coin '{coin_id}' not found in config. Available: "
                             f"{[c.id for c in self.config.coins]}")

        tf = self._find_timeframe(coin, interval)
        if tf is None:
            raise ValueError(f"Interval '{interval}' not configured for {coin_id}. "
                             f"Available: {[t.interval for t in coin.timeframes]}")

        out_path = self.config.data_path(coin_id, tf.filename)

        # Use cache if available and not forced
        if out_path.exists() and self.config.storage.use_cache and not force:
            if verbose:
                print(f"  [cache] Loading {coin_id} {interval} from {out_path}")
            return pd.read_parquet(out_path)

        # Download from Binance
        df = self._fetch_klines(coin.symbol, interval, tf.hours, verbose=verbose)

        # Persist
        out_path.parent.mkdir(parents=True, exist_ok=True)
        df.to_parquet(out_path, compression=self.config.storage.parquet_compression, index=False)
        if verbose:
            print(f"  [saved] {len(df):,} rows → {out_path}")

        return df

    def download_all(self, force: bool = False, verbose: bool = True) -> dict[str, dict[str, pd.DataFrame]]:
        """Download all coins × timeframes defined in config.

        Returns:
            Nested dict: {coin_id: {interval: DataFrame}}
        """
        results: dict[str, dict[str, pd.DataFrame]] = {}
        for coin in self.config.coins:
            results[coin.id] = {}
            for tf in coin.timeframes:
                if verbose:
                    print(f"\n[{coin.id}] Downloading {tf.interval} ({tf.hours}h)...")
                try:
                    df = self.download(coin.id, tf.interval, force=force, verbose=verbose)
                    results[coin.id][tf.interval] = df
                except Exception as exc:
                    print(f"  [error] {coin.id} {tf.interval}: {exc}")
        return results

    # ── Private helpers ──────────────────────────────────────────────────────

    def _find_timeframe(self, coin: CoinConfig, interval: str) -> Optional[TimeframeConfig]:
        for tf in coin.timeframes:
            if tf.interval == interval:
                return tf
        return None

    def _fetch_klines(
        self,
        symbol: str,
        interval: str,
        hours: int,
        verbose: bool = True,
    ) -> pd.DataFrame:
        """Page through Binance klines API and return a sorted DataFrame."""
        cfg = self.config.source
        total_seconds = hours * 3_600

        # Estimate pages needed (Binance returns up to page_size candles)
        interval_seconds = self._interval_to_seconds(interval)
        total_candles = total_seconds // interval_seconds
        pages_needed = total_candles // cfg.page_size + 2

        url = f"{cfg.base_url}/api/v3/klines"
        all_frames: list[pd.DataFrame] = []
        end_time: Optional[int] = None

        if verbose:
            print(f"  Fetching ~{hours}h of {interval} data "
                  f"({total_candles:,} candles, up to {pages_needed} pages)…")

        for page in range(pages_needed):
            params: dict = {"symbol": symbol, "interval": interval, "limit": cfg.page_size}
            if end_time is not None:
                params["endTime"] = end_time

            try:
                resp = self._session.get(url, params=params, timeout=cfg.timeout_seconds)
                resp.raise_for_status()
                raw = resp.json()
            except requests.RequestException as exc:
                print(f"  Page {page}: network error – {exc}. Stopping early.")
                break

            if not raw:
                break

            df = pd.DataFrame(raw, columns=self.KLINE_COLS)
            df["close"] = df["close"].astype(float)
            df["open"] = df["open"].astype(float)
            df["high"] = df["high"].astype(float)
            df["low"] = df["low"].astype(float)
            df["volume"] = df["volume"].astype(float)
            df["open_time"] = pd.to_datetime(df["open_time"], unit="ms", utc=True)
            df["close_time"] = pd.to_datetime(df["close_time"], unit="ms", utc=True)
            all_frames.append(df[["open_time", "open", "high", "low", "close", "volume", "close_time"]])

            # Move backwards
            end_time = int(df["open_time"].iloc[0].timestamp() * 1_000) - 1

            # Check if we have enough data
            total_rows = sum(len(f) for f in all_frames)
            if total_rows >= total_candles:
                break

            if (page + 1) % 20 == 0 and verbose:
                print(f"  {page + 1}/{pages_needed} pages ({total_rows:,} rows)…")

            time.sleep(cfg.rate_limit_delay)

        if not all_frames:
            raise RuntimeError(f"No data downloaded for {symbol} {interval}")

        result = (
            pd.concat(all_frames)
            .drop_duplicates("open_time")
            .sort_values("open_time")
            .reset_index(drop=True)
        )

        if verbose:
            print(f"  Downloaded {len(result):,} rows "
                  f"({result['open_time'].iloc[0]} → {result['open_time'].iloc[-1]})")

        return result

    @staticmethod
    def _interval_to_seconds(interval: str) -> int:
        """Convert Binance interval string to seconds."""
        mapping = {
            "1s": 1, "1m": 60, "3m": 180, "5m": 300, "15m": 900,
            "30m": 1800, "1h": 3600, "2h": 7200, "4h": 14400,
            "6h": 21600, "8h": 28800, "12h": 43200,
            "1d": 86400, "3d": 259200, "1w": 604800,
        }
        return mapping.get(interval, 60)
