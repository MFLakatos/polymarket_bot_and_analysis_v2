"""
BTC 5-Minute Reversal Bot
==========================
Extends BaseBot. All window timing, logging, CLOB, and trade gates
are handled by the base class.

This bot adds:
  - ReversalModel query to get P(reversal)
  - Tier top-up logic
  - Per-window P&L summary display

Config (config/btc_5m_bot.yaml):
  trading.max_entry_price  : skip if estimated share price > this (e.g. 0.72)
  trading.tiers            : [{max_reversal_prob, shares}, ...]
  general.max_natr         : skip if NATR% > this value (null = disabled)
  time_filter.*            : weekend/NYSE/session gates
  trading.mode             : simulation | live
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from base_bot.base_bot import BaseBot, BotConfig, TradeRecord


class BTC5mReversalBot(BaseBot):
    """Polymarket BTC Up/Down 5m bot using the reversal probability model."""

    def __init__(self, config_path: str = "config/btc_5m_bot.yaml") -> None:
        # ── Load raw config ───────────────────────────────────────────────────
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        with open(p) as f:
            self._raw: dict[str, Any] = yaml.safe_load(f) or {}

        trading = self._raw.get("trading", {})
        general = self._raw.get("general", {})
        time_filter_cfg = self._raw.get("time_filter", {})

        # Build BotConfig from the yaml sections
        cfg = BotConfig(
            name            = "btc_5m_reversal",
            coin            = "BTC",
            timeframe       = "1s",
            mode            = "live" if trading.get("enabled", False) else "simulation",
            max_entry_price = float(trading.get("max_entry_price", 1.0)),
            atr_period      = int(general.get("atr_period", 14)),
            max_natr        = _opt_float(general.get("max_natr")),
            poll_interval   = float(general.get("poll_interval_seconds", 2.0)),
            min_remaining   = int(trading.get("min_seconds_remaining", 30)),
            time_filter     = time_filter_cfg,
            clob            = self._raw.get("clob", {}),
            log_dir         = general.get("log_dir", "output/logs"),
            log_every_tick  = bool(general.get("log_every_tick", False)),
            verbose         = self._raw.get("display", {}).get("verbose", True),
        )
        super().__init__(cfg)

        # Tiers sorted ascending by threshold
        self._tiers = sorted(
            trading.get("tiers", []),
            key=lambda x: x["max_reversal_prob"],
        )

        # Load reversal model
        dataset = general.get(
            "reversal_dataset_path",
            "data/crypto/BTC/reversal_dataset.parquet",
        )
        from btc_reversal_model import ReversalModel
        print("Loading reversal model...")
        self._model = ReversalModel(
            dataset_path=dataset,
            delta_bw=float(general.get("delta_bandwidth_usd", 50.0)),
            time_bw=float(general.get("time_bandwidth_seconds", 30.0)),
        )
        print(
            f"  base rate={self._model.base_rate:.3f}  "
            f"({self._model.base_rate*100:.0f}% of training seconds saw a reversal)\n"
        )

        # ── Print startup banner ──────────────────────────────────────────────
        mode_str  = "LIVE" if cfg.mode == "live" else "MONITOR/SIM"
        tf_desc   = self.time_filter.describe()
        entry_str = (f"${cfg.max_entry_price:.4f}"
                     if cfg.max_entry_price < 1.0 else "none")
        atr_str   = (f"{cfg.max_natr:.2f}%" if cfg.max_natr else "none")

        print(f"{'═'*68}")
        print(f"  BTC 5m Reversal Bot  —  {mode_str}")
        print(f"{'═'*68}")
        print(f"  Max entry price : {entry_str}")
        print(f"  Max NATR        : {atr_str}")
        print(f"  Time filter     : {tf_desc}")
        print(f"  Poll interval   : {cfg.poll_interval}s")
        print(f"  Tiers:")
        for tier in self._tiers:
            print(f"    P(rev) < {tier['max_reversal_prob']:.2f}  →  "
                  f"target {tier['shares']} shares total")
        print()

    # ── Lifecycle hooks ───────────────────────────────────────────────────────

    def on_window_open(self, open_price: float, boundary: datetime) -> None:
        print(f"\n{'═'*68}")
        print(f"  Window {len(self._results)+1}  ──  "
              f"{boundary.strftime('%H:%M:%S')} → "
              f"{(boundary.replace(second=0,microsecond=0).__class__.__call__(boundary.timestamp()+300)).__class__.__name__}"  # noqa
        )
        # simpler:
        from datetime import timedelta
        close_str = (boundary + timedelta(seconds=300)).strftime("%H:%M:%S")
        print(f"  Window {len(self._results)+1}  ──  "
              f"{boundary.strftime('%H:%M:%S')} → {close_str} UTC")
        print(f"  Open price : ${open_price:,.2f}")
        print(f"{'─'*68}")

    def on_tick(self, current_price: float, delta: float, remaining: int) -> None:
        direction = "UP" if delta >= 0 else "DOWN"
        prob      = self._model.probability_from_delta(delta, remaining)

        # ATR info (optional — if we have warmed-up data)
        atr_val  = self.atr_filter.atr
        natr_pct = self.atr_filter.natr(current_price) if atr_val else None

        # Display line
        from bot_tools.time_utils.window_manager import _now
        dc     = "\033[92m" if delta >= 0 else "\033[91m"
        R      = "\033[0m"
        pos    = {d: n for d, n in self._shares_bought.items() if n > 0}
        pos_str = f"  \033[2m[{pos}]\033[0m" if pos else ""
        print(
            f"  [{_now().strftime('%H:%M:%S')}] ${current_price:>10,.2f} | "
            f"Δ {dc}{delta:>+8.2f}{R} ({direction}) | "
            f"{remaining:>3}s | P(rev)={prob:.3f}{pos_str}          ",
            end="\r",
        )

        # Tier top-up logic
        target_shares, tier_label = self._find_tier(prob)
        if target_shares == 0:
            return
        already = self._shares_bought.get(direction, 0)
        if target_shares <= already:
            return
        shares_to_add = target_shares - already

        print()   # clear \r before trade message
        self.maybe_trade(
            direction       = direction,
            current_price   = current_price,
            prob            = prob,
            remaining       = remaining,
            shares_to_add   = shares_to_add,
            tier_label      = tier_label,
            atr_high        = atr_val,
            natr_pct        = natr_pct,
        )

    def on_window_close(self, close_price: float, winner: str, pnl: float) -> None:
        print()  # clear \r if needed
        trades = self._window_trades

        wc  = "\033[92m" if winner == "UP"  else "\033[91m"
        pc  = "\033[92m" if pnl >= 0       else "\033[91m"
        R   = "\033[0m"

        delta = close_price - self._open_price
        dc    = "\033[92m" if delta >= 0 else "\033[91m"

        print(f"\n  {'─'*70}")
        print(f"  Close {self.window_mgr.window_end.strftime('%H:%M:%S')} UTC  "
              f"close=${close_price:,.2f}  {dc}Δ{delta:+.2f}{R}  "
              f"winner={wc}{winner}{R}  "
              f"P&L={pc}{pnl:+.3f}{R}")

        if trades:
            print(f"\n  {'Time':>10}  {'Dir':>5}  {'+Shr':>4}  {'Tot':>3}  "
                  f"{'BTC':>10}  {'P(rev)':>7}  {'Shr$':>6}  {'Cost':>7}  "
                  f"{'P&L':>8}  Result")
            sep = "  " + "─"*73
            print(sep)
            for t in trades:
                tpnl = t.pnl(winner)
                dc2  = "\033[92m" if t.direction == "UP" else "\033[91m"
                tc   = "\033[92m" if tpnl >= 0 else "\033[91m"
                res  = "\033[92mWIN\033[0m" if t.direction == winner else "\033[91mLOSS\033[0m"
                print(
                    f"  {t.time_utc:>10}  {dc2}{t.direction:>5}{R}  "
                    f"{t.shares_added:>+4}  {t.shares_total:>3}  "
                    f"${t.btc_price:>9,.2f}  {t.prob:>7.3f}  "
                    f"{t.est_share_price:>6.3f}  ${t.est_cost:>6.3f}  "
                    f"{tc}{tpnl:>+8.3f}{R}  {res}"
                )
            invested = sum(t.est_cost for t in trades)
            print(sep)
            print(f"  {'TOTAL':>10}  {'':>5}  {'':>4}  {'':>3}  "
                  f"{'':>10}  {'':>7}  {'':>6}  ${invested:>6.3f}  "
                  f"{pc}{pnl:>+8.3f}{R}")
        else:
            print(f"  No trades placed this window.")
        print(f"  {'─'*70}")

    # ── Tier helper ───────────────────────────────────────────────────────────

    def _find_tier(self, prob: float) -> tuple[int, str]:
        """Return (target_shares, label) for the applicable tier. (0,'') if none."""
        for tier in self._tiers:
            if prob < tier["max_reversal_prob"]:
                label = f"tier_{tier['max_reversal_prob']}x{tier['shares']}"
                return int(tier["shares"]), label
        return 0, ""


def _opt_float(v) -> float | None:
    try:
        return float(v) if v is not None else None
    except (TypeError, ValueError):
        return None
