"""
TradeLogger
===========
Structured per-decision logger for bots.

Logs every evaluation (trade placed, skipped, window open/close) to:
  - A rotating CSV file for backtest analysis
  - Optional stdout (configurable level)

Each row captures the full market state at decision time so you can
reconstruct exactly why the bot did what it did during any period.

CSV columns:
  timestamp         ISO UTC of the decision
  window_start      ISO UTC of window open
  window_close      ISO UTC of window close
  event_type        WINDOW_OPEN | TICK | TRADE | SKIP | WINDOW_CLOSE | INFO | ERROR
  btc_price         BTC/USD at decision time
  open_price        BTC/USD at window open (= target price)
  delta_usd         btc_price - open_price
  direction         UP | DOWN | -
  remaining_secs    seconds left in window
  reversal_prob     P(reversal) from model
  atr_value         absolute ATR (NaN if not computed)
  natr_pct          normalised ATR % (NaN if not computed)
  est_share_price   estimated Polymarket share cost
  max_entry_price   configured cap (NaN if not set)
  shares_added      incremental shares in this order (0 if skip)
  shares_total      cumulative shares for direction this window
  tier_label        which tier fired (e.g. "tier_0.3x1")
  action            TRADE | SKIP | -
  skip_reason       human-readable reason if SKIP
  order_id          CLOB order id (simulation / live)
  pnl               realised P&L for this trade (filled at window close)
  mode              simulation | live

Usage::

    logger = TradeLogger(
        log_dir="output/logs",
        bot_name="btc_5m_reversal",
        mode="simulation",
        verbose=True,
    )
    logger.open_window(boundary, window_end, open_price)
    logger.log_tick(btc_price, delta, prob, remaining)
    logger.log_skip("price > MAX_ENTRY_PRICE", direction, btc_price, ...)
    logger.log_trade(direction, shares_added, shares_total, ...)
    logger.close_window(close_price, winner, trades)
"""
from __future__ import annotations

import csv
import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal, Optional


EventType = Literal[
    "WINDOW_OPEN", "TICK", "TRADE", "SKIP", "WINDOW_CLOSE", "INFO", "ERROR"
]

_FIELDS = [
    "timestamp", "window_start", "window_close", "event_type",
    "btc_price", "open_price", "delta_usd", "direction", "remaining_secs",
    "reversal_prob", "atr_value", "natr_pct",
    "est_share_price", "max_entry_price",
    "shares_added", "shares_total", "tier_label",
    "action", "skip_reason", "order_id",
    "pnl", "mode",
]


