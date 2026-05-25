"""
Candle Pattern Analyzer
=======================
Scans OHLCV price data and counts every N-candle directional pattern
(each candle classified as UP if Close > Open, DOWN otherwise).

For a given num_candles=3, it tracks all 2^3 = 8 possible patterns:
  (UP, UP, UP), (UP, UP, DOWN), ... (DOWN, DOWN, DOWN)

For every pattern, it records:
  - How many times that exact sequence appeared
  - How many times each was followed by UP vs DOWN

This allows querying: "given the last N candles were [UP, DOWN, UP],
what is the probability the next candle is UP?"

Results are persisted to disk as JSON and can be loaded at any time
for fast O(1) lookup via CandlePatternModel.
"""
from __future__ import annotations

import json
from collections import defaultdict
from pathlib import Path
from typing import Iterator

import pandas as pd


# ── Direction helpers ─────────────────────────────────────────────────────────

def candle_direction(open_: float, close: float) -> str:
    """Return 'UP' if Close > Open, 'DOWN' otherwise."""
    return "UP" if close > open_ else "DOWN"


def iter_patterns(df: pd.DataFrame, num_candles: int) -> Iterator[tuple[tuple[str, ...], str]]:
    """
    Iterate over all windows of size num_candles+1 in df.

    Yields (pattern_tuple, next_direction) where:
      pattern_tuple  = (dir[t], dir[t+1], ..., dir[t+num_candles-1])
      next_direction = direction of candle at t+num_candles
    """
    if len(df) < num_candles + 1:
        return

    dirs = [
        candle_direction(row["open"], row["close"])
        for _, row in df.iterrows()
    ]

    for i in range(len(dirs) - num_candles):
        pattern = tuple(dirs[i : i + num_candles])
        next_dir = dirs[i + num_candles]
        yield pattern, next_dir


# ── Core analyzer ─────────────────────────────────────────────────────────────

class CandlePatternAnalyzer:
    """
    Scans historical OHLCV data and computes pattern transition counts.

    Parameters
    ----------
    num_candles : int
        Length of the pattern window (e.g. 3 → analyse 3-candle sequences).
    """

    def __init__(self, num_candles: int) -> None:
        if num_candles < 1:
            raise ValueError("num_candles must be >= 1")
        self.num_candles = num_candles

        # counts[pattern]["total"]    = how many times this pattern appeared
        # counts[pattern]["UP"]       = how many were followed by UP
        # counts[pattern]["DOWN"]     = how many were followed by DOWN
        self._counts: dict[str, dict[str, int]] = defaultdict(lambda: {"total": 0, "UP": 0, "DOWN": 0})

    def fit(self, df: pd.DataFrame) -> "CandlePatternAnalyzer":
        """
        Scan a DataFrame and count all pattern occurrences.

        df must contain columns: open, close (case-insensitive).
        Scans from the oldest candle to the newest.
        """
        df = df.copy()
        df.columns = [c.lower() for c in df.columns]
        required = {"open", "close"}
        missing = required - set(df.columns)
        if missing:
            raise ValueError(f"DataFrame missing columns: {missing}")

        df = df.sort_values("open_time") if "open_time" in df.columns else df.reset_index(drop=True)

        for pattern, next_dir in iter_patterns(df, self.num_candles):
            key = _pattern_key(pattern)
            self._counts[key]["total"] += 1
            self._counts[key][next_dir] += 1

        return self

    def total_patterns(self) -> int:
        """Total number of pattern occurrences observed."""
        return sum(v["total"] for v in self._counts.values())

    def pattern_count(self, pattern: tuple[str, ...] | list[str]) -> int:
        """How many times a specific pattern appeared."""
        return self._counts[_pattern_key(tuple(pattern))]["total"]

    def probabilities(self, pattern: tuple[str, ...] | list[str]) -> dict[str, float]:
        """
        Returns {"UP": p_up, "DOWN": p_down, "count": n} for a given pattern.
        Returns 50/50 with count=0 if pattern was never seen.
        """
        key = _pattern_key(tuple(pattern))
        c = self._counts.get(key, {"total": 0, "UP": 0, "DOWN": 0})
        total = c["total"]
        if total == 0:
            return {"UP": 0.5, "DOWN": 0.5, "count": 0}
        return {
            "UP":    c["UP"]   / total,
            "DOWN":  c["DOWN"] / total,
            "count": total,
        }

    def all_probabilities(self) -> dict[str, dict]:
        """
        Returns a dict of all observed patterns and their stats.
        Sorted by count descending.
        """
        result = {}
        for key, c in sorted(self._counts.items(), key=lambda x: -x[1]["total"]):
            total = c["total"]
            result[key] = {
                "pattern": _key_to_pattern(key),
                "count":   total,
                "UP":      c["UP"],
                "DOWN":    c["DOWN"],
                "p_up":    c["UP"]   / total if total else 0.5,
                "p_down":  c["DOWN"] / total if total else 0.5,
            }
        return result

    def to_dict(self) -> dict:
        """Serialize to a plain dict for JSON persistence."""
        return {
            "num_candles": self.num_candles,
            "counts": {k: dict(v) for k, v in self._counts.items()},
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CandlePatternAnalyzer":
        """Deserialize from a plain dict."""
        obj = cls(num_candles=data["num_candles"])
        for key, counts in data["counts"].items():
            obj._counts[key] = dict(counts)
        return obj

    def save(self, path: str | Path) -> Path:
        """Persist to JSON."""
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(self.to_dict(), indent=2))
        return p

    @classmethod
    def load(cls, path: str | Path) -> "CandlePatternAnalyzer":
        """Load from JSON."""
        data = json.loads(Path(path).read_text())
        return cls.from_dict(data)


