"""
Wallet Trade Analyzer
======================
Reads a wallet's downloaded trade history from data/wallets/{wallet_id}/
and produces enriched CSVs and per-window aggregate statistics.

Supports two data sources:
  1. Copy trading bot CSV logs (detected_trades_*.csv)
  2. Raw Polymarket wallet download (trades.csv from wallet-download CLI)

Output files:
  output/wallet_analysis/{wallet_id}/trades_enriched.csv
  output/wallet_analysis/{wallet_id}/windows_summary.csv
  output/wallet_analysis/{wallet_id}/windows.json
"""
from __future__ import annotations

import csv
import json
import re
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

SHARE_SIZE = 1.0   # default shares per trade for return calculation


# ── Date helpers ──────────────────────────────────────────────────────────────

def parse_dt(s: str) -> datetime:
    s = s.strip().replace("Z", "+00:00")
    dt = datetime.fromisoformat(s)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def window_start_for(dt: datetime) -> datetime:
    """Floor a datetime to the nearest 5-minute boundary."""
    floored = (dt.minute // 5) * 5
    return dt.replace(minute=floored, second=0, microsecond=0)


# ── Return helpers ────────────────────────────────────────────────────────────

def compute_returns(outcome: str, usdc_amount: float, shares: float = SHARE_SIZE):
    """
    Compute (return_if_up, return_if_down) for a single trade.
    Each winning share pays $1.
    """
    outcome = outcome.strip().lower()
    if outcome == "up":
        return shares - usdc_amount, -usdc_amount
    else:
        return -usdc_amount, shares - usdc_amount


# ── Loaders ───────────────────────────────────────────────────────────────────

def load_bot_csv(path: Path) -> list[dict]:
    """Load detected_trades_*.csv from the copy trading bot."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            try:
                dt_open = parse_dt(raw["time_open"])
                win_start = window_start_for(dt_open)
                secs = int((dt_open - win_start).total_seconds())
                usdc = float(raw.get("usdc_amount") or 0)
                outcome = raw.get("outcome", "").strip()
                ret_up, ret_dn = compute_returns(outcome, usdc)
                rows.append({
                    "source":          "bot",
                    "market_title":    raw.get("market_title", "").strip(),
                    "window_start":    win_start.isoformat(),
                    "time_open":       raw["time_open"].strip(),
                    "secs_in_window":  secs,
                    "side":            raw.get("side", "").strip(),
                    "outcome":         outcome,
                    "shares":          float(raw.get("size") or SHARE_SIZE),
                    "usdc_paid":       usdc,
                    "price":           float(raw.get("price") or 0),
                    "up_price":        float(raw.get("up_price") or 0),
                    "down_price":      float(raw.get("down_price") or 0),
                    "return_if_up":    round(ret_up, 4),
                    "return_if_down":  round(ret_dn, 4),
                    "status":          raw.get("status", "").strip(),
                    "reason":          raw.get("reason", "").strip(),
                    "token_id":        raw.get("token_id", "").strip(),
                    "wallet_address":  raw.get("wallet_address", "").strip(),
                })
            except Exception as e:
                print(f"  Skipping row: {e}")
    return rows


def load_wallet_csv(path: Path) -> list[dict]:
    """Load trades.csv from the wallet-download CLI."""
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        for raw in csv.DictReader(f):
            try:
                # Try to parse timestamp
                ts_raw = raw.get("timestamp") or raw.get("createdAt") or ""
                if not ts_raw:
                    continue
                try:
                    dt_open = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
                except (ValueError, TypeError):
                    dt_open = parse_dt(str(ts_raw))

                win_start = window_start_for(dt_open)
                secs = int((dt_open - win_start).total_seconds())
                usdc = float(raw.get("size") or raw.get("usdcSize") or 0)
                outcome = raw.get("outcome", raw.get("outcomeIndex", "")).strip()
                price = float(raw.get("price") or 0)
                ret_up, ret_dn = compute_returns(outcome, usdc)

                rows.append({
                    "source":          "wallet",
                    "market_title":    raw.get("title", raw.get("market", "")).strip(),
                    "window_start":    win_start.isoformat(),
                    "time_open":       dt_open.isoformat(),
                    "secs_in_window":  secs,
                    "side":            raw.get("side", "").strip(),
                    "outcome":         outcome,
                    "shares":          SHARE_SIZE,
                    "usdc_paid":       usdc,
                    "price":           price,
                    "up_price":        price if outcome.lower() == "up" else 1 - price,
                    "down_price":      price if outcome.lower() == "down" else 1 - price,
                    "return_if_up":    round(ret_up, 4),
                    "return_if_down":  round(ret_dn, 4),
                    "status":          "executed",
                    "reason":          "",
                    "token_id":        raw.get("asset", raw.get("conditionId", "")).strip(),
                    "wallet_address":  raw.get("proxyWallet", raw.get("maker", "")).strip(),
                })
            except Exception as e:
                print(f"  Skipping row: {e}")
    return rows


# ── Per-window aggregation ────────────────────────────────────────────────────

def summarize_windows(trades: list[dict]) -> tuple[list[dict], dict]:
    by_window: dict[str, list[dict]] = defaultdict(list)
    for t in trades:
        by_window[t["window_start"]].append(t)

    summary_rows = []
    windows_json = {}

    for win_start in sorted(by_window.keys()):
        wt = by_window[win_start]
        up = [t for t in wt if t["outcome"].lower() == "up"]
        dn = [t for t in wt if t["outcome"].lower() == "down"]

        total_usdc = sum(t["usdc_paid"] for t in wt)
        up_usdc    = sum(t["usdc_paid"] for t in up)
        dn_usdc    = sum(t["usdc_paid"] for t in dn)
        ret_up     = round(sum(t["return_if_up"]   for t in wt), 4)
        ret_dn     = round(sum(t["return_if_down"] for t in wt), 4)
        secs       = [t["secs_in_window"] for t in wt]

        def wavg(subset):
            total = sum(t["usdc_paid"] for t in subset)
            return round(sum(t["price"] * t["usdc_paid"] for t in subset) / total, 6) if total else None

        row = {
            "window_start":              win_start,
            "market_title":              wt[0]["market_title"],
            "total_trades":              len(wt),
            "up_trades":                 len(up),
            "down_trades":               len(dn),
            "total_usdc_paid":           round(total_usdc, 4),
            "up_usdc_paid":              round(up_usdc, 4),
            "down_usdc_paid":            round(dn_usdc, 4),
            "up_wavg_price":             wavg(up),
            "down_wavg_price":           wavg(dn),
            "first_trade_secs":          min(secs),
            "last_trade_secs":           max(secs),
            "net_return_if_UP_wins":     ret_up,
            "net_return_if_DOWN_wins":   ret_dn,
        }
        summary_rows.append(row)
        windows_json[win_start] = {"stats": row, "trades": wt}

    return summary_rows, windows_json


# ── Main analysis function ────────────────────────────────────────────────────

def analyze(wallet_id: str, data_dir: str = "data/wallets",
            output_dir: str = "output/wallet_analysis") -> Path:
    """
    Run full analysis for a wallet.

    Looks for CSV files in data/wallets/{wallet_id}/:
      - detected_trades_*.csv  (bot logs — preferred)
      - trades.csv             (raw wallet download)

    Returns the output directory path.
    """
    wallet_dir = Path(data_dir) / wallet_id
    if not wallet_dir.exists():
        raise FileNotFoundError(
            f"Wallet data directory not found: {wallet_dir}\n"
            f"Run: poetry run wallet-download --address {wallet_id}"
        )

    out_dir = Path(output_dir) / wallet_id
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"\nAnalysing wallet: {wallet_id}")
    print(f"  Data dir:   {wallet_dir}")
    print(f"  Output dir: {out_dir}")

    # Discover input files
    all_trades: list[dict] = []

    bot_csvs = sorted(wallet_dir.glob("detected_trades_*.csv"))
    wallet_csvs = list((wallet_dir / "trades.csv",))

    for path in bot_csvs:
        trades = load_bot_csv(path)
        print(f"  Loaded {len(trades)} trades from {path.name} (bot log)")
        all_trades.extend(trades)

    if not bot_csvs:
        for path in wallet_csvs:
            if path.exists():
                trades = load_wallet_csv(path)
                print(f"  Loaded {len(trades)} trades from {path.name} (wallet download)")
                all_trades.extend(trades)

    if not all_trades:
        print("  ⚠ No trade data found.")
        return out_dir

    # Deduplicate and sort
    seen = set()
    unique: list[dict] = []
    for t in all_trades:
        key = (t["time_open"], t["outcome"], t["usdc_paid"])
        if key not in seen:
            seen.add(key)
            unique.append(t)

    unique.sort(key=lambda t: (t["window_start"], t["secs_in_window"]))
    print(f"  Total unique trades: {len(unique)}")

    summary, windows_json = summarize_windows(unique)

    # Write enriched trades CSV
    trade_fields = [
        "window_start", "secs_in_window", "time_open",
        "outcome", "side", "shares", "usdc_paid", "price",
        "up_price", "down_price", "return_if_up", "return_if_down",
        "status", "reason", "market_title", "token_id", "wallet_address",
    ]
    trades_path = out_dir / "trades_enriched.csv"
    with open(trades_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=trade_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(unique)
    print(f"  Saved {len(unique)} rows → {trades_path}")

    # Write summary CSV
    summary_fields = [
        "window_start", "market_title", "total_trades", "up_trades", "down_trades",
        "total_usdc_paid", "up_usdc_paid", "down_usdc_paid",
        "up_wavg_price", "down_wavg_price",
        "first_trade_secs", "last_trade_secs",
        "net_return_if_UP_wins", "net_return_if_DOWN_wins",
    ]
    summary_path = out_dir / "windows_summary.csv"
    with open(summary_path, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=summary_fields, extrasaction="ignore")
        w.writeheader()
        w.writerows(summary)
    print(f"  Saved {len(summary)} windows → {summary_path}")

    # Write JSON
    json_path = out_dir / "windows.json"
    with open(json_path, "w") as f:
        json.dump(windows_json, f, indent=2, default=str)
    print(f"  Saved full data → {json_path}")

    # Print console summary
    total_spent  = sum(r["total_usdc_paid"] for r in summary)
    total_ret_up = sum(r["net_return_if_UP_wins"]   for r in summary)
    total_ret_dn = sum(r["net_return_if_DOWN_wins"] for r in summary)

    print(f"\n  {'Window':<22} {'Trades':>6} {'UP $':>8} {'DN $':>8}  {'If UP':>8}  {'If DN':>8}")
    print("  " + "─" * 72)
    for r in summary:
        print(
            f"  {r['window_start'][:16]:<22} {r['total_trades']:>6} "
            f"{r['up_usdc_paid']:>8.2f} {r['down_usdc_paid']:>8.2f}  "
            f"{r['net_return_if_UP_wins']:>+8.2f}  {r['net_return_if_DOWN_wins']:>+8.2f}"
        )
    print("  " + "─" * 72)
    print(
        f"  {'TOTAL':<22} {sum(r['total_trades'] for r in summary):>6} "
        f"{sum(r['up_usdc_paid'] for r in summary):>8.2f} "
        f"{sum(r['down_usdc_paid'] for r in summary):>8.2f}  "
        f"{total_ret_up:>+8.2f}  {total_ret_dn:>+8.2f}"
    )
    print(f"\n  Total USDC spent    : ${total_spent:.2f}")
    print(f"  Net if ALL UP win   : ${total_ret_up:.2f}")
    print(f"  Net if ALL DOWN win : ${total_ret_dn:.2f}")

    return out_dir
