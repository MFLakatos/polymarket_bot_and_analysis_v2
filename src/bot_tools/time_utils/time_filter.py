"""
TimeFilter
===========
Runtime check: "Should the bot trade right now?"

Works on a single datetime point (not a DataFrame) — suitable for live bots.
Reuses the session definitions from crypto_data.filters.

Configuration keys (all optional, default False/empty):
  exclude_weekends:      bool   – block Saturday/Sunday UTC
  exclude_outside_nyse:  bool   – block hours outside NYSE 09:30–16:00 ET
  exclude_nyse_holidays: bool   – block known NYSE public holidays
  sessions:
    enabled:             bool   – enable session gating
    blocked_sessions:    list   – sessions to BLOCK (bot goes idle)
    allowed_sessions:    list   – sessions to ALLOW (all others blocked)
    (if both provided, allowed_sessions takes precedence)

Available sessions: Asia, Europe, New_York, Overlap_EU_NY, Overlap_Asia_EU,
                    New_York_Extended, All

Usage::

    tf = TimeFilter.from_dict({
        "exclude_weekends": True,
        "sessions": {
            "enabled": True,
            "allowed_sessions": ["New_York", "Europe"],
        }
    })
    ok, reason = tf.check()          # checks datetime.now(UTC)
    ok, reason = tf.check(some_dt)   # checks specific datetime
"""
from __future__ import annotations

from datetime import datetime, time as dtime, timezone
from typing import List

# ── Session UTC windows (same as crypto_data.filters) ─────────────────────────
_SESSION_UTC: dict[str, tuple[int, int, int, int]] = {
    "Asia":              (0,  0,  9,  0),
    "Europe":            (7,  0, 16, 30),
    "New_York":          (13, 30, 20,  0),
    "Overlap_EU_NY":     (13, 30, 16, 30),
    "Overlap_Asia_EU":   (7,  0,  9,  0),
    "New_York_Extended": (12,  0, 21,  0),
    "All":               (0,  0, 23, 59),
}

_NYSE_OPEN_ET  = dtime(9, 30)
_NYSE_CLOSE_ET = dtime(16, 0)

_NYSE_HOLIDAYS: frozenset[str] = frozenset({
    "2024-01-01","2024-01-15","2024-02-19","2024-03-29","2024-05-27",
    "2024-06-19","2024-07-04","2024-09-02","2024-11-28","2024-12-25",
    "2025-01-01","2025-01-20","2025-02-17","2025-04-18","2025-05-26",
    "2025-06-19","2025-07-04","2025-09-01","2025-11-27","2025-12-25",
    "2026-01-01","2026-01-19","2026-02-16","2026-04-03","2026-05-25",
    "2026-06-19","2026-07-03","2026-09-07","2026-11-26","2026-12-25",
})

try:
    from zoneinfo import ZoneInfo
    _TZ_ET = ZoneInfo("America/New_York")
    _HAS_TZ = True
except ImportError:
    try:
        import pytz
        _TZ_ET = pytz.timezone("America/New_York")  # type: ignore[assignment]
        _HAS_TZ = True
    except ImportError:
        _TZ_ET = None  # type: ignore[assignment]
        _HAS_TZ = False

AVAILABLE_SESSIONS = sorted(_SESSION_UTC.keys())


class SessionConfig:
    def __init__(
        self,
        enabled: bool = False,
        allowed_sessions: List[str] | None = None,
        blocked_sessions: List[str] | None = None,
    ):
        self.enabled          = enabled
        self.allowed_sessions = allowed_sessions or []  # allow-list (others blocked)
        self.blocked_sessions = blocked_sessions or []  # block-list (others allowed)

    @classmethod
    def from_dict(cls, d: dict) -> "SessionConfig":
        return cls(
            enabled=bool(d.get("enabled", False)),
            allowed_sessions=list(d.get("allowed_sessions", [])),
            blocked_sessions=list(d.get("blocked_sessions", [])),
        )


