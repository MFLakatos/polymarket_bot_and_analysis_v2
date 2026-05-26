"""
Wallet Window Plotter
======================
Per-window chart layout:

  TOP PANEL
  ─────────
  LEFT axis  (0–1):  Implied probability dots + BTC price (normalised)
                     • BTC open price  →  y = 0.5  (coincides with neutral 50%)
                     • BTC moves up    →  y > 0.5
                     • BTC moves down  →  y < 0.5
                     • Scale auto-fit: largest BTC deviation from open = 0.35 units
                     • RIGHT side: secondary USD labels (O / H / L / C ticks)
                     • Blue  ● UP  buys (at their Polymarket implied probability)
                     • Red   ● DOWN buys

  RIGHT axis (USDC): Running scenario returns (step lines)
                     • Green  — Sum ret if UP   wins
                     • Orange — Sum ret if DOWN wins

  Legend:  BTC OHLC in USD  +  data-quality badge (candles / gaps)

  BOTTOM PANEL (shared x-axis)
  ─────────────────────────────
  dBTC/dt = price[t] − price[t−1]  (USD/s)
  Teal fill = price rising, red fill = falling.

RETURN FORMULA
──────────────
  Sum ret if UP   = Σ_{UP}  (1 − up_price)  × shares  −  Σ_{DOWN} down_price  × shares
  Sum ret if DOWN = Σ_{DOWN}(1 − down_price) × shares  −  Σ_{UP}  up_price    × shares

BTC DATA SOURCE & RELIABILITY
──────────────────────────────
  Source : Binance REST  /api/v3/klines  interval=1s
  Note   : Binance provides genuine 1-second OHLCV data.  However Polymarket
           uses Chainlink price feeds (on Polygon), not Binance.  Small
           discrepancies in level (<$50) and timing (<2 s) are normal.
           The legend shows the candle count so you can spot gaps.
           Data is cached to disk on first fetch (output/wallet_analysis/btc_cache/).
"""
from __future__ import annotations

import csv
import json
import os
import time
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

import requests
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.gridspec as gridspec
import matplotlib.ticker as mticker
import numpy as np

# ── Palette ───────────────────────────────────────────────────────────────────
C_UP      = "#3b82f6"
C_DOWN    = "#ef4444"
C_RET_UP  = "#22c55e"
C_RET_DWN = "#f97316"
C_BTC     = "#e2e8f0"
C_DERIV_P = "#2dd4bf"
C_DERIV_N = "#f87171"
C_ZERO    = "#475569"
BG        = "#0f172a"
GRID      = "#1e293b"
TXT       = "#94a3b8"
WIN_SECS  = 300


# ── BTC price fetcher ─────────────────────────────────────────────────────────

def fetch_btc_prices(
    window_start_utc: datetime,
    cache_dir: str,
) -> tuple[dict[int, float], dict]:
    """
    Fetch 1-second BTC close prices from Binance for a 300-second window.

    Returns
    -------
    prices : {offset_seconds: close_price}   offset 0 = window open
    meta   : data-quality dict with keys:
               candles_expected, candles_received, gaps, source
    """
    os.makedirs(cache_dir, exist_ok=True)
    slug        = window_start_utc.strftime("%Y%m%dT%H%M%S")
    cache_file  = os.path.join(cache_dir, f"btc_{slug}.json")
    cache_meta  = os.path.join(cache_dir, f"btc_{slug}_meta.json")

    if os.path.exists(cache_file):
        with open(cache_file) as f:
            prices = {int(k): v for k, v in json.load(f).items()}
        meta = {}
        if os.path.exists(cache_meta):
            with open(cache_meta) as f:
                meta = json.load(f)
        return prices, meta

    start_ms = int(window_start_utc.timestamp() * 1_000)
    end_ms   = start_ms + WIN_SECS * 1_000

    try:
        resp = requests.get(
            "https://api.binance.com/api/v3/klines",
            params={
                "symbol":    "BTCUSDT",
                "interval":  "1s",
                "startTime": start_ms,
                "endTime":   end_ms,
                "limit":     WIN_SECS,
            },
            timeout=15,
        )
        resp.raise_for_status()
        klines = resp.json()
    except Exception as exc:
        print(f"    [warn] BTC fetch failed: {exc}")
        return {}, {"error": str(exc)}

    prices: dict[int, float] = {}
    for kl in klines:
        offset         = int((kl[0] - start_ms) / 1_000)
        prices[offset] = float(kl[4])   # close price of 1-second candle

    # Detect gaps: seconds with no candle between 0 and max offset
    if prices:
        expected_offsets = set(range(0, max(prices) + 1))
        actual_offsets   = set(prices.keys())
        gap_seconds      = sorted(expected_offsets - actual_offsets)
        n_gaps           = len(gap_seconds)
    else:
        n_gaps = WIN_SECS

    meta = {
        "candles_expected": WIN_SECS,
        "candles_received": len(prices),
        "gaps":             n_gaps,
        "source":           "Binance /api/v3/klines interval=1s",
        "note": (
            "Binance 1s data is genuine per-second OHLCV. "
            "Polymarket uses Chainlink (Polygon); small level/timing "
            "discrepancies (<$50, <2s) are normal."
        ),
    }

    with open(cache_file,  "w") as f:
        json.dump(prices, f)
    with open(cache_meta, "w") as f:
        json.dump(meta, f, indent=2)

    time.sleep(0.15)
    return prices, meta