class TradeLogger:
    """Logs every bot decision with full market context."""

    def __init__(
        self,
        log_dir:  str  = "output/logs",
        bot_name: str  = "bot",
        mode:     str  = "simulation",
        verbose:  bool = True,
        log_every_tick: bool = False,   # if False, only log trades/skips/window events
    ) -> None:
        self.bot_name       = bot_name
        self.mode           = mode
        self.verbose        = verbose
        self.log_every_tick = log_every_tick

        # State carried between calls
        self._window_start:  str | None = None
        self._window_close:  str | None = None
        self._open_price:    float = 0.0

        # CSV file
        log_path = Path(log_dir)
        log_path.mkdir(parents=True, exist_ok=True)
        ts_tag = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
        self._csv_path = log_path / f"{bot_name}_{mode}_{ts_tag}.csv"
        self._fh   = open(self._csv_path, "w", newline="", encoding="utf-8")
        self._writer = csv.DictWriter(self._fh, fieldnames=_FIELDS, extrasaction="ignore")
        self._writer.writeheader()
        self._fh.flush()

        if verbose:
            print(f"  [logger] Logging to {self._csv_path}")

    # ── Window lifecycle ──────────────────────────────────────────────────────

    def open_window(
        self,
        boundary:   datetime,
        window_end: datetime,
        open_price: float,
    ) -> None:
        self._window_start = boundary.isoformat()
        self._window_close = window_end.isoformat()
        self._open_price   = open_price
        self._write(
            event_type="WINDOW_OPEN",
            btc_price=open_price, open_price=open_price,
            delta_usd=0.0, direction="-", remaining_secs=300,
            action="-",
        )
        if self.verbose:
            print(f"  [log] Window open  {boundary.strftime('%H:%M:%S')} UTC  "
                  f"open=${open_price:,.2f}")

    def close_window(
        self,
        close_price: float,
        winner:      str,
        pnl:         float = 0.0,
    ) -> None:
        delta = close_price - self._open_price
        self._write(
            event_type="WINDOW_CLOSE",
            btc_price=close_price, open_price=self._open_price,
            delta_usd=delta, direction=winner, remaining_secs=0,
            action="-", pnl=pnl,
        )
        if self.verbose:
            sign = "+" if delta >= 0 else ""
            pnl_sign = "+" if pnl >= 0 else ""
            print(f"  [log] Window close {self._window_close[11:19] if self._window_close else '?'} UTC  "  # type: ignore[index]
                  f"close=${close_price:,.2f} Δ{sign}{delta:.2f}  "
                  f"winner={winner}  P&L={pnl_sign}{pnl:.3f}")

    # ── Decisions ─────────────────────────────────────────────────────────────

    def log_tick(
        self,
        btc_price:     float,
        delta:         float,
        direction:     str,
        prob:          float,
        remaining:     int,
        atr_value:     float | None = None,
        natr_pct:      float | None = None,
    ) -> None:
        if not self.log_every_tick:
            return
        self._write(
            event_type="TICK",
            btc_price=btc_price, delta_usd=delta, direction=direction,
            remaining_secs=remaining, reversal_prob=prob,
            atr_value=atr_value, natr_pct=natr_pct,
            action="-",
        )

    def log_trade(
        self,
        direction:       str,
        shares_added:    int,
        shares_total:    int,
        btc_price:       float,
        delta:           float,
        prob:            float,
        remaining:       int,
        est_share_price: float,
        max_entry_price: float | None,
        tier_label:      str = "",
        atr_value:       float | None = None,
        natr_pct:        float | None = None,
        order_id:        str = "",
    ) -> None:
        self._write(
            event_type="TRADE",
            btc_price=btc_price, delta_usd=delta, direction=direction,
            remaining_secs=remaining, reversal_prob=prob,
            atr_value=atr_value, natr_pct=natr_pct,
            est_share_price=est_share_price, max_entry_price=max_entry_price,
            shares_added=shares_added, shares_total=shares_total,
            tier_label=tier_label, action="TRADE",
            order_id=order_id,
        )
        if self.verbose:
            print(f"  [log] TRADE  {direction} +{shares_added} (total {shares_total})  "
                  f"P(rev)={prob:.3f}  share=${est_share_price:.3f}  "
                  f"Δ{delta:+.2f}  {remaining}s left")

    def log_skip(
        self,
        reason:          str,
        direction:       str = "-",
        btc_price:       float = 0.0,
        delta:           float = 0.0,
        prob:            float = 0.0,
        remaining:       int = 0,
        est_share_price: float | None = None,
        max_entry_price: float | None = None,
        atr_value:       float | None = None,
        natr_pct:        float | None = None,
    ) -> None:
        self._write(
            event_type="SKIP",
            btc_price=btc_price, delta_usd=delta, direction=direction,
            remaining_secs=remaining, reversal_prob=prob,
            atr_value=atr_value, natr_pct=natr_pct,
            est_share_price=est_share_price, max_entry_price=max_entry_price,
            action="SKIP", skip_reason=reason,
        )
        if self.verbose:
            print(f"  [log] SKIP   {direction}  reason={reason}  "
                  f"P(rev)={prob:.3f}  Δ{delta:+.2f}")

    def log_info(self, message: str) -> None:
        self._write(event_type="INFO", skip_reason=message, action="-")
        if self.verbose:
            print(f"  [log] INFO   {message}")

    def log_error(self, message: str) -> None:
        self._write(event_type="ERROR", skip_reason=message, action="-")
        print(f"  [log] ERROR  {message}")

    # ── Lifecycle ─────────────────────────────────────────────────────────────

    def close(self) -> None:
        self._fh.flush()
        self._fh.close()
        if self.verbose:
            print(f"  [logger] Closed → {self._csv_path}")

    @property
    def path(self) -> Path:
        return self._csv_path

    # ── Internal ──────────────────────────────────────────────────────────────

    def _write(self, event_type: str, **kwargs) -> None:
        row = {f: "" for f in _FIELDS}
        row.update({
            "timestamp":    datetime.now(timezone.utc).isoformat(),
            "window_start": self._window_start or "",
            "window_close": self._window_close or "",
            "event_type":   event_type,
            "open_price":   self._open_price,
            "mode":         self.mode,
        })
        for k, v in kwargs.items():
            if k in row and v is not None:
                row[k] = v
        self._writer.writerow(row)
        self._fh.flush()
