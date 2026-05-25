"""
Market Session & Calendar Filters
===================================
Filters a price DataFrame to include only candles that fall within
specific market conditions:

  - Weekday / weekend separation
  - NYSE trading hours (with proper DST handling via zoneinfo)
  - Named trading sessions: Asia, Europe, New York, overlaps

All filters operate on the ``open_time`` column (UTC-aware datetime).
They never modify price data — they only drop rows.

Session definitions (UTC)
--------------------------
  Asia          00:00 – 09:00  (Tokyo / Sydney)
  Europe        07:00 – 16:30  (Frankfurt / London)
  New_York      13:30 – 20:00* (NYSE; EDT basis — DST-aware when pytz/zoneinfo available)
  Overlap_EU_NY 13:30 – 16:30  (Both Europe and NY open)
  Overlap_Asia_EU 07:00 – 09:00 (Both Asia and Europe open)

  * NYSE: 09:30–16:00 ET → 13:30–20:00 UTC (EDT/summer) or 14:30–21:00 UTC (EST/winter).
    When zoneinfo is available we use the exact local time (09:30–16:00 ET) so DST is
    handled correctly.  When it is not, we fall back to fixed UTC windows (EDT basis).

Usage
-----
    from crypto_data.filters import apply_filters, FilterConfig

    cfg = FilterConfig(
        exclude_weekends=True,
        exclude_outside_nyse=False,
        sessions=SessionFilterConfig(enabled=True, active=["New_York", "Europe"]),
    )
    df_filtered = apply_filters(df, cfg)

    # Or load from the full CryptoDataConfig:
    df_filtered = apply_filters(df, crypto_cfg.filters)
"""
from __future__ import annotations

from datetime import time as dtime
from typing import List

import pandas as pd

# ── Optional timezone support ─────────────────────────────────────────────────
try:
    from zoneinfo import ZoneInfo          # Python 3.9+
    _TZ_EASTERN = ZoneInfo("America/New_York")
    _HAS_ZONEINFO = True
except ImportError:
    try:
        import pytz
        _TZ_EASTERN = pytz.timezone("America/New_York")  # type: ignore[assignment]
        _HAS_ZONEINFO = True
    except ImportError:
        _TZ_EASTERN = None                 # type: ignore[assignment]
        _HAS_ZONEINFO = False


# ── Session window definitions (UTC, fallback when no tz lib) ─────────────────
# (start_hour, start_min, end_hour, end_min)
_SESSION_UTC: dict[str, tuple[int, int, int, int]] = {
    # Asia: Tokyo open (00:00 UTC) → Frankfurt open (07:00 UTC)
    "Asia":             (0,  0,  9,  0),
    # Europe: Frankfurt open → NYSE open
    "Europe":           (7,  0, 16, 30),
    # New York: NYSE open → NYSE close (EDT approximation)
    "New_York":         (13, 30, 20,  0),
    # Overlap: both Europe and NY desks open
    "Overlap_EU_NY":    (13, 30, 16, 30),
    # Overlap: both Asia and EU desks open
    "Overlap_Asia_EU":  (7,  0,  9,  0),
    # Extended NY (pre + post market on EDT basis)
    "New_York_Extended": (12, 0, 21,  0),
    # 24/7 (no-op — keeps all rows; useful to list explicitly)
    "All":              (0,  0, 23, 59),
}

# NYSE regular hours in local ET
_NYSE_OPEN_ET  = dtime(9, 30)
_NYSE_CLOSE_ET = dtime(16, 0)

# Known NYSE holidays (YYYY-MM-DD).  Extend as needed.
_NYSE_HOLIDAYS: frozenset[str] = frozenset({
    # 2024
    "2024-01-01", "2024-01-15", "2024-02-19", "2024-03-29", "2024-05-27",
    "2024-06-19", "2024-07-04", "2024-09-02", "2024-11-28", "2024-12-25",
    # 2025
    "2025-01-01", "2025-01-20", "2025-02-17", "2025-04-18", "2025-05-26",
    "2025-06-19", "2025-07-04", "2025-09-01", "2025-11-27", "2025-12-25",
    # 2026
    "2026-01-01", "2026-01-19", "2026-02-16", "2026-04-03", "2026-05-25",
    "2026-06-19", "2026-07-03", "2026-09-07", "2026-11-26", "2026-12-25",
})

AVAILABLE_SESSIONS: list[str] = sorted(_SESSION_UTC.keys())


# ── Filter config (can be constructed from YAML dict or Pydantic model) ────────

