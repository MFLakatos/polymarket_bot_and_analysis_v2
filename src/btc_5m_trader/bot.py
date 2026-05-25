"""
BTC 5-Minute Reversal Bot
==========================

TIER LOGIC (correct behaviour):
  Tiers define a maximum TARGET share count per probability level.
  The bot tracks how many shares have been bought per direction this window.
  Each poll it finds the applicable tier (lowest threshold P(rev) qualifies for)
  and tops-up to that tier's share count if not already there.

  Example with tiers [{max=0.3, shares=1}, {max=0.2, shares=2}, {max=0.1, shares=3}]:

    P(rev)=0.288  → tier {max=0.3, shares=1} → have 0 UP → buy 1 UP   (total: 1)
    P(rev)=0.036  → tier {max=0.1, shares=3} → have 1 UP → buy 2 more (total: 3)
    P(rev)=0.036  → tier {max=0.1, shares=3} → have 3 UP → no action  (already maxed)
    P(rev)=0.450  → above all tiers           → no action

  For the opposite direction (DOWN), the same top-up logic applies independently.

WINDOW BOUNDARY (fixed):
  boundary = start_of_hour + next_300s_multiple  (computed from UTC hour start)
  window_end = boundary + 300s
  Loop exits when now() >= window_end — never bleeds into the next window.

P&L:
  bet direction == winner  →  +shares × (1 − est_share_price)
  bet direction != winner  →  −shares × est_share_price
  est_share_price = 1 − P(reversal) at time of purchase
"""
from __future__ import annotations

import signal
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import requests
import yaml


class C:
    R   = "\033[0m";  B = "\033[1m";  DIM = "\033[2m"
    G   = "\033[92m"; RED = "\033[91m"; Y = "\033[93m"; CYN = "\033[96m"


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ── Boundary helpers ──────────────────────────────────────────────────────────

def _next_boundary() -> tuple[datetime, float]:
    """
    Return (boundary_utc, seconds_to_wait).

    boundary is always at an exact :00/:05/:10/…/:55 mark, computed as
      start_of_current_hour  +  next multiple of 300s

    Computing from start_of_hour avoids the sub-second rounding errors
    that caused the previous implementation to show wrong close times.
    """
    now = _now()
    start_of_hour = now.replace(minute=0, second=0, microsecond=0)
    elapsed = (now - start_of_hour).total_seconds()
    next_b  = (int(elapsed / 300) + 1) * 300          # e.g. 2700 = 45:00
    boundary = start_of_hour + timedelta(seconds=next_b)
    wait     = next_b - elapsed
    return boundary, wait


def _btc(symbol: str = "BTCUSDT", timeout: int = 5) -> float | None:
    try:
        r = requests.get(
            "https://api.binance.com/api/v3/ticker/price",
            params={"symbol": symbol}, timeout=timeout,
        )
        if r.ok:
            return float(r.json()["price"])
    except Exception:
        pass
    return None


def _btc_retry(attempts: int = 5, delay: float = 0.4) -> float | None:
    for _ in range(attempts):
        p = _btc()
        if p is not None:
            return p
        time.sleep(delay)
    return None


# ── Data classes ──────────────────────────────────────────────────────────────

@dataclass
class WindowTrade:
    time_utc:        str
    direction:       str    # "UP" or "DOWN"
    shares_added:    int    # incremental shares bought in THIS order
    shares_total:    int    # cumulative shares for this direction after this buy
    btc_at_trade:    float
    delta_at_trade:  float
    reversal_prob:   float
    remaining_secs:  int
    est_share_price: float  # 1 − P(reversal)
    est_usdc_cost:   float  # shares_added × est_share_price