# ── OHLC helper ───────────────────────────────────────────────────────────────

def compute_ohlc(prices: dict[int, float]) -> dict:
    """Compute O/H/L/C over the window from {offset: price}."""
    if not prices:
        return {}
    xs    = sorted(prices.keys())
    vals  = [prices[x] for x in xs]
    return {
        "O": vals[0],
        "H": max(vals),
        "L": min(vals),
        "C": vals[-1],
        "range": max(vals) - min(vals),
    }


# ── Loader ────────────────────────────────────────────────────────────────────

def load(csv_path: str | Path) -> dict[str, list[dict]]:
    """Load trades_enriched.csv → {window_start: [trade_dict, ...]}."""
    by_window: dict[str, list[dict]] = defaultdict(list)
    with open(csv_path, newline="", encoding="utf-8") as f:
        for row in csv.DictReader(f):
            by_window[row["window_start"]].append({
                "outcome":      row["outcome"].lower().strip(),
                "secs":         int(row["secs_in_window"]),
                "shares":       float(row.get("shares") or 1.0),
                "usdc_paid":    float(row.get("usdc_paid") or 0),
                "up_price":     float(row.get("up_price") or 0),
                "down_price":   float(row.get("down_price") or 0),
                "market_title": row.get("market_title", ""),
            })
    return by_window


# ── Return series ─────────────────────────────────────────────────────────────

def compute_return_series(
    trades: list[dict],
) -> tuple[list[int], list[float], list[float]]:
    """
    Running scenario returns, updating at each trade event.

    Sum ret if UP   = Σ_{UP}  (1−up_p)*s  −  Σ_{DOWN} dn_p*s
    Sum ret if DOWN = Σ_{DOWN}(1−dn_p)*s  −  Σ_{UP}   up_p*s
    """
    secs   = [0]
    ret_up = [0.0]
    ret_dn = [0.0]
    ru = rd = 0.0

    for t in sorted(trades, key=lambda x: x["secs"]):
        s = t["shares"]
        if t["outcome"] == "up":
            ru += (1.0 - t["up_price"]) * s
            rd -= t["up_price"] * s
        else:
            ru -= t["down_price"] * s
            rd += (1.0 - t["down_price"]) * s
        secs.append(t["secs"])
        ret_up.append(ru)
        ret_dn.append(rd)

    return secs, ret_up, ret_dn


# ── BTC normalization ─────────────────────────────────────────────────────────

def normalise_btc(
    btc_prices: dict[int, float],
    target_half_range: float = 0.35,
) -> tuple[list[int], list[float], float, float]:
    """
    Normalise BTC prices so that open_price → 0.5 on the left (0–1) axis.

    The largest deviation from the open price is mapped to ±target_half_range.
    Returns (x_seconds, y_normalised, open_price_usd, scale_usd_per_unit).

    scale_usd_per_unit tells you how many USD correspond to 1 unit on the
    left axis (useful for the secondary USD tick labels).
    """
    xs   = sorted(btc_prices.keys())
    vals = np.array([btc_prices[x] for x in xs], dtype=float)
    p0   = vals[0]

    deviations  = vals - p0
    max_dev     = max(abs(deviations.max()), abs(deviations.min()), 1.0)
    scale       = target_half_range / max_dev        # prob units per USD
    normalised  = deviations * scale + 0.5           # 0.5 = open price

    return xs, normalised.tolist(), p0, 1.0 / scale  # return USD/unit


# ── Axis styling ─────────────────────────────────────────────────────────────

def _style(ax, ylabel: str, ycolor: str = TXT) -> None:
    ax.set_facecolor(BG)
    ax.tick_params(colors=TXT, labelsize=8)
    ax.set_ylabel(ylabel, color=ycolor, fontsize=8.5)
    for sp in ax.spines.values():
        sp.set_edgecolor(GRID)