class SessionFilterConfig:
    """Which named sessions to keep."""

    def __init__(self, enabled: bool = False, active: List[str] | None = None) -> None:
        # Whether to apply session filtering at all
        self.enabled: bool = enabled
        # List of session names to KEEP (e.g. ["New_York", "Europe"])
        # If empty and enabled=True → keeps nothing (all filtered out) — warn user
        self.active: List[str] = active or []

    @classmethod
    def from_dict(cls, d: dict) -> "SessionFilterConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            active=list(d.get("active_sessions", d.get("active", []))),
        )


class FilterConfig:
    """Top-level filter configuration."""

    def __init__(
        self,
        exclude_weekends: bool = False,
        exclude_outside_nyse: bool = False,
        exclude_nyse_holidays: bool = False,
        sessions: SessionFilterConfig | None = None,
    ) -> None:
        # Drop Saturday and Sunday candles (UTC day)
        self.exclude_weekends: bool = exclude_weekends
        # Drop candles outside NYSE trading hours (Mon–Fri 09:30–16:00 ET)
        self.exclude_outside_nyse: bool = exclude_outside_nyse
        # Drop candles on known NYSE holidays
        self.exclude_nyse_holidays: bool = exclude_nyse_holidays
        # Named session filter
        self.sessions: SessionFilterConfig = sessions or SessionFilterConfig()

    @classmethod
    def from_dict(cls, d: dict) -> "FilterConfig":
        return cls(
            exclude_weekends=bool(d.get("exclude_weekends", False)),
            exclude_outside_nyse=bool(d.get("exclude_outside_nyse", False)),
            exclude_nyse_holidays=bool(d.get("exclude_nyse_holidays", False)),
            sessions=SessionFilterConfig.from_dict(d.get("sessions", {})),
        )

    @property
    def any_active(self) -> bool:
        return (
            self.exclude_weekends
            or self.exclude_outside_nyse
            or self.exclude_nyse_holidays
            or (self.sessions.enabled and bool(self.sessions.active))
        )


# ── Core filter function ──────────────────────────────────────────────────────

def apply_filters(df: pd.DataFrame, cfg: FilterConfig) -> pd.DataFrame:
    """
    Apply all enabled filters to a price DataFrame.

    Parameters
    ----------
    df  : DataFrame with an ``open_time`` column (UTC datetime, tz-aware or naive).
    cfg : FilterConfig specifying which filters to apply.

    Returns
    -------
    Filtered DataFrame (copy, original unchanged).  Index is reset.
    Reports how many rows were dropped per filter to stdout.
    """
    if df.empty or not cfg.any_active:
        return df

    df = df.copy()
    # Ensure UTC-aware datetime
    dt = pd.to_datetime(df["open_time"], utc=True)

    original_len = len(df)
    dropped: dict[str, int] = {}

    # ── 1. Weekends ───────────────────────────────────────────────────────────
    if cfg.exclude_weekends:
        mask = dt.dt.dayofweek < 5       # 0=Mon … 4=Fri
        n_dropped = (~mask).sum()
        df = df[mask].copy()
        dt = dt[mask]
        dropped["weekends"] = int(n_dropped)

    # ── 2. NYSE holidays ──────────────────────────────────────────────────────
    if cfg.exclude_nyse_holidays:
        date_strs = dt.dt.strftime("%Y-%m-%d")
        mask = ~date_strs.isin(_NYSE_HOLIDAYS)
        n_dropped = (~mask).sum()
        df = df[mask].copy()
        dt = dt[mask]
        dropped["nyse_holidays"] = int(n_dropped)

    # ── 3. NYSE trading hours ─────────────────────────────────────────────────
    if cfg.exclude_outside_nyse:
        mask = _nyse_hours_mask(dt)
        n_dropped = (~mask).sum()
        df = df[mask].copy()
        dt = dt[mask]
        dropped["outside_nyse"] = int(n_dropped)

    # ── 4. Session filter ─────────────────────────────────────────────────────
    if cfg.sessions.enabled and cfg.sessions.active:
        mask = _session_mask(dt, cfg.sessions.active)
        n_dropped = (~mask).sum()
        df = df[mask].copy()
        dt = dt[mask]
        dropped["outside_sessions"] = int(n_dropped)
    elif cfg.sessions.enabled and not cfg.sessions.active:
        print("  ⚠  sessions.enabled=true but sessions.active is empty — no rows filtered")

    # ── Report ────────────────────────────────────────────────────────────────
    total_dropped = original_len - len(df)
    if total_dropped > 0:
        pct = total_dropped / original_len * 100
        parts = ", ".join(f"{k}: {v:,}" for k, v in dropped.items())
        print(
            f"  [filters] {total_dropped:,} rows dropped ({pct:.1f}%) "
            f"from {original_len:,} → {len(df):,}  ({parts})"
        )

    return df.reset_index(drop=True)


