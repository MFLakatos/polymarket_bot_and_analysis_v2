"""PriceLoader — loads saved parquet price files and computes indicators.

Example::

    cfg = load_crypto_config()
    loader = PriceLoader(cfg)

    # Load BTC hourly data with all configured indicators
    df = loader.load("BTC", "1h")

    # Load last 500 candles only
    df = loader.load("BTC", "1h", lookback=500)

    # Load without computing indicators
    df = loader.load_raw("BTC", "1d")
"""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import pandas as pd

from crypto_data.config import CryptoDataConfig
from crypto_data.filters import FilterConfig, apply_filters, describe_filters


class PriceLoader:
    """Loads cached parquet price data with optional filtering and indicator computation."""

    def __init__(self, config: CryptoDataConfig) -> None:
        self.config = config

    # ── Public API ───────────────────────────────────────────────────────────

    def load(
        self,
        coin_id: str,
        interval: str,
        lookback: int = 0,
        compute_indicators: bool = True,
        filters: FilterConfig | None = None,
    ) -> pd.DataFrame:
        """Load price data with optional session/calendar filtering and indicators.

        Args:
            coin_id:  Coin identifier (e.g. "BTC").
            interval: Timeframe (e.g. "1h").
            lookback: Number of most-recent candles to return AFTER filtering (0 = all).
            compute_indicators: Whether to add SMA, EMA, RSI, MACD, BB columns.
            filters:  FilterConfig to apply. If None, uses config defaults.
                      Pass FilterConfig() explicitly to override config settings.

        Returns:
            DataFrame with OHLCV + indicator columns, sorted ascending by open_time.

        Example::

            # Use config defaults (from crypto_data.yaml filters section)
            df = loader.load("BTC", "1h")

            # Override: NYSE hours only, no weekends
            from crypto_data.filters import FilterConfig
            df = loader.load("BTC", "1h", filters=FilterConfig(
                exclude_weekends=True,
                exclude_outside_nyse=True,
            ))

            # New York session only
            from crypto_data.filters import FilterConfig, SessionFilterConfig
            df = loader.load("BTC", "1h", filters=FilterConfig(
                sessions=SessionFilterConfig(enabled=True, active=["New_York"]),
            ))
        """
        # Resolve filter config
        active_filters = filters if filters is not None else self.config.filters.to_filter_config()

        df = self.load_raw(coin_id, interval)

        # Apply filters BEFORE lookback (lookback takes last N of the filtered set)
        if active_filters.any_active:
            desc = describe_filters(active_filters)
            print(f"  Applying filters: {desc}")
            df = apply_filters(df, active_filters)

        # Apply lookback after filtering
        if lookback and lookback > 0:
            df = df.tail(lookback).reset_index(drop=True)

        if compute_indicators:
            df = self._add_indicators(df)
        return df

    def load_raw(self, coin_id: str, interval: str, lookback: int = 0,
                 filters: FilterConfig | None = None) -> pd.DataFrame:
        """Load raw OHLCV data without indicator computation.

        If filters are provided (or configured in config), they are applied here.
        lookback is applied AFTER filtering.
        """
        path = self._resolve_path(coin_id, interval)
        if not path.exists():
            raise FileNotFoundError(
                f"No data file found for {coin_id} {interval} at {path}. "
                f"Run: poetry run crypto-data download --coin {coin_id} --timeframe {interval}"
            )
        df = pd.read_parquet(path)
        df = df.sort_values("open_time").reset_index(drop=True)

        # Apply filters if provided
        active_filters = filters if filters is not None else self.config.filters.to_filter_config()
        if active_filters.any_active:
            df = apply_filters(df, active_filters)

        if lookback and lookback > 0:
            df = df.tail(lookback).reset_index(drop=True)
        return df

    def list_available(self) -> dict[str, list[str]]:
        """Return dict of {coin_id: [available intervals]} based on files present."""
        result: dict[str, list[str]] = {}
        for coin in self.config.coins:
            available = []
            for tf in coin.timeframes:
                p = self.config.data_path(coin.id, tf.filename)
                if p.exists():
                    available.append(tf.interval)
            if available:
                result[coin.id] = available
        return result

    def info(self, coin_id: str, interval: str) -> dict:
        """Return metadata about a stored dataset (rows, date range, size)."""
        path = self._resolve_path(coin_id, interval)
        if not path.exists():
            return {"exists": False, "path": str(path)}
        df = pd.read_parquet(path, columns=["open_time"])
        return {
            "exists": True,
            "path": str(path),
            "rows": len(df),
            "start": str(df["open_time"].min()),
            "end": str(df["open_time"].max()),
            "size_mb": round(path.stat().st_size / 1_048_576, 2),
        }

    # ── Indicator computation ────────────────────────────────────────────────

    def _add_indicators(self, df: pd.DataFrame) -> pd.DataFrame:
        """Add all configured technical indicators to the DataFrame."""
        ind = self.config.analysis.indicators
        close = df["close"]

        # Simple Moving Averages
        for period in ind.sma:
            df[f"sma_{period}"] = close.rolling(window=period).mean()

        # Exponential Moving Averages
        for period in ind.ema:
            df[f"ema_{period}"] = close.ewm(span=period, adjust=False).mean()

        # RSI
        df["rsi"] = self._rsi(close, ind.rsi_period)

        # MACD
        ema_fast = close.ewm(span=ind.macd_fast, adjust=False).mean()
        ema_slow = close.ewm(span=ind.macd_slow, adjust=False).mean()
        df["macd"] = ema_fast - ema_slow
        df["macd_signal"] = df["macd"].ewm(span=ind.macd_signal, adjust=False).mean()
        df["macd_hist"] = df["macd"] - df["macd_signal"]

        # Bollinger Bands
        bb_mid = close.rolling(window=ind.bb_period).mean()
        bb_std = close.rolling(window=ind.bb_period).std()
        df["bb_upper"] = bb_mid + ind.bb_std * bb_std
        df["bb_mid"] = bb_mid
        df["bb_lower"] = bb_mid - ind.bb_std * bb_std
        df["bb_width"] = (df["bb_upper"] - df["bb_lower"]) / bb_mid

        return df

    @staticmethod
    def _rsi(close: pd.Series, period: int) -> pd.Series:
        """Compute RSI using the Wilder smoothing method."""
        delta = close.diff()
        gain = delta.clip(lower=0)
        loss = (-delta).clip(lower=0)
        avg_gain = gain.ewm(alpha=1 / period, adjust=False).mean()
        avg_loss = loss.ewm(alpha=1 / period, adjust=False).mean()
        rs = avg_gain / avg_loss.replace(0, float("nan"))
        return 100 - (100 / (1 + rs))

    # ── Path helpers ─────────────────────────────────────────────────────────

    def _resolve_path(self, coin_id: str, interval: str) -> Path:
        coin = self.config.get_coin(coin_id)
        if coin is None:
            raise ValueError(f"Coin '{coin_id}' not in config.")
        tf = next((t for t in coin.timeframes if t.interval == interval), None)
        if tf is None:
            raise ValueError(
                f"Interval '{interval}' not configured for {coin_id}. "
                f"Available: {[t.interval for t in coin.timeframes]}"
            )
        return self.config.data_path(coin_id, tf.filename)
