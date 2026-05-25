"""
BTC Reversal Probability Model
================================
Gaussian kernel regression over the pre-computed reversal dataset.

Given:
  target_price   – BTC/USD at the start of the 5-minute window (price[0])
  current_price  – BTC/USD right now (price[t])
  time_left      – seconds remaining in the window (300 - t)

Returns:
  P(price reverses direction before window ends)

Optimisation: pre-computes a dense (200 × 60) grid at init time for O(1)
lookups. Falls back to the raw kernel only if the query is out of grid range.

Usage
-----
    from btc_reversal_model import ReversalModel

    model = ReversalModel()                            # loads default dataset path
    p = model.probability(95_000, 95_250, 180)
    print(f"P(reversal) = {p:.3f}")                   # e.g. 0.142

    # Or via delta directly (price already computed)
    p = model.probability_from_delta(delta_usd=250, remaining_seconds=180)
"""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

DEFAULT_DATASET = "data/crypto/BTC/reversal_dataset.parquet"


class ReversalModel:
    """
    Fast O(1) reversal probability lookup via pre-computed kernel-regression grid.

    Parameters
    ----------
    dataset_path   Path to the parquet file built by build_dataset.py.
    delta_bw       Kernel bandwidth (USD) for the Δ axis.
    time_bw        Kernel bandwidth (seconds) for the remaining-time axis.
    grid_delta_bins, grid_time_bins  Grid resolution for fast lookup.
    """

    def __init__(
        self,
        dataset_path: str | Path = DEFAULT_DATASET,
        delta_bw: float = 50.0,
        time_bw: float = 30.0,
        grid_delta_bins: int = 200,
        grid_time_bins: int = 60,
    ) -> None:
        path = Path(dataset_path)
        if not path.exists():
            raise FileNotFoundError(
                f"Reversal dataset not found: {path}\n"
                f"Run: poetry run build-reversal-dataset"
            )
        df = pd.read_parquet(path)
        required = {"delta_usd", "remaining_seconds", "flip"}
        if not required.issubset(df.columns):
            raise ValueError(f"Dataset missing columns. Expected: {required}. Got: {set(df.columns)}")

        self._delta     = df["delta_usd"].to_numpy(dtype=np.float64)
        self._time      = df["remaining_seconds"].to_numpy(dtype=np.float64)
        self._flip      = df["flip"].to_numpy(dtype=np.float64)
        self._base_rate = float(self._flip.mean())
        self._delta_bw  = delta_bw
        self._time_bw   = time_bw

        # Build fast lookup grid
        self._grid_delta_edges = np.linspace(self._delta.min(), self._delta.max(), grid_delta_bins + 1)
        self._grid_time_edges  = np.linspace(1, 299, grid_time_bins + 1)
        self._grid = self._build_grid(grid_delta_bins, grid_time_bins)

        print(
            f"ReversalModel loaded: {len(df):,} samples | "
            f"base rate={self._base_rate:.3f} | "
            f"grid={grid_delta_bins}×{grid_time_bins}"
        )

    # ── Public API ────────────────────────────────────────────────────────────

    def probability(
        self,
        target_price: float,
        current_price: float,
        time_left: int | float,
    ) -> float:
        """
        P(reversal) given prices and time remaining.

        Parameters
        ----------
        target_price   BTC/USD at window open (price[0]).
        current_price  BTC/USD right now (price[t]).
        time_left      Seconds remaining in the current 5-minute window.

        Returns float in [0, 1]. Returns base_rate if out of range.
        """
        delta = current_price - target_price
        return self.probability_from_delta(delta, time_left)

    def probability_from_delta(self, delta_usd: float, remaining_seconds: float) -> float:
        """
        P(reversal) directly from delta and remaining seconds.

        Uses fast grid lookup. Falls back to kernel if out of range.
        """
        # Clamp time to [1, 299]
        t = float(remaining_seconds)
        if not (1 <= t <= 299):
            return self._base_rate

        # Try grid lookup
        di = np.searchsorted(self._grid_delta_edges, delta_usd, side="right") - 1
        ti = np.searchsorted(self._grid_time_edges,  t,         side="right") - 1
        di = int(np.clip(di, 0, self._grid.shape[0] - 1))
        ti = int(np.clip(ti, 0, self._grid.shape[1] - 1))

        val = self._grid[di, ti]
        if np.isnan(val):
            val = self._kernel_query(delta_usd, t)
        return float(np.clip(val, 0.0, 1.0))

    @property
    def base_rate(self) -> float:
        """Overall unconditional reversal rate in the training data."""
        return self._base_rate

    # ── Grid builder ──────────────────────────────────────────────────────────

    def _build_grid(self, nd: int, nt: int) -> np.ndarray:
        """Pre-compute P(flip) at each grid cell via kernel regression."""
        grid = np.full((nd, nt), np.nan)
        d_centers = (self._grid_delta_edges[:-1] + self._grid_delta_edges[1:]) / 2
        t_centers = (self._grid_time_edges[:-1]  + self._grid_time_edges[1:])  / 2

        for di, dc in enumerate(d_centers):
            for ti, tc in enumerate(t_centers):
                grid[di, ti] = self._kernel_query(dc, tc)

        return grid

    def _kernel_query(self, delta: float, time_left: float) -> float:
        """Naïve Gaussian kernel regression (used only for grid construction)."""
        dw = np.exp(-0.5 * ((self._delta - delta) / self._delta_bw) ** 2)
        tw = np.exp(-0.5 * ((self._time   - time_left) / self._time_bw) ** 2)
        w  = dw * tw
        w_sum = w.sum()
        if w_sum < 1e-10:
            return self._base_rate
        return float((w * self._flip).sum() / w_sum)