class TimeFilter:
    """Runtime point-in-time trading gate."""

    def __init__(
        self,
        exclude_weekends:     bool = False,
        exclude_outside_nyse: bool = False,
        exclude_nyse_holidays: bool = False,
        sessions:             SessionConfig | None = None,
    ):
        self.exclude_weekends      = exclude_weekends
        self.exclude_outside_nyse  = exclude_outside_nyse
        self.exclude_nyse_holidays = exclude_nyse_holidays
        self.sessions              = sessions or SessionConfig()

    @classmethod
    def from_dict(cls, d: dict) -> "TimeFilter":
        return cls(
            exclude_weekends=bool(d.get("exclude_weekends", False)),
            exclude_outside_nyse=bool(d.get("exclude_outside_nyse", False)),
            exclude_nyse_holidays=bool(d.get("exclude_nyse_holidays", False)),
            sessions=SessionConfig.from_dict(d.get("sessions", {})),
        )

    @classmethod
    def always_open(cls) -> "TimeFilter":
        """No restrictions — always returns True."""
        return cls()

    def check(self, dt: datetime | None = None) -> tuple[bool, str]:
        """
        Returns (is_open, reason).
        is_open=True  → bot may trade
        is_open=False → bot should skip, reason explains why
        """
        if dt is None:
            dt = datetime.now(timezone.utc)
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)

        # Weekend
        if self.exclude_weekends and dt.weekday() >= 5:
            day = "Saturday" if dt.weekday() == 5 else "Sunday"
            return False, f"weekend ({day})"

        # NYSE holiday
        if self.exclude_nyse_holidays:
            date_str = dt.strftime("%Y-%m-%d")
            if date_str in _NYSE_HOLIDAYS:
                return False, f"NYSE holiday ({date_str})"

        # NYSE trading hours
        if self.exclude_outside_nyse:
            ok, reason = self._nyse_check(dt)
            if not ok:
                return False, reason

        # Session filter
        if self.sessions.enabled:
            ok, reason = self._session_check(dt)
            if not ok:
                return False, reason

        return True, ""

    def is_open(self, dt: datetime | None = None) -> bool:
        ok, _ = self.check(dt)
        return ok

    def describe(self) -> str:
        parts: list[str] = []
        if self.exclude_weekends:
            parts.append("no weekends")
        if self.exclude_outside_nyse:
            mode = "DST-aware" if _HAS_TZ else "UTC-approx"
            parts.append(f"NYSE hours ({mode})")
        if self.exclude_nyse_holidays:
            parts.append("no NYSE holidays")
        if self.sessions.enabled:
            if self.sessions.allowed_sessions:
                parts.append(f"allow: {', '.join(self.sessions.allowed_sessions)}")
            elif self.sessions.blocked_sessions:
                parts.append(f"block: {', '.join(self.sessions.blocked_sessions)}")
        return ", ".join(parts) if parts else "none (always open)"

    # ── NYSE check ────────────────────────────────────────────────────────────

    def _nyse_check(self, dt: datetime) -> tuple[bool, str]:
        if dt.weekday() >= 5:
            return False, "NYSE closed (weekend)"
        if _HAS_TZ and _TZ_ET is not None:
            try:
                dt_et    = dt.astimezone(_TZ_ET)
                local_t  = dtime(dt_et.hour, dt_et.minute)
                in_hours = _NYSE_OPEN_ET <= local_t < _NYSE_CLOSE_ET
                if not in_hours:
                    return False, f"outside NYSE hours ({dt_et.strftime('%H:%M')} ET)"
                return True, ""
            except Exception:
                pass
        # UTC fallback: EDT basis 13:30–20:00
        hm = dt.hour * 60 + dt.minute
        if not (810 <= hm < 1200):
            return False, f"outside NYSE hours UTC approx ({dt.strftime('%H:%M')} UTC)"
        return True, ""

    # ── Session check ─────────────────────────────────────────────────────────

    def _session_check(self, dt: datetime) -> tuple[bool, str]:
        hm  = dt.hour * 60 + dt.minute
        active: list[str] = []
        for name, (sh, sm, eh, em) in _SESSION_UTC.items():
            start = sh * 60 + sm
            end   = eh * 60 + em
            if start <= end:
                if start <= hm < end:
                    active.append(name)
            else:
                if hm >= start or hm < end:
                    active.append(name)

        # Allow-list mode: must be in at least one allowed session
        if self.sessions.allowed_sessions:
            for s in self.sessions.allowed_sessions:
                if s in active:
                    return True, ""
            allowed_str = ", ".join(self.sessions.allowed_sessions)
            active_str  = ", ".join(active) if active else "none"
            return False, f"not in allowed sessions [{allowed_str}] (active: {active_str})"

        # Block-list mode: must not be in any blocked session
        if self.sessions.blocked_sessions:
            for s in self.sessions.blocked_sessions:
                if s in active:
                    return False, f"blocked session: {s}"

        return True, ""