@dataclass
class WindowResult:
    window_num:   int
    open_time:    str    # ISO UTC
    close_time:   str    # ISO UTC
    open_price:   float
    close_price:  float
    delta_final:  float
    winner:       str    # "UP" or "DOWN"
    trades:       list[WindowTrade] = field(default_factory=list)

    def _trade_pnl(self, t: WindowTrade) -> float:
        if t.direction == self.winner:
            return  t.shares_added * (1.0 - t.est_share_price)
        else:
            return -t.shares_added * t.est_share_price

    @property
    def pnl(self) -> float:
        return sum(self._trade_pnl(t) for t in self.trades)

    @property
    def total_invested(self) -> float:
        return sum(t.est_usdc_cost for t in self.trades)

    def print_summary(self) -> None:
        wc  = C.G   if self.winner == "UP"   else C.RED
        dc  = C.G   if self.delta_final >= 0 else C.RED
        pnl = self.pnl
        pc  = C.G   if pnl >= 0             else C.RED

        print(f"\n  {'─'*70}")
        print(f"  {C.B}Window {self.window_num} result{C.R}")
        print(f"  Open  {self.open_time[11:19]} UTC  →  Close {self.close_time[11:19]} UTC")
        print(f"  Open  price : {C.B}${self.open_price:>10,.2f}{C.R}")
        print(f"  Close price : {C.B}${self.close_price:>10,.2f}{C.R}  "
              f"({dc}Δ {self.delta_final:+.2f}{C.R})")
        print(f"  Winner      : {wc}{C.B}{self.winner}{C.R}")

        if not self.trades:
            print(f"  No trades placed.")
            print(f"  {'─'*70}")
            return

        print(f"\n  {'Time':>10}  {'Dir':>5}  {'+Shr':>4}  {'Tot':>3}  "
              f"{'BTC $':>10}  {'P(rev)':>7}  {'ShareP':>7}  {'Cost':>7}  "
              f"{'P&L':>8}  Result")
        sep = "  " + "─"*75
        print(sep)

        for t in self.trades:
            won  = t.direction == self.winner
            tpnl = self._trade_pnl(t)
            dc2  = C.G   if t.direction == "UP" else C.RED
            tc   = C.G   if tpnl >= 0           else C.RED
            res  = f"{C.G}WIN{C.R}"              if won else f"{C.RED}LOSS{C.R}"
            print(
                f"  {t.time_utc:>10}  "
                f"{dc2}{t.direction:>5}{C.R}  "
                f"{t.shares_added:>+4}  "           # incremental shares
                f"{t.shares_total:>3}  "             # running total
                f"${t.btc_at_trade:>9,.2f}  "
                f"{t.reversal_prob:>7.3f}  "
                f"{t.est_share_price:>7.3f}  "
                f"${t.est_usdc_cost:>6.3f}  "
                f"{tc}{tpnl:>+8.3f}{C.R}  "
                f"{res}"
            )

        print(sep)
        print(f"  {'TOTAL':>10}  {'':>5}  {'':>4}  {'':>3}  "
              f"{'':>10}  {'':>7}  {'':>7}  "
              f"${self.total_invested:>6.3f}  "
              f"{pc}{pnl:>+8.3f}{C.R}")
        print(f"  {'─'*70}")


# ── Bot ───────────────────────────────────────────────────────────────────────