# ── Main plot ─────────────────────────────────────────────────────────────────

def plot_window(
    win_key:   str,
    trades:    list[dict],
    outdir:    str,
    cache_dir: str,
) -> str:
    """Plot one 5-minute window. Returns saved PNG path."""

    up_t  = [t for t in trades if t["outcome"] == "up"]
    dn_t  = [t for t in trades if t["outcome"] == "down"]

    # Fetch BTC data
    dt_win = datetime.fromisoformat(win_key.replace("Z", "+00:00"))
    if dt_win.tzinfo is None:
        dt_win = dt_win.replace(tzinfo=timezone.utc)
    btc_prices, btc_meta = fetch_btc_prices(dt_win, cache_dir)
    ohlc = compute_ohlc(btc_prices)

    # ── Figure ────────────────────────────────────────────────────────────────
    fig = plt.figure(figsize=(16, 10), facecolor=BG)
    gs  = gridspec.GridSpec(
        2, 1,
        height_ratios=[3.5, 1],
        hspace=0.07,
        left=0.06, right=0.88, top=0.91, bottom=0.08,
    )
    ax_prob = fig.add_subplot(gs[0])
    ax_drv  = fig.add_subplot(gs[1], sharex=ax_prob)
    ax_ret  = ax_prob.twinx()    # right: scenario returns

    # Style
    _style(ax_prob, "Implied probability  /  BTC normalised (open = 0.5)", C_BTC)
    _style(ax_ret,  "Net return (USDC)",    C_RET_UP)
    _style(ax_drv,  "dBTC/dt  (USD/s)",     C_DERIV_P)

    ax_prob.set_xlabel("", color=TXT)
    ax_drv .set_xlabel("Seconds in window", color=TXT, fontsize=8.5)
    ax_prob.set_xlim(0, WIN_SECS)
    ax_prob.set_ylim(0.0, 1.0)
    ax_prob.axhline(0.5, color=GRID, linewidth=0.9, linestyle="--", alpha=0.7,
                    label="_nolegend_")
    ax_ret .axhline(0.0, color=C_ZERO, linewidth=0.6)
    ax_drv .axhline(0.0, color=C_ZERO, linewidth=0.7)
    ax_prob.grid(axis="x", color=GRID, linewidth=0.35, alpha=0.45)
    plt.setp(ax_prob.get_xticklabels(), visible=False)

    # ── BTC price on LEFT axis (open = 0.5) ───────────────────────────────────
    usd_per_unit = None   # how many USD = 1 unit on the left axis
    if btc_prices:
        xs_n, ys_n, p0, usd_per_unit = normalise_btc(btc_prices)

        # Main BTC line
        ax_prob.plot(xs_n, ys_n, color=C_BTC, linewidth=1.4, alpha=0.80,
                     zorder=3, label="_btc_line_")

        # Fill above/below open (0.5)
        ax_prob.fill_between(xs_n, ys_n, 0.5,
                             where=[y >= 0.5 for y in ys_n],
                             color=C_UP, alpha=0.07, step=None)
        ax_prob.fill_between(xs_n, ys_n, 0.5,
                             where=[y <  0.5 for y in ys_n],
                             color=C_DOWN, alpha=0.07, step=None)

        # Secondary USD tick labels on the LEFT spine
        # Show ticks at 0.2, 0.35, 0.5, 0.65, 0.8 → convert to USD
        prob_ticks = [0.10, 0.20, 0.35, 0.50, 0.65, 0.80, 0.90]
        usd_labels = [f"${p0 + (pt - 0.5) * usd_per_unit:,.0f}" for pt in prob_ticks]
        ax2_usd = ax_prob.secondary_yaxis("left")
        ax2_usd.set_yticks(prob_ticks)
        ax2_usd.set_yticklabels(usd_labels, fontsize=6.5, color="#64748b")
        ax2_usd.tick_params(length=3, width=0.5, colors="#64748b")
        for sp in ax2_usd.spines.values():
            sp.set_edgecolor(GRID)
        # Keep primary ticks too (probability)
        ax_prob.set_yticks([0.0, 0.25, 0.5, 0.75, 1.0])
        ax_prob.set_yticklabels(["0.00", "0.25", "0.50", "0.75", "1.00"],
                                fontsize=8, color=TXT)

        # BTC derivative in bottom panel
        xs_sorted = sorted(btc_prices.keys())
        if len(xs_sorted) > 1:
            drv_xs = xs_sorted[1:]
            drv_ys = np.array(
                [btc_prices[xs_sorted[i]] - btc_prices[xs_sorted[i-1]]
                 for i in range(1, len(xs_sorted))],
                dtype=float,
            )
            ax_drv.fill_between(drv_xs, drv_ys, 0,
                                where=drv_ys >= 0, color=C_DERIV_P, alpha=0.65, step="post")
            ax_drv.fill_between(drv_xs, drv_ys, 0,
                                where=drv_ys <  0, color=C_DERIV_N, alpha=0.65, step="post")
            ax_drv.step(drv_xs, drv_ys, where="post",
                        color=C_DERIV_P, linewidth=0.8, alpha=0.85)
            ymax = max(float(np.abs(drv_ys).max()), 1.0)
            ax_drv.set_ylim(-ymax * 1.4, ymax * 1.4)
    else:
        ax_drv.text(WIN_SECS / 2, 0, "BTC data unavailable",
                    ha="center", va="center", color=TXT, fontsize=9)

    # ── Scenario return step lines ─────────────────────────────────────────────
    if trades:
        secs, ret_up, ret_dn = compute_return_series(trades)
        # Extend to window end
        secs   .append(WIN_SECS);  ret_up.append(ret_up[-1]);  ret_dn.append(ret_dn[-1])

        xs_a  = np.array(secs,   dtype=float)
        ys_ru = np.array(ret_up, dtype=float)
        ys_rd = np.array(ret_dn, dtype=float)

        ax_ret.step(secs, ret_up, where="post", color=C_RET_UP,  linewidth=2.0,
                    label="Sum ret if UP")
        ax_ret.step(secs, ret_dn, where="post", color=C_RET_DWN, linewidth=2.0,
                    label="Sum ret if DOWN")
        ax_ret.fill_between(xs_a, ys_ru, 0, step="post",
                            where=ys_ru >= 0, color=C_RET_UP,  alpha=0.09)
        ax_ret.fill_between(xs_a, ys_ru, 0, step="post",
                            where=ys_ru <  0, color=C_RET_UP,  alpha=0.04)
        ax_ret.fill_between(xs_a, ys_rd, 0, step="post",
                            where=ys_rd >= 0, color=C_RET_DWN, alpha=0.09)
        ax_ret.fill_between(xs_a, ys_rd, 0, step="post",
                            where=ys_rd <  0, color=C_RET_DWN, alpha=0.04)

        ax_ret.tick_params(axis="y", colors=C_RET_UP, labelsize=8)
        ax_ret.yaxis.set_major_formatter(
            mticker.FuncFormatter(lambda v, _: f"{v:+.3f}")
        )

    # ── Trade dots on left axis ────────────────────────────────────────────────
    for t in up_t:
        ax_prob.scatter(t["secs"], t["up_price"],
                        color=C_UP, s=95, zorder=6,
                        edgecolors="white", linewidths=0.6,
                        label="UP buy" if t is up_t[0] else "")
    for t in dn_t:
        ax_prob.scatter(t["secs"], t["down_price"],
                        color=C_DOWN, s=95, zorder=6,
                        edgecolors="white", linewidths=0.6,
                        label="DOWN buy" if t is dn_t[0] else "")

    # Annotations
    for t in trades:
        col   = C_UP if t["outcome"] == "up" else C_DOWN
        price = t["up_price"] if t["outcome"] == "up" else t["down_price"]
        lbl   = f"${t['usdc_paid']:.2f}"
        if t["shares"] != 1.0:
            lbl += f" ×{t['shares']:.0f}"
        ax_prob.annotate(lbl, xy=(t["secs"], price),
                         xytext=(5, 6), textcoords="offset points",
                         fontsize=7.5, color=col, zorder=7)

    # Vertical trade markers (both panels)
    for t in trades:
        col = C_UP if t["outcome"] == "up" else C_DOWN
        for ax in (ax_prob, ax_drv):
            ax.axvline(t["secs"], color=col, linewidth=0.6, alpha=0.35, linestyle="--")

    # ── Legend ────────────────────────────────────────────────────────────────
    # OHLC badge
    n_recv = btc_meta.get("candles_received", 0)
    n_gaps = btc_meta.get("gaps", 0)
    quality_str = f"{n_recv}/300 candles" + (f"  ⚠ {n_gaps} gaps" if n_gaps else "  ✓ complete")

    if ohlc:
        ohlc_label = (
            f"BTC  O:${ohlc['O']:,.0f}  H:${ohlc['H']:,.0f}  "
            f"L:${ohlc['L']:,.0f}  C:${ohlc['C']:,.0f}  "
            f"Rng:${ohlc['range']:.0f}\n"
            f"1s Binance  [{quality_str}]"
        )
        if usd_per_unit:
            ohlc_label += f"  · scale: $1 = {1/usd_per_unit*1000:.2f}‰ prob"
    else:
        ohlc_label = f"BTC unavailable  [{quality_str}]"

    # Build combined legend
    from matplotlib.lines import Line2D
    from matplotlib.patches import Patch

    h_prob, l_prob = ax_prob.get_legend_handles_labels()
    h_ret,  l_ret  = ax_ret.get_legend_handles_labels()

    btc_handle = Line2D([0], [0], color=C_BTC, linewidth=1.5, label=ohlc_label)
    extra_handles = [btc_handle]
    extra_labels  = [ohlc_label]

    all_handles = extra_handles + h_prob + h_ret
    all_labels  = extra_labels  + l_prob + l_ret

    ax_prob.legend(
        all_handles, all_labels,
        loc="upper right",
        fontsize=7.5,
        facecolor="#0f172a",
        edgecolor="#334155",
        labelcolor="white",
        framealpha=0.92,
        ncol=1,
        handlelength=1.4,
    )

    # Derivative strip legend
    from matplotlib.patches import Patch as P
    ax_drv.legend(
        handles=[
            P(color=C_DERIV_P, alpha=0.7, label="dBTC/dt ≥ 0  (rising)"),
            P(color=C_DERIV_N, alpha=0.7, label="dBTC/dt < 0  (falling)"),
        ],
        loc="upper left", fontsize=7,
        facecolor="#0f172a", edgecolor="#334155",
        labelcolor="white", framealpha=0.88,
    )

    # ── Title ─────────────────────────────────────────────────────────────────
    total_cost   = sum(t["usdc_paid"] for t in trades)
    up_shares    = sum(t["shares"] for t in up_t)
    dn_shares    = sum(t["shares"] for t in dn_t)
    title = (
        f"BTC 5-min window  ·  {win_key[:16]} UTC\n"
        f"{len(up_t)} UP ({up_shares:.0f} shares)  "
        f"| {len(dn_t)} DOWN ({dn_shares:.0f} shares)  "
        f"| total spent: ${total_cost:.3f}"
    )
    ax_prob.set_title(title, color="white", fontsize=9.5, pad=9,
                      fontfamily="monospace", loc="left")

    # ── Save ──────────────────────────────────────────────────────────────────
    os.makedirs(outdir, exist_ok=True)
    slug  = win_key[:16].replace("-", "").replace("T", "_").replace(":", "")
    fname = os.path.join(outdir, f"window_{slug}.png")
    fig.savefig(fname, dpi=160, bbox_inches="tight", facecolor=BG)
    plt.close(fig)
    return fname


