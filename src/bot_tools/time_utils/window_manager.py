"""
WindowManager
=============
Owns the 5-minute window boundary sequence.

Key design decisions:
- The first boundary is computed once from the real clock.
- Every subsequent boundary is prev + 300 s — never re-derived from the clock.
  This guarantees no window is ever skipped due to processing time between windows.
- All sleeps go through _isleep() which wakes every 0.2 s so Ctrl+C / stop()
  is responded to within 200 ms rather than after a 5-minute block.

Usage::

    wm = WindowManager()
    wm.start()                          # blocks until first boundary
    while wm.running:
        open_price = fetch_price()
        while wm.running and wm.remaining > 0:
            do_tick()
            wm.sleep(poll_interval)
        close_price = fetch_price()
        wm.advance()                    # move to next boundary
"""
from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WindowManager:
    """Manages 5-minute Polymarket window boundaries."""

    WINDOW_SECONDS = 300

    def __init__(self) -> None:
        self._boundary:    datetime | None = None
        self._window_end:  datetime | None = None
        self._running:     bool = False

    # ── Public API ────────────────────────────────────────────────────────────

    @property
    def running(self) -> bool:
        return self._running

    @property
    def boundary(self) -> datetime:
        if self._boundary is None:
            raise RuntimeError("WindowManager not started — call start() first")
        return self._boundary

    @property
    def window_end(self) -> datetime:
        if self._window_end is None:
            raise RuntimeError("WindowManager not started — call start() first")
        return self._window_end

    @property
    def remaining(self) -> int:
        """Seconds left in the current window. 0 when window has closed."""
        if self._window_end is None:
            return 0
        return max(0, int((self._window_end - _now()).total_seconds()))

    @property
    def elapsed(self) -> int:
        """Seconds elapsed since window open."""
        if self._boundary is None:
            return 0
        return max(0, int((_now() - self._boundary).total_seconds()))

    def stop(self) -> None:
        """Signal the manager to stop (Ctrl+C handler calls this)."""
        self._running = False

    def start(self) -> None:
        """
        Find the next 5-minute boundary and sleep until it.
        Blocks the calling thread. Sets running=True.
        """
        self._running = True
        boundary, wait = self._next_boundary()
        self._set_window(boundary)
        print(f"  [window] Next boundary in {wait:.1f}s "
              f"({boundary.strftime('%H:%M:%S')} UTC)")
        self.sleep(wait)

    def advance(self) -> None:
        """
        Move to the next window (prev_boundary + 300 s).
        Sleeps the fractional time until the boundary if processing finished early.
        """
        next_boundary  = self.boundary + timedelta(seconds=self.WINDOW_SECONDS)
        self._set_window(next_boundary)
        wait = max(0.0, (next_boundary - _now()).total_seconds())
        if wait > 0.1:
            self.sleep(wait)

    def sleep(self, seconds: float, granularity: float = 0.2) -> None:
        """
        Interruptible sleep: wakes every granularity seconds to check running.
        Returns early if stop() is called.
        """
        deadline = time.monotonic() + seconds
        while self._running:
            left = deadline - time.monotonic()
            if left <= 0:
                break
            time.sleep(min(granularity, left))

    def wait_for_close(self, extra: float = 0.5) -> None:
        """Sleep until the window has closed plus extra seconds."""
        remaining = (self._window_end - _now()).total_seconds()
        if remaining > 0:
            self.sleep(remaining + extra)

    # ── Private ───────────────────────────────────────────────────────────────

    def _set_window(self, boundary: datetime) -> None:
        self._boundary   = boundary
        self._window_end = boundary + timedelta(seconds=self.WINDOW_SECONDS)

    @staticmethod
    def _next_boundary() -> tuple[datetime, float]:
        now           = _now()
        start_of_hour = now.replace(minute=0, second=0, microsecond=0)
        elapsed       = (now - start_of_hour).total_seconds()
        next_b        = (int(elapsed / 300) + 1) * 300
        boundary      = start_of_hour + timedelta(seconds=next_b)
        wait          = next_b - elapsed
        return boundary, wait
