"""
BaseBot
========
Abstract base class for all Polymarket trading bots.

Subclasses implement three lifecycle hooks:
  on_window_open(open_price, boundary)
  on_tick(current_price, delta, remaining)
  on_window_close(close_price, winner, pnl)

BaseBot handles everything else:
  - Window boundary sequencing (no skipped windows, no Ctrl+C freezing)
  - Price fetching with retries
  - TimeFilter gate (skips window if time gate is closed)
  - ATRFilter gate (skips individual trades if NATR too high)
  - MAX_ENTRY_PRICE gate (skips trade if est share price > cap)
  - TradeLogger wiring
  - ClobConnection (simulation or live)
  - Session summary on exit

Configuration is built with BotConfig, which can be loaded from a YAML
section or constructed programmatically.

Example::

    class MyBot(BaseBot):
        def on_window_open(self, open_price, boundary):
            self._open = open_price

        def on_tick(self, current_price, delta, remaining):
            prob = self.reversal_model.probability_from_delta(delta, remaining)
            direction = "UP" if delta >= 0 else "DOWN"
            self.maybe_trade(direction, current_price, prob, remaining)

        def on_window_close(self, close_price, winner, pnl):
            self.logger.log_info(f"Window closed: {winner}  P&L={pnl:+.3f}")

    cfg = BotConfig.from_yaml("config/btc_5m_bot.yaml")
    bot = MyBot(cfg)
    bot.run()
"""
from __future__ import annotations

import signal
import sys
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal, Optional

import yaml

from bot_tools.time_utils.window_manager import WindowManager
from bot_tools.time_utils.time_filter    import TimeFilter
from bot_tools.atr_filter.atr            import ATRFilter
from bot_tools.clob_connection.client    import ClobConnection, OrderResult
from bot_tools.logger.trade_logger       import TradeLogger


# ── Config ────────────────────────────────────────────────────────────────────

@dataclass
class BotConfig:
    """
    Unified configuration for any bot.
    Load from a YAML section or build programmatically.
    """
    # Identity
    name:            str   = "bot"
    coin:            str   = "BTC"
    timeframe:       str   = "1m"

    # Execution mode
    mode:            Literal["simulation", "live"] = "simulation"

    # Trade gate: skip if estimated share price > this value (0–1)
    max_entry_price: float = 1.0          # 1.0 = disabled

    # ATR gate
    atr_period:      int   = 14
    max_natr:        float | None = None  # None = disabled

    # Poll
    poll_interval:   float = 2.0
    min_remaining:   int   = 30

    # Time filter (mirrors TimeFilter.from_dict)
    time_filter:     dict = field(default_factory=dict)

    # CLOB config (ignored in simulation mode)
    clob:            dict = field(default_factory=dict)

    # Logger
    log_dir:         str  = "output/logs"
    log_every_tick:  bool = False
    verbose:         bool = True

    @classmethod
    def from_yaml(cls, path: str | Path, section: str | None = None) -> "BotConfig":
        """
        Load from YAML.  If `section` is given, reads cfg[section];
        otherwise reads the top-level keys directly.
        """
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        with open(p) as f:
            raw: dict = yaml.safe_load(f) or {}
        data = raw.get(section, raw) if section else raw
        return cls._from_dict(data)

    @classmethod
    def _from_dict(cls, d: dict) -> "BotConfig":
        trading = d.get("trading", {})
        general = d.get("general", d)
        return cls(
            name           = str(d.get("name", "bot")),
            coin           = str(d.get("coin", "BTC")),
            timeframe      = str(d.get("timeframe", "1m")),
            mode           = trading.get("mode", d.get("mode", "simulation")),
            max_entry_price= float(trading.get("max_entry_price",
                                               d.get("max_entry_price", 1.0))),
            atr_period     = int(d.get("atr_period", 14)),
            max_natr       = _opt_float(d.get("max_natr")),
            poll_interval  = float(general.get("poll_interval_seconds",
                                               d.get("poll_interval", 2.0))),
            min_remaining  = int(trading.get("min_seconds_remaining",
                                             d.get("min_remaining", 30))),
            time_filter    = d.get("time_filter", {}),
            clob           = d.get("clob", {}),
            log_dir        = str(d.get("log_dir", "output/logs")),
            log_every_tick = bool(d.get("log_every_tick", False)),
            verbose        = bool(d.get("verbose", True)),
        )


def _opt_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None


# ── Window trade record (shared between base and subclasses) ──────────────────

@dataclass
class TradeRecord:
    time_utc:        str
    direction:       str
    shares_added:    int
    shares_total:    int
    btc_price:       float
    delta:           float
    prob:            float
    remaining:       int
    est_share_price: float
    est_cost:        float
    order_result:    OrderResult | None = None

    def pnl(self, winner: str) -> float:
        if self.direction == winner:
            return  self.shares_added * (1.0 - self.est_share_price)
        return -self.shares_added * self.est_share_price