class BTC5mBot:

    def __init__(self, config_path: str = "config/btc_5m_bot.yaml") -> None:
        p = Path(config_path)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        with open(p) as f:
            self.cfg: dict[str, Any] = yaml.safe_load(f) or {}

        self._general = self.cfg.get("general", {})
        self._trading = self.cfg.get("trading", {})
        self._display = self.cfg.get("display", {})
        # Sort tiers ascending by threshold so we evaluate lowest first
        self._tiers   = sorted(
            self._trading.get("tiers", []),
            key=lambda x: x["max_reversal_prob"]
        )
        self._poll    = float(self._general.get("poll_interval_seconds", 2.0))
        self._enabled = bool(self._trading.get("enabled", False))
        self._running = False

        dataset = self._general.get(
            "reversal_dataset_path",
            "data/crypto/BTC/reversal_dataset.parquet",
        )
        from btc_reversal_model import ReversalModel
        print("Loading reversal model...")
        self._model = ReversalModel(
            dataset_path=dataset,
            delta_bw=float(self._general.get("delta_bandwidth_usd", 50.0)),
            time_bw=float(self._general.get("time_bandwidth_seconds", 30.0)),
        )

        self._clob: Any = None
        if self._enabled:
            self._init_clob()

        self._results: list[WindowResult] = []

    # ── CLOB init ─────────────────────────────────────────────────────────────

    def _init_clob(self) -> None:
        import os
        try:
            from py_clob_client_v2 import ClobClient
        except ImportError:
            print(f"{C.RED}ERROR: py_clob_client_v2 required.{C.R}"); sys.exit(1)
        key = os.environ.get("POLYMARKET_PRIVATE_KEY", "")
        if not key:
            print(f"{C.RED}ERROR: POLYMARKET_PRIVATE_KEY required.{C.R}"); sys.exit(1)
        sig  = int(self._trading.get("signature_type", 0))
        fund = os.environ.get("POLYMARKET_FUNDER_ADDRESS", "") or None
        self._clob = ClobClient(
            host="https://clob.polymarket.com", chain_id=137,
            key=key, funder=fund, signature_type=sig,
        )
        creds = self._clob.create_or_derive_api_key()
        if creds:
            from py_clob_client_v2 import ClobClient as CC
            self._clob = CC(
                host="https://clob.polymarket.com", chain_id=137,
                key=key, funder=fund, signature_type=sig, creds=creds,
            )
        print(f"{C.G}✓ CLOB API connected{C.R}")

    # ── Run ───────────────────────────────────────────────────────────────────

    def run(self) -> None:
        signal.signal(signal.SIGINT,  lambda *_: setattr(self, "_running", False))
        signal.signal(signal.SIGTERM, lambda *_: setattr(self, "_running", False))
        self._running = True

        mode = "LIVE TRADING" if self._enabled else "MONITOR ONLY"
        br   = self._model.base_rate

        print(f"\n{C.B}{C.CYN}{'═'*68}{C.R}")
        print(f"{C.B}  BTC 5m Reversal Bot — {mode}{C.R}")
        print(f"{C.B}{'═'*68}{C.R}\n")
        print(f"  Poll interval  : {self._poll}s")
        print(f"  Model base rate: {br:.3f}  "
              f"({C.DIM}unconditional P(reversal) = {br*100:.1f}%{C.R})")
        print(f"  {C.DIM}(In {br*100:.0f}% of 5-min windows BTC reversed at some point.){C.R}")
        print(f"\n  Tiers (ascending threshold):")
        for tier in self._tiers:
            print(f"    P(rev) < {tier['max_reversal_prob']:.2f}  →  "
                  f"target {tier['shares']} shares total for that direction")
        print()

        while self._running:
            try:
                self._run_one_window()
            except KeyboardInterrupt:
                break
            except Exception as e:
                print(f"\n{C.RED}[ERROR] {e}{C.R}")
                time.sleep(5)

        self._print_session_summary()

    # ── One window ────────────────────────────────────────────────────────────

    def _run_one_window(self) -> None:
        boundary, wait = _next_boundary()
        print(f"\n{C.DIM}  Next window in {wait:.1f}s "
              f"(boundary {boundary.strftime('%H:%M:%S')} UTC){C.R}")
        time.sleep(max(0.0, wait - 0.3))

        # ── Capture open price ────────────────────────────────────────────────
        open_price = _btc_retry(attempts=5)
        if open_price is None:
            print(f"{C.Y}  ⚠  Could not fetch open price — skipping window{C.R}")
            time.sleep(300)
            return

        window_open_dt = _now()
        window_end_dt  = boundary + timedelta(seconds=300)
        win_num        = len(self._results) + 1
        min_remaining  = int(self._trading.get("min_seconds_remaining", 30))

        print(f"\n{C.B}{'═'*68}{C.R}")
        print(f"{C.B}  Window {win_num}  ──  {window_open_dt.strftime('%H:%M:%S')} UTC  "
              f"→  closes {window_end_dt.strftime('%H:%M:%S')} UTC{C.R}")
        print(f"  Open price : {C.B}${open_price:,.2f}{C.R}")
        print(f"{C.B}{'─'*68}{C.R}")

        # Per-window accumulator:
        #   shares_bought[direction] = how many shares we hold so far this window
        shares_bought: dict[str, int]      = {"UP": 0, "DOWN": 0}
        window_trades: list[WindowTrade]   = []
        last_price:    float               = open_price

        # ── Polling loop ──────────────────────────────────────────────────────
        while self._running:
            now = _now()
            remaining = int((window_end_dt - now).total_seconds())
            if remaining <= 0:
                break

            current = _btc()
            if current is None:
                time.sleep(self._poll)
                continue
            last_price = current

            delta     = current - open_price
            prob      = self._model.probability_from_delta(delta, remaining)
            direction = "UP" if delta >= 0 else "DOWN"
            dc        = C.G if delta >= 0 else C.RED

            # Build position status string
            pos_parts = []
            for d in ("UP", "DOWN"):
                if shares_bought[d] > 0:
                    pos_parts.append(f"{d}:{shares_bought[d]}")
            pos_str = (f"  {C.DIM}[pos: {', '.join(pos_parts)}]{C.R}"
                       if pos_parts else "")

            if self._display.get("enabled", True):
                ts = now.strftime("%H:%M:%S")
                print(
                    f"  [{ts}] ${current:>10,.2f} | "
                    f"Δ {dc}{delta:>+8.2f}{C.R} ({direction}) | "
                    f"{remaining:>3}s left | "
                    f"P(rev)={C.Y}{prob:.3f}{C.R}{pos_str}          ",
                    end="\r",
                )

            # ── Tier top-up logic ─────────────────────────────────────────────
            if remaining > min_remaining:
                trade = self._evaluate_tiers(
                    direction, current, open_price, prob, remaining,
                    already_bought=shares_bought[direction],
                )
                if trade is not None:
                    shares_bought[direction] = trade.shares_total
                    window_trades.append(trade)

            time.sleep(self._poll)

        print()   # clear \r line

        # ── Fetch close price ─────────────────────────────────────────────────
        overshoot = (_now() - window_end_dt).total_seconds()
        if overshoot < 0:
            time.sleep(abs(overshoot) + 0.5)

        close_price = _btc_retry(attempts=8, delay=0.5) or last_price
        delta_final = close_price - open_price
        winner      = "UP" if delta_final > 0 else "DOWN"

        result = WindowResult(
            window_num  = win_num,
            open_time   = window_open_dt.isoformat(),
            close_time  = window_end_dt.isoformat(),
            open_price  = open_price,
            close_price = close_price,
            delta_final = delta_final,
            winner      = winner,
            trades      = window_trades,
        )
        self._results.append(result)
        result.print_summary()

    # ── Tier top-up logic ─────────────────────────────────────────────────────

    def _evaluate_tiers(
        self,
        direction: str,
        btc_price: float,
        open_price: float,
        prob: float,
        remaining: int,
        already_bought: int,
    ) -> WindowTrade | None:
        """
        Find the applicable tier for this prob level.
        If that tier's target share count > already_bought, buy the difference.
        Otherwise no action (already at or above this tier).

        Tiers are sorted ascending by max_reversal_prob, so the first matching
        tier is the MOST AGGRESSIVE one (lowest threshold = highest confidence).
        """
        target_shares = 0
        for tier in self._tiers:
            if prob < tier["max_reversal_prob"]:
                target_shares = int(tier["shares"])
                break           # first match = lowest threshold that qualifies

        if target_shares == 0 or target_shares <= already_bought:
            return None         # no new tier firing, or already maxed

        shares_to_add   = target_shares - already_bought
        est_share_price = round(max(0.51, min(0.99, 1.0 - prob)), 4)
        est_cost        = round(shares_to_add * est_share_price, 4)

        ts = _now().strftime("%H:%M:%S")
        dc = C.G if direction == "UP" else C.RED

        print(
            f"\n  {C.B}[{ts}] TRADE  "
            f"BUY {shares_to_add}× {dc}{direction}{C.R}  "
            f"(+{shares_to_add} → total {target_shares})  "
            f"P(rev)={prob:.3f}  "
            f"est. share=${est_share_price:.3f}  "
            f"est. cost=${est_cost:.3f}  "
            f"({remaining}s left){C.R}"
        )
        if self._enabled and self._clob is not None:
            print(f"  {C.Y}⚠  Live order requires market token_id{C.R}")
        else:
            print(f"  {C.DIM}(simulation){C.R}")

        return WindowTrade(
            time_utc        = ts,
            direction       = direction,
            shares_added    = shares_to_add,
            shares_total    = target_shares,
            btc_at_trade    = btc_price,
            delta_at_trade  = btc_price - open_price,
            reversal_prob   = prob,
            remaining_secs  = remaining,
            est_share_price = est_share_price,
            est_usdc_cost   = est_cost,
        )

    # ── Session summary ───────────────────────────────────────────────────────

    def _print_session_summary(self) -> None:
        results    = self._results
        all_trades = [t for r in results for t in r.trades]

        print(f"\n{C.B}{'═'*68}{C.R}")
        print(f"{C.B}  SESSION SUMMARY{C.R}")
        print(f"{'═'*68}")
        print(f"  Windows completed : {len(results)}")
        print(f"  Total orders      : {len(all_trades)}")

        if not results:
            print(f"{'═'*68}\n")
            return

        total_invested = sum(r.total_invested for r in results)
        total_pnl      = sum(r.pnl for r in results)
        wins           = sum(1 for r in results if r.pnl > 0)
        losses         = sum(1 for r in results if r.pnl < 0)
        no_trade       = sum(1 for r in results if not r.trades)
        pnl_c          = C.G if total_pnl >= 0 else C.RED

        print(f"  Total invested    : ${total_invested:.3f}")
        print(f"  Total P&L         : {pnl_c}{total_pnl:>+.3f}{C.R}")
        print(f"  Profitable windows: {wins}")
        print(f"  Loss windows      : {losses}")
        if no_trade:
            print(f"  No-trade windows  : {no_trade}")
        print()

        hdr = (f"  {'#':>3}  {'Open':>8}  {'Close':>8}  "
               f"{'Open BTC':>10}  {'Close BTC':>10}  "
               f"{'Δ':>8}  {'Win':>4}  {'Ord':>3}  {'P&L':>9}")
        sep = "  " + "─"*78
        print(hdr)
        print(sep)

        for r in results:
            wc = C.G   if r.winner == "UP"   else C.RED
            dc = C.G   if r.delta_final >= 0 else C.RED
            pc = C.G   if r.pnl >= 0         else C.RED
            print(
                f"  {r.window_num:>3}  "
                f"{r.open_time[11:19]:>8}  "
                f"{r.close_time[11:19]:>8}  "
                f"${r.open_price:>9,.2f}  "
                f"${r.close_price:>9,.2f}  "
                f"{dc}{r.delta_final:>+8.2f}{C.R}  "
                f"{wc}{r.winner:>4}{C.R}  "
                f"{len(r.trades):>3}  "
                f"{pc}{r.pnl:>+9.3f}{C.R}"
            )

        print(sep)
        pc = C.G if total_pnl >= 0 else C.RED
        print(
            f"  {'':>3}  {'TOTAL':>8}  {'':>8}  "
            f"{'':>10}  {'':>10}  "
            f"{'':>8}  {'':>4}  {len(all_trades):>3}  "
            f"{pc}{total_pnl:>+9.3f}{C.R}"
        )
        print(f"{'═'*68}\n")