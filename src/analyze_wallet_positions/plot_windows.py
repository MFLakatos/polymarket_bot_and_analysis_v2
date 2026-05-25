"""
Wallet Window Plotter
======================
Reads the output of analyze_trades.py (trades_enriched.csv) and plots
one chart per 5-minute window, similar to the original plot_windows.py.

Per-window chart:
  • Blue  dots — UP   buys at their implied price (left axis, 0–1)
  • Red   dots — DOWN buys at their implied price (left axis, 0–1)
  • Green  step line — cumulative net return if UP   wins (right axis)
  • Orange step line — cumulative net return if DOWN wins (right axis)
  • White  line — BTC/USDT 1-second close, normalised so window-open
                  price sits at y=0.5 on left axis

BTC data is fetched from Binance (cached in output/wallet_analysis/btc_cache/).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.ticker as mticker

# ── Palette ───────────────────────────────────────────────────────────────────
C_UP      = "#3b82f6"
C_DOWN    = "#ef4444"
C_RET_UP  = "#22c55e"
C_RET_DWN = "#f97316"
C_BTC     = "#f8fafc"
BG        = "#0f172a"
GRID      = "#1e293b"
WIN_SECS  = 300
BTC_SCALE = 0.5   # normalisation factor for BTC price on left axis


# ── BTC price fetcher ─────────────────────────────────────────────────────────

def fetch_btc_prices(window_start_utc: datetime, cache_dir: str) -> dict[int, float]:
    """Return {offset_seconds: close_price} for the 300-second window, cached."""
    os.makedirs(cache_dir, exist_ok=True)
    slug = window_start_utc.strftime("%Y%m%dT%H%M%S")
    cache_file = os.path.join(cache_dir, f"btc_{slug}.json")

    if os.path.exists(cache_file):
        with open(cache_file) as f:
            return {int(k): v for k, v in json.load(f).items()}

    start_ms = int(window_start_utc.timestamp() * 1000)
    end_ms   = start_ms + WIN_SECS * 1000

    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={"symbol": "BTCUSDT", "interval": "1s",
                    "startTime": start_ms, "endTime": end_ms, "limit": 300},
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()
    except Exception as e:
        print(f"    BTC fetch failed: {e}")
        return {}

    prices = {}
    for kl in klines:
        offset = int((kl[0] - start_ms) / 1000)
        prices[offset] = float(kl[4])  # close

    with open(cache_file, "w") as f:
        json.dump(prices, f)
    time.sleep(0.15)
    return prices


# ── Loader ────────────────────────────────────────────────────────────────────

def load(csv_path: str | Path) -> dict[str, list[dict]]:
    """Load trades_enriched.csv, group by window_start."""
    by_window: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_window[row["window_start"]].append({
                "outcome":       row["outcome"].lower(),
                "secs":          int(row["secs_in_window"]),
                "usdc_paid":     float(row["usdc_paid"]),
                "price":         float(row.get("price") or 0),
                "up_price":      float(row.get("up_price") or 0),
                "down_price":    float(row.get("down_price") or 0),
                "return_if_up":  float(row["return_if_up"]),
                "return_if_down":float(row["return_if_down"]),
                "market_title":  row.get("market_title", ""),
            })
    return by_window


# ── Per-window plotter ────────────────────────────────────────────────────────

def plot_window(win_key: str, trades: list[dict],
                outdir: str, cache_dir: str) -> str:
    """Plot one 5-minute window. Returns saved filename."""
    up_t = [t for t in trades if t["outcome"] == "up"]
    dn_t = [t for t in trades if t["outcome"] == "down"]

    # Fetch BTC prices
    dt_win = datetime.fromisoformat(win_key.replace("Z", "+00:00"))
    if dt_win.tzinfo is None:
        dt_win = dt_win.replace(tzinfo=timezone.utc)
    btc_prices = fetch_btc_prices(dt_win, cache_dir)

    fig, ax1 = plt.subplots(figsize=(14, 6), facecolor=BG)
    ax2 = ax1.twinx()
    ax1.set_facecolor(BG)
    ax2.set_facecolor(BG)

    for spine in list(ax1.spines.values()) + list(ax2.spines.values()):
        spine.set_edgecolor(GRID)

    ax1.tick_params(colors="#94a3b8");  ax2.tick_params(colors="#94a3b8")
    ax1.set_xlabel("Seconds in window", color="#94a3b8")
    ax1.set_ylabel("Implied probability", color="#94a3b8")
    ax2.set_ylabel("Net return (USDC)", color="#94a3b8")
    ax1.set_xlim(0, WIN_SECS)
    ax1.set_ylim(0, 1)
    ax1.axhline(0.5, color=GRID, linewidth=0.8, linestyle="--")

    # BTC price line
    if btc_prices:
        p0 = btc_prices.get(0, next(iter(btc_prices.values())))
        xs = sorted(btc_prices.keys())
        ys = [0.5 + (btc_prices[x] - p0) * BTC_SCALE / p0 for x in xs]
        price_range = max(abs(y - 0.5) for y in ys) * p0 / BTC_SCALE if ys else 0
        ax1.plot(xs, ys, color=C_BTC, linewidth=1.2, alpha=0.7,
                 label=f"BTC (±${price_range:.0f} scale)")

    # Trade dots
    for t in up_t:
        ax1.scatter(t["secs"], t["up_price"], color=C_UP, s=80, zorder=5,
                    label="UP buy" if t == up_t[0] else "")
    for t in dn_t:
        ax1.scatter(t["secs"], t["down_price"], color=C_DOWN, s=80, zorder=5,
                    label="DOWN buy" if t == dn_t[0] else "")

    # Cumulative returns
    if trades:
        xs_sorted = sorted(t["secs"] for t in trades)
        trades_s = sorted(trades, key=lambda t: t["secs"])
        cum_up = 0.0;  cum_dn = 0.0
        cx, cy_up, cy_dn = [], [], []
        for t in trades_s:
            cum_up += t["return_if_up"]
            cum_dn += t["return_if_down"]
            cx.append(t["secs"]); cy_up.append(cum_up); cy_dn.append(cum_dn)
        ax2.step(cx, cy_up, where="post", color=C_RET_UP,  linewidth=1.8, label="Cum ret if UP")
        ax2.step(cx, cy_dn, where="post", color=C_RET_DWN, linewidth=1.8, label="Cum ret if DN")
        ax2.axhline(0, color="#475569", linewidth=0.6)

    # Trade annotations
    for t in trades:
        col = C_UP if t["outcome"] == "up" else C_DOWN
        ax1.annotate(
            f"${t['usdc_paid']:.1f}",
            xy=(t["secs"], t["up_price"] if t["outcome"] == "up" else t["down_price"]),
            xytext=(6, 6), textcoords="offset points",
            fontsize=7, color=col,
            arrowprops=dict(arrowstyle="-", color=col, alpha=0.35),
        )

    total_cost = sum(t["usdc_paid"] for t in trades)
    ax1.set_title(
        f"BTC 5-min window  ·  {win_key[:16]}\n"
        f"{len(up_t)} UP  |  {len(dn_t)} DOWN  |  Total spent: ${total_cost:.2f}",
        color="white", fontsize=11, pad=14, fontfamily="monospace",
    )

    h1, l1 = ax1.get_legend_handles_labels()
    h2, l2 = ax2.get_legend_handles_labels()
    ax1.legend(h1 + h2, l1 + l2, loc="upper left", fontsize=8,
               facecolor="#1e293b", edgecolor="#334155",
               labelcolor="white", framealpha=0.85)

    os.makedirs(outdir, exist_ok=True)
    slug = win_key[:16].replace("-", "").replace("T", "_").replace(":", "")
    fname = os.path.join(outdir, f"window_{slug}.png")
    plt.tight_layout()
    plt.savefig(fname, dpi=150, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return fname


# ── Main ──────────────────────────────────────────────────────────────────────

def plot_all(
    trades_csv: str | Path,
    output_dir: str = "output/wallet_analysis",
    cache_dir: str | None = None,
) -> list[str]:
    """Plot all windows from a trades_enriched.csv. Returns list of saved paths."""
    path = Path(trades_csv)
    wallet_id = path.parent.name
    out = Path(output_dir) / wallet_id / "window_plots"
    cache = Path(cache_dir or (str(path.parent.parent / "btc_cache")))

    print(f"Loading {path} ...")
    by_window = load(path)
    n = sum(len(v) for v in by_window.values())
    print(f"  {n} trades across {len(by_window)} windows\n")

    saved = []
    for win_key in sorted(by_window.keys()):
        trades = by_window[win_key]
        up_n = sum(1 for t in trades if t["outcome"] == "up")
        dn_n = len(trades) - up_n
        print(f"  {win_key[:16]}  ({up_n} UP, {dn_n} DOWN)", end=" ... ")
        fname = plot_window(win_key, trades, str(out), str(cache))
        print(f"→ {Path(fname).name}")
        saved.append(fname)

    print(f"\n✓ {len(saved)} plots saved to {out}/")
    return saved