# ── BaseBot ───────────────────────────────────────────────────────────────────

class BaseBot(ABC):
    """
    Abstract base class.  Subclasses override the three lifecycle hooks.
    Call run() to start the bot loop.
    """

    def __init__(self, config: BotConfig) -> None:
        self.config = config
        self._running = False

        # ── Core tools ────────────────────────────────────────────────────────
        self.window_mgr  = WindowManager()
        self.time_filter = TimeFilter.from_dict(config.time_filter)
        self.atr_filter  = ATRFilter(
            period=config.atr_period,
            max_natr=config.max_natr,
        )
        self.clob = (
            ClobConnection.simulation() if config.mode == "simulation"
            else ClobConnection.from_dict(config.clob, mode="live")
        )
        self.logger = TradeLogger(
            log_dir       = config.log_dir,
            bot_name      = config.name,
            mode          = config.mode,
            verbose       = config.verbose,
            log_every_tick= config.log_every_tick,
        )

        # Per-window state (reset each window)
        self._shares_bought: dict[str, int] = {"UP": 0, "DOWN": 0}
        self._window_trades: list[TradeRecord] = []
        self._open_price:    float = 0.0
        self._last_price:    float = 0.0

        # Session accumulators
        self._results: list[dict] = []

    # ── Lifecycle hooks (subclass overrides) ─────────────────────────────────

    @abstractmethod
    def on_window_open(self, open_price: float, boundary: datetime) -> None:
        """Called once at window open with the captured BTC price."""
        ...

    @abstractmethod
    def on_tick(self, current_price: float, delta: float, remaining: int) -> None:
        """
        Called every poll_interval seconds during the window.
        Use self.maybe_trade() inside this method to place orders.
        """
        ...

    @abstractmethod
    def on_window_close(self, close_price: float, winner: str, pnl: float) -> None:
        """Called once at window close with the final BTC price."""
        ...

    # ── Trade helper (call from on_tick) ──────────────────────────────────────

    def maybe_trade(
        self,
        direction:       str,
        current_price:   float,
        prob:            float,
        remaining:       int,
        shares_to_add:   int = 1,
        tier_label:      str = "",
        atr_high:        float | None = None,
        natr_pct:        float | None = None,
    ) -> Optional[TradeRecord]:
        """
        Central trade gate.  Checks:
          1. max_entry_price — skip if est_share_price > cap
          2. ATR gate        — skip if NATR > max_natr
          3. TimeFilter      — skip if outside allowed sessions
          Then places or simulates the order.

        Returns TradeRecord if an order was placed, None if skipped.
        """
        delta           = current_price - self._open_price
        est_share_price = round(max(0.51, min(0.99, 1.0 - prob)), 4)

        # ── MAX_ENTRY_PRICE gate ──────────────────────────────────────────────
        if est_share_price > self.config.max_entry_price:
            reason = (f"price ${est_share_price:.4f} > max "
                      f"${self.config.max_entry_price:.4f}")
            self.logger.log_skip(
                reason, direction, current_price, delta, prob, remaining,
                est_share_price=est_share_price,
                max_entry_price=self.config.max_entry_price,
                atr_value=atr_high, natr_pct=natr_pct,
            )
            return None

        # ── ATR gate ──────────────────────────────────────────────────────────
        if atr_high is not None and natr_pct is not None:
            atr_ok, atr_reason = self.atr_filter.check(current_price)
            if not atr_ok:
                self.logger.log_skip(
                    f"ATR: {atr_reason}", direction, current_price, delta,
                    prob, remaining, est_share_price=est_share_price,
                    atr_value=atr_high, natr_pct=natr_pct,
                )
                return None

        # ── TimeFilter gate ───────────────────────────────────────────────────
        tf_ok, tf_reason = self.time_filter.check()
        if not tf_ok:
            self.logger.log_skip(
                f"time filter: {tf_reason}", direction, current_price, delta,
                prob, remaining, est_share_price=est_share_price,
            )
            return None

        # ── Place order ───────────────────────────────────────────────────────
        est_cost = round(shares_to_add * est_share_price, 4)
        ts       = datetime.now(timezone.utc).strftime("%H:%M:%S")

        # For live mode, token_id must be provided via subclass override
        token_id = getattr(self, "_token_id", "")
        result   = self.clob.buy(token_id, shares_to_add, est_share_price)

        new_total = self._shares_bought.get(direction, 0) + shares_to_add
        self._shares_bought[direction] = new_total

        record = TradeRecord(
            time_utc        = ts,
            direction       = direction,
            shares_added    = shares_to_add,
            shares_total    = new_total,
            btc_price       = current_price,
            delta           = delta,
            prob            = prob,
            remaining       = remaining,
            est_share_price = est_share_price,
            est_cost        = est_cost,
            order_result    = result,
        )
        self._window_trades.append(record)

        self.logger.log_trade(
            direction=direction, shares_added=shares_to_add,
            shares_total=new_total, btc_price=current_price,
            delta=delta, prob=prob, remaining=remaining,
            est_share_price=est_share_price,
            max_entry_price=self.config.max_entry_price,
            tier_label=tier_label,
            atr_value=atr_high, natr_pct=natr_pct,
            order_id=result.order_id if result else "",
        )
        return record

    # ── Main run loop ─────────────────────────────────────────────────────────

    def run(self) -> None:
        def _handle(*_):
            print(f"\n  Shutdown requested — finishing current window...")
            self._running = False
            self.window_mgr.stop()

        signal.signal(signal.SIGINT,  _handle)
        signal.signal(signal.SIGTERM, _handle)
        self._running = True

        mode_str = "LIVE" if self.config.mode == "live" else "SIMULATION"
        self.logger.log_info(f"Bot starting — mode={mode_str}  coin={self.config.coin}")

        self.window_mgr.start()

        while self._running:
            boundary   = self.window_mgr.boundary
            window_end = self.window_mgr.window_end

            # ── Check time filter at window level ─────────────────────────────
            tf_ok, tf_reason = self.time_filter.check(boundary)
            if not tf_ok:
                self.logger.log_info(f"Window skipped (time filter: {tf_reason})")
                self.window_mgr.advance()
                continue

            # ── Open price ────────────────────────────────────────────────────
            from bot_tools.time_utils.window_manager import _now
            import requests as _req
            open_price = self._fetch_price_retry()
            if open_price is None:
                self.logger.log_error(f"Could not fetch open price for {boundary.strftime('%H:%M:%S')}")
                self.window_mgr.advance()
                continue

            self._open_price    = open_price
            self._last_price    = open_price
            self._shares_bought = {"UP": 0, "DOWN": 0}
            self._window_trades = []

            self.logger.open_window(boundary, window_end, open_price)
            self.on_window_open(open_price, boundary)

            # ── Polling loop ──────────────────────────────────────────────────
            while self._running and self.window_mgr.remaining > 0:
                current = self._fetch_price()
                if current is None:
                    self.window_mgr.sleep(self.config.poll_interval)
                    continue
                self._last_price = current
                delta            = current - open_price

                if self.window_mgr.remaining > self.config.min_remaining:
                    self.on_tick(current, delta, self.window_mgr.remaining)

                self.window_mgr.sleep(self.config.poll_interval)

            # ── Close ─────────────────────────────────────────────────────────
            self.window_mgr.wait_for_close(extra=0.5)
            close_price = self._fetch_price_retry(attempts=8) or self._last_price
            delta_final = close_price - open_price
            winner      = "UP" if delta_final > 0 else "DOWN"
            pnl = sum(t.pnl(winner) for t in self._window_trades)

            self.logger.close_window(close_price, winner, pnl)
            self.on_window_close(close_price, winner, pnl)

            self._results.append({
                "boundary":    boundary.isoformat(),
                "open_price":  open_price,
                "close_price": close_price,
                "delta":       delta_final,
                "winner":      winner,
                "trades":      len(self._window_trades),
                "pnl":         pnl,
            })

            if self._running:
                self.window_mgr.advance()

        self.logger.log_info("Bot stopped")
        self._print_summary()
        self.logger.close()

    # ── Helpers ───────────────────────────────────────────────────────────────

    def _fetch_price(self) -> float | None:
        import requests
        try:
            r = requests.get(
                "https://api.binance.com/api/v3/ticker/price",
                params={"symbol": f"{self.config.coin}USDT"},
                timeout=5,
            )
            if r.ok:
                return float(r.json()["price"])
        except Exception:
            pass
        return None

    def _fetch_price_retry(self, attempts: int = 5) -> float | None:
        for _ in range(attempts):
            p = self._fetch_price()
            if p is not None:
                return p
            self.window_mgr.sleep(0.4)
        return None

    def _print_summary(self) -> None:
        if not self._results:
            return
        total_pnl = sum(r["pnl"] for r in self._results)
        print(f"\n{'═'*60}")
        print(f"  SESSION SUMMARY — {self.config.name}")
        print(f"{'═'*60}")
        print(f"  Mode     : {self.config.mode}")
        print(f"  Windows  : {len(self._results)}")
        print(f"  Total P&L: {'+' if total_pnl >= 0 else ''}{total_pnl:.3f}")
        print(f"  Log      : {self.logger.path}")
        print(f"{'═'*60}\n")