# ── Session mask ──────────────────────────────────────────────────────────────

def _session_mask(dt: pd.Series, active_sessions: list[str]) -> pd.Series:
    """Return boolean mask: True for rows within any of the active sessions."""
    unknown = [s for s in active_sessions if s not in _SESSION_UTC]
    if unknown:
        print(
            f"  ⚠  Unknown sessions: {unknown}. "
            f"Available: {AVAILABLE_SESSIONS}"
        )

    combined = pd.Series(False, index=dt.index)
    hour_min = dt.dt.hour * 60 + dt.dt.minute  # minutes since midnight UTC

    for name in active_sessions:
        if name not in _SESSION_UTC:
            continue
        sh, sm, eh, em = _SESSION_UTC[name]
        start = sh * 60 + sm
        end   = eh * 60 + em
        if start <= end:
            combined |= (hour_min >= start) & (hour_min < end)
        else:
            # Wraps midnight (e.g. Asia can start at 22:00 UTC in some definitions)
            combined |= (hour_min >= start) | (hour_min < end)

    return combined


# ── NYSE hours mask ───────────────────────────────────────────────────────────

def _nyse_hours_mask(dt: pd.Series) -> pd.Series:
    """
    True for rows that fall within NYSE regular trading hours (09:30–16:00 ET).
    Uses zoneinfo/pytz for DST-correct conversion when available.
    Falls back to fixed UTC window (EDT basis: 13:30–20:00 UTC) otherwise.
    """
    if _HAS_ZONEINFO and _TZ_EASTERN is not None:
        return _nyse_mask_tz_aware(dt)
    else:
        return _nyse_mask_utc_approx(dt)


def _nyse_mask_tz_aware(dt: pd.Series) -> pd.Series:
    """DST-correct NYSE mask using tz conversion."""
    # Convert UTC → ET (handles EST/EDT automatically)
    try:
        dt_et = dt.dt.tz_convert(_TZ_EASTERN)
    except Exception:
        return _nyse_mask_utc_approx(dt)

    local_time = dt_et.dt.hour * 60 + dt_et.dt.minute
    open_min   = _NYSE_OPEN_ET.hour  * 60 + _NYSE_OPEN_ET.minute   # 570
    close_min  = _NYSE_CLOSE_ET.hour * 60 + _NYSE_CLOSE_ET.minute  # 960
    # Also exclude weekdays: dt.dayofweek already filters via exclude_weekends,
    # but NYSE hours only apply Mon–Fri regardless.
    is_weekday = dt.dt.dayofweek < 5
    return is_weekday & (local_time >= open_min) & (local_time < close_min)


def _nyse_mask_utc_approx(dt: pd.Series) -> pd.Series:
    """Fixed-UTC approximation (EDT basis: 13:30–20:00 UTC, Mon–Fri)."""
    hour_min   = dt.dt.hour * 60 + dt.dt.minute
    is_weekday = dt.dt.dayofweek < 5
    return is_weekday & (hour_min >= 810) & (hour_min < 1200)  # 13:30–20:00


# ── Convenience helpers ───────────────────────────────────────────────────────

def describe_filters(cfg: FilterConfig) -> str:
    """Return a human-readable description of active filters."""
    parts: list[str] = []
    if cfg.exclude_weekends:
        parts.append("no weekends")
    if cfg.exclude_outside_nyse:
        mode = "DST-aware" if _HAS_ZONEINFO else "UTC-approx"
        parts.append(f"NYSE hours only ({mode})")
    if cfg.exclude_nyse_holidays:
        parts.append("no NYSE holidays")
    if cfg.sessions.enabled and cfg.sessions.active:
        parts.append(f"sessions: {', '.join(cfg.sessions.active)}")
    return ", ".join(parts) if parts else "none"


def session_info() -> str:
    """Return a formatted table of all available sessions."""
    lines = [f"  {'Session':<20} {'UTC Start':>10}  {'UTC End':>10}"]
    lines.append("  " + "─" * 46)
    for name, (sh, sm, eh, em) in sorted(_SESSION_UTC.items()):
        lines.append(f"  {name:<20} {sh:02d}:{sm:02d} UTC  →  {eh:02d}:{em:02d} UTC")
    if not _HAS_ZONEINFO:
        lines.append("\n  ⚠  zoneinfo/pytz not found — NYSE filter uses fixed UTC approx")
    return "\n".join(lines)