# ── Entry point ───────────────────────────────────────────────────────────────

def plot_all(
    trades_csv:  str | Path,
    output_dir:  str = "output/wallet_analysis",
    cache_dir:   str | None = None,
) -> list[str]:
    """Plot all windows. Returns list of saved PNG paths."""
    path      = Path(trades_csv)
    wallet_id = path.parent.name
    out       = Path(output_dir) / wallet_id / "window_plots"
    cache     = Path(cache_dir or str(path.parent.parent / "btc_cache"))

    print(f"Loading {path} ...")
    by_window = load(path)
    n_trades  = sum(len(v) for v in by_window.values())
    print(f"  {n_trades} trades across {len(by_window)} windows")
    print(f"  BTC cache: {cache}\n")

    saved: list[str] = []
    for win_key in sorted(by_window.keys()):
        trades = by_window[win_key]
        up_n   = sum(1 for t in trades if t["outcome"] == "up")
        dn_n   = len(trades) - up_n
        print(f"  {win_key[:16]}  ({up_n} UP, {dn_n} DOWN)", end=" ... ", flush=True)
        try:
            fname = plot_window(win_key, trades, str(out), str(cache))
            # Print BTC quality for this window
            meta_file = Path(cache) / f"btc_{datetime.fromisoformat(win_key.replace('Z','+00:00')).strftime('%Y%m%dT%H%M%S')}_meta.json"
            quality = ""
            if meta_file.exists():
                m = json.loads(meta_file.read_text())
                quality = f"  [{m.get('candles_received','?')}/300 candles"
                if m.get("gaps", 0):
                    quality += f", {m['gaps']} gaps]"
                else:
                    quality += "]"
            print(f"→ {Path(fname).name}{quality}")
            saved.append(fname)
        except Exception as exc:
            print(f"ERROR: {exc}")

    print(f"\n✓ {len(saved)} plots saved → {out}/")
    return saved
