"""
BTC 5m Reversal Bot — Simulator
=================================
Replays historical detected_trades CSV logs through the reversal model
and produces a P&L report showing what would have happened.

Input:  data/copy_trading/detected_trades_*.csv  (or any path)
Output: console P&L table + output/simulation/sim_report.csv
"""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


SHARE_SIZE = 1.0  # default shares per simulated trade


def window_start_for(dt: datetime) -> datetime:
    floored = (dt.minute // 5) * 5
    return dt.replace(minute=floored, second=0, microsecond=0)


class SimulatedTrade:
    def __init__(
        self,
        window_start: str,
        secs_in_window: int,
        outcome: str,        # "up" or "down"
        price: float,        # implied probability paid
        shares: float,
        reversal_prob: float,
        decision: str,       # "BUY" or "SKIP"
    ):
        self.window_start    = window_start
        self.secs_in_window  = secs_in_window
        self.outcome         = outcome.lower()
        self.price           = price
        self.shares          = shares
        self.reversal_prob   = reversal_prob
        self.decision        = decision
        self.usdc_paid       = price * shares
        # Net returns (computed after we know the window result)
        self.return_if_up    = (shares - self.usdc_paid) if self.outcome == "up"   else -self.usdc_paid
        self.return_if_down  = (shares - self.usdc_paid) if self.outcome == "down" else -self.usdc_paid


class SimulationReport:
    def __init__(self):
        self.trades: list[SimulatedTrade] = []
        self.windows: dict[str, list[SimulatedTrade]] = defaultdict(list)

    def add(self, trade: SimulatedTrade) -> None:
        self.trades.append(trade)
        self.windows[trade.window_start].append(trade)

    def print_summary(self) -> None:
        buys = [t for t in self.trades if t.decision == "BUY"]
        skips = [t for t in self.trades if t.decision == "SKIP"]

        total_spent = sum(t.usdc_paid for t in buys)
        total_if_up   = sum(t.return_if_up   for t in buys)
        total_if_down = sum(t.return_if_down for t in buys)

        print("\n" + "═" * 70)
        print("  BTC 5m Reversal Bot — Simulation Report")
        print("═" * 70)
        print(f"  Total windows analysed : {len(self.windows)}")
        print(f"  Trades placed (BUY)    : {len(buys)}")
        print(f"  Trades skipped         : {len(skips)}")
        print(f"  Total USDC spent       : ${total_spent:.2f}")
        print(f"  Net P&L if ALL UP win  : ${total_if_up:+.2f}")
        print(f"  Net P&L if ALL DOWN win: ${total_if_down:+.2f}")
        print()
        print(f"{'Window':<22} {'Trades':>6} {'Spent':>8}  {'If UP':>8}  {'If DN':>8}  {'P(rev)avg':>10}")
        print("─" * 72)
        for win_start in sorted(self.windows.keys()):
            wt = [t for t in self.windows[win_start] if t.decision == "BUY"]
            if not wt:
                continue
            spent = sum(t.usdc_paid for t in wt)
            ret_up = sum(t.return_if_up for t in wt)
            ret_dn = sum(t.return_if_down for t in wt)
            avg_p  = sum(t.reversal_prob for t in wt) / len(wt)
            print(f"  {win_start[:16]:<20} {len(wt):>6}  ${spent:>6.2f}  "
                  f"{ret_up:>+8.2f}  {ret_dn:>+8.2f}  {avg_p:>9.3f}")
        print("─" * 72)
        print(f"  {'TOTAL':<20} {len(buys):>6}  ${total_spent:>6.2f}  "
              f"{total_if_up:>+8.2f}  {total_if_down:>+8.2f}")
        print("═" * 70)

    def save_csv(self, path: str | Path) -> None:
        out = Path(path)
        out.parent.mkdir(parents=True, exist_ok=True)
        fields = [
            "window_start", "secs_in_window", "outcome", "price", "shares",
            "usdc_paid", "reversal_prob", "decision",
            "return_if_up", "return_if_down",
        ]
        with open(out, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=fields)
            w.writeheader()
            for t in self.trades:
                w.writerow({
                    "window_start":   t.window_start,
                    "secs_in_window": t.secs_in_window,
                    "outcome":        t.outcome,
                    "price":          round(t.price, 6),
                    "shares":         t.shares,
                    "usdc_paid":      round(t.usdc_paid, 4),
                    "reversal_prob":  round(t.reversal_prob, 4),
                    "decision":       t.decision,
                    "return_if_up":   round(t.return_if_up, 4),
                    "return_if_down": round(t.return_if_down, 4),
                })
        print(f"  Simulation CSV saved → {out}")


def run_simulation(
    detected_trades_path: str | Path,
    model,                          # ReversalModel instance
    tiers: list[dict],              # [{"max_reversal_prob": float, "shares": int}]
    output_path: str | Path = "output/simulation/sim_report.csv",
    target_market_keyword: str = "5m",
) -> SimulationReport:
    """
    Replay detected trades through the reversal model.

    For each trade in the CSV:
      1. Compute delta_usd from price and outcome
      2. Query model.probability_from_delta(delta_usd, secs_left)
      3. Apply tier logic → BUY or SKIP
      4. Record in SimulationReport
    """
    report = SimulationReport()
    path = Path(detected_trades_path)
    if not path.exists():
        print(f"⚠ File not found: {path}")
        return report

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    # Filter to target market
    if target_market_keyword:
        rows = [r for r in rows if target_market_keyword.lower() in r.get("market_title", "").lower()]

    print(f"  Loaded {len(rows)} rows from {path.name}")

    for raw in rows:
        try:
            dt_open = datetime.fromisoformat(raw["time_open"].replace("Z", "+00:00"))
            if dt_open.tzinfo is None:
                dt_open = dt_open.replace(tzinfo=timezone.utc)
            win_start = window_start_for(dt_open)
            win_start_str = win_start.strftime("%Y-%m-%dT%H:%M:%S+00:00")
            secs_in_win = int((dt_open - win_start).total_seconds())

            outcome = raw.get("outcome", "").lower()
            up_price = float(raw.get("up_price") or 0.0)
            down_price = float(raw.get("down_price") or 0.0)
            price = up_price if outcome == "up" else down_price
            if price <= 0:
                price = float(raw.get("price") or 0.0)

            # Reconstruct delta_usd from the implied probability
            # If we bought UP at price p, delta was positive (BTC moved up)
            # We use: delta ~ (price - 0.5) * 200  as a rough linear mapping
            # The model is queried with remaining time
            secs_left = max(1, 300 - secs_in_win)

            # For simulation: use the price directly as P(win)
            # A price of 0.6 for UP means market thinks 60% chance UP wins
            # We infer direction and magnitude from the outcome/price pair
            if outcome == "up":
                delta_usd = (price - 0.5) * 400.0  # heuristic scale
            else:
                delta_usd = -(price - 0.5) * 400.0

            reversal_prob = model.probability_from_delta(delta_usd, secs_left)

            # Apply tiers
            decision = "SKIP"
            shares_to_buy = 0.0
            for tier in sorted(tiers, key=lambda x: x["max_reversal_prob"]):
                if reversal_prob < tier["max_reversal_prob"]:
                    decision = "BUY"
                    shares_to_buy = float(tier.get("shares", SHARE_SIZE))
                    break

            trade = SimulatedTrade(
                window_start=win_start_str,
                secs_in_window=secs_in_win,
                outcome=outcome,
                price=price,
                shares=shares_to_buy if decision == "BUY" else 0.0,
                reversal_prob=reversal_prob,
                decision=decision,
            )
            report.add(trade)

        except Exception as e:
            print(f"  Skipping row: {e}")

    report.print_summary()
    report.save_csv(output_path)
    return report