# ── Fast lookup model ─────────────────────────────────────────────────────────

class CandlePatternModel:
    """
    Fast O(1) lookup model built from a CandlePatternAnalyzer result.

    Loads the precomputed probability table from disk once, then answers
    queries in microseconds via dict lookup.

    Usage
    -----
        model = CandlePatternModel("output/candle_patterns/btc_1h_n3.json")
        p = model.p_up(["UP", "DOWN", "UP"])    # → 0.537
        p = model.p_down(["DOWN", "DOWN", "UP"]) # → 0.461
        info = model.query(["UP", "UP", "DOWN"]) # → full dict
    """

    def __init__(self, path: str | Path) -> None:
        self._analyzer = CandlePatternAnalyzer.load(path)
        self.num_candles = self._analyzer.num_candles
        # Pre-materialise all probabilities into a flat dict for O(1) access
        self._table: dict[str, dict] = self._analyzer.all_probabilities()

    def query(self, pattern: list[str] | tuple[str, ...]) -> dict:
        """
        Full probability info for a pattern.

        Returns {"UP": float, "DOWN": float, "count": int, "pattern": list}
        """
        key = _pattern_key(tuple(p.upper() for p in pattern))
        entry = self._table.get(key)
        if entry is None:
            return {"UP": 0.5, "DOWN": 0.5, "count": 0, "pattern": list(pattern)}
        return {
            "UP":      entry["p_up"],
            "DOWN":    entry["p_down"],
            "count":   entry["count"],
            "pattern": entry["pattern"],
        }

    def p_up(self, pattern: list[str] | tuple[str, ...]) -> float:
        """P(next candle is UP) given the last num_candles pattern."""
        return self.query(pattern)["UP"]

    def p_down(self, pattern: list[str] | tuple[str, ...]) -> float:
        """P(next candle is DOWN) given the last num_candles pattern."""
        return self.query(pattern)["DOWN"]

    def all_patterns(self) -> dict[str, dict]:
        """Return the full precomputed probability table."""
        return self._table


# ── Internal helpers ──────────────────────────────────────────────────────────

def _pattern_key(pattern: tuple[str, ...]) -> str:
    """Convert ('UP', 'DOWN', 'UP') → 'UP|DOWN|UP'."""
    return "|".join(p.upper() for p in pattern)


def _key_to_pattern(key: str) -> list[str]:
    """Convert 'UP|DOWN|UP' → ['UP', 'DOWN', 'UP']."""
    return key.split("|")
