"""
ATR Filter
===========
Computes ATR (Average True Range) and NATR (Normalized ATR = ATR/Close × 100)
and provides a gate: `passes(close, high, low) → bool`.

Two usage modes:

1. OFFLINE — load from a parquet file (for backtesting / bot startup warmup)::

    atr = ATRFilter.from_parquet("data/crypto/BTC/btc_1m.parquet", period=14)
    ok, reason = atr.check(current_close)

2. ONLINE — update tick by tick during live trading::

    atr = ATRFilter(period=14, max_natr=2.0)
    atr.update(high, low, close)   # call each new candle
    ok, reason = atr.check(close)

ATR formula (Wilder's smoothing):
    TR  = max(high - low, |high - prev_close|, |low - prev_close|)
    ATR = EWM(TR, alpha = 1/period)

NATR (%) = (ATR / close) × 100
Gate:  if NATR > max_natr_pct → skip trade
"""
from __future__ import annotations

from collections import deque
from pathlib import Path
from typing import Optional

import math


class ATRFilter:
    """
    Online ATR/NATR calculator with a configurable max-NATR gate.

    Parameters
    ----------
    period      : ATR smoothing period (Wilder's EWM: alpha = 1/period)
    max_natr    : Maximum allowed NATR % before blocking a trade.
                  None = always pass (ATR computed but gate disabled).
    min_samples : Minimum candles required before the gate is active.
                  Before this, check() always returns (True, "warming up").
    """

    def __init__(
        self,
        period:      int   = 14,
        max_natr:    float | None = None,
        min_samples: int   = 14,
    ) -> None:
        if period < 1:
            raise ValueError("period must be >= 1")
        self.period      = period
        self.max_natr    = max_natr
        self.min_samples = min_samples
        self._alpha      = 1.0 / period
        self._atr:        float | None = None
        self._prev_close: float | None = None
        self._n_samples:  int = 0

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def atr(self) -> float | None:
        return self._atr

    def natr(self, close: float) -> float | None:
        """NATR % = (ATR / close) × 100. None if not enough data."""
        if self._atr is None or close <= 0:
            return None
        return (self._atr / close) * 100.0

    def update(self, high: float, low: float, close: float) -> None:
        """Feed one new candle (call in chronological order)."""
        if self._prev_close is None:
            tr = high - low
        else:
            tr = max(
                high - low,
                abs(high - self._prev_close),
                abs(low  - self._prev_close),
            )
        if self._atr is None:
            self._atr = tr
        else:
            self._atr = self._alpha * tr + (1 - self._alpha) * self._atr
        self._prev_close = close
        self._n_samples += 1

    def check(self, close: float) -> tuple[bool, str]:
        """
        Returns (is_ok, reason).
        is_ok=True  → ATR within limits, proceed
        is_ok=False → ATR too high, skip trade
        """
        if self._n_samples < self.min_samples:
            return True, f"warming up ({self._n_samples}/{self.min_samples} samples)"
        if self.max_natr is None:
            return True, ""
        natr = self.natr(close)
        if natr is None:
            return True, ""
        if natr > self.max_natr:
            return False, (f"NATR {natr:.2f}% > max {self.max_natr:.2f}%  "
                           f"(ATR=${self._atr:.2f})")
        return True, ""

    def status(self, close: float) -> str:
        """Human-readable current state."""
        if self._atr is None:
            return "ATR=n/a"
        natr = self.natr(close)
        gate = f"  max={self.max_natr:.2f}%" if self.max_natr else ""
        return f"ATR=${self._atr:.2f}  NATR={natr:.2f}%{gate}"

    # ── Offline loader ────────────────────────────────────────────────────────

    @classmethod
    def from_parquet(
        cls,
        parquet_path: str | Path,
        period: int = 14,
        max_natr: float | None = None,
        warmup_rows: int | None = None,
    ) -> "ATRFilter":
        """
        Build an ATRFilter pre-warmed from a parquet file.
        Uses the last `warmup_rows` rows (default: 3 × period).

        The resulting filter can be queried with check(current_close).
        Call update(h, l, c) for each new candle as it arrives live.
        """
        import pandas as pd
        path = Path(parquet_path)
        if not path.exists():
            raise FileNotFoundError(f"Parquet not found: {path}")
        df = pd.read_parquet(path)
        df.columns = [c.lower() for c in df.columns]
        required = {"high", "low", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"Parquet missing columns: {missing}")
        df = df.sort_values("open_time") if "open_time" in df.columns else df
        n = warmup_rows or max(3 * period, 100)
        df = df.tail(n)

        obj = cls(period=period, max_natr=max_natr, min_samples=period)
        for _, row in df.iterrows():
            obj.update(float(row["high"]), float(row["low"]), float(row["close"]))
        return obj

    @classmethod
    def disabled(cls) -> "ATRFilter":
        """Returns an ATRFilter that always passes (max_natr=None)."""
        return cls(max_natr=None)
