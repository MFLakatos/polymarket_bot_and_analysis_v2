"""
BTC Reversal Model – Interactive Visualizer
============================================
Generates a self-contained HTML file with four interactive tabs:

  1. 3D Surface   – rotate & zoom the full P(reversal) probability landscape
  2. Heatmap      – top-down 2-D view with contour lines; hover for exact values
  3. Time slices  – P(reversal) vs Δ price at fixed time-remaining thresholds
  4. Dataset info – training period, sample counts, filter settings, model config

Each tab is a separate Plotly figure, avoiding the 3D/2D axis-space conflict
that breaks heatmaps and scatter charts when mixed with go.Surface in one figure.

Usage
-----
    # From Python
    from btc_reversal_model.visualize import save_html
    save_html("output/reversal_viz.html")

    # From CLI  (after poetry install)
    visualize-reversal --output output/reversal_viz.html
    visualize-reversal --filter-market-hours   # shows filter info in Dataset tab

The HTML is self-contained (Plotly JS bundled inline, ~3 MB).
No internet connection required.
"""
from __future__ import annotations

import argparse
from pathlib import Path
from typing import Any

import numpy as np

# ── Palette ───────────────────────────────────────────────────────────────────
BG        = "#0f172a"
PAPER_BG  = "#0f172a"
PLOT_BG   = "#1e293b"
GRID_CLR  = "#334155"
TXT       = "#94a3b8"
TXT_LIGHT = "#cbd5e1"
TITLE_CLR = "#e2e8f0"
ACCENT    = "#3b82f6"

_SLICE_COLOURS = [
    "#3b82f6", "#06b6d4", "#22c55e", "#eab308",
    "#f97316", "#ef4444", "#a855f7",
]

_COMMON_LAYOUT = dict(
    paper_bgcolor=PAPER_BG,
    plot_bgcolor=PLOT_BG,
    font=dict(color=TXT, size=12),
    height=640,
    margin=dict(l=70, r=40, t=70, b=60),
    hoverlabel=dict(
        bgcolor="#1e293b",
        bordercolor="#475569",
        font=dict(color="white", size=12),
    ),
    legend=dict(
        bgcolor=BG,
        bordercolor=GRID_CLR,
        borderwidth=1,
        font=dict(color=TXT),
    ),
)

_AXIS_STYLE = dict(
    gridcolor=GRID_CLR,
    zerolinecolor=GRID_CLR,
    tickfont=dict(color=TXT),
    linecolor=GRID_CLR,
)


# ── Helper: colourbar ─────────────────────────────────────────────────────────

def _cbar(title: str) -> dict:
    return dict(
        title=dict(text=title, font=dict(color=TXT, size=12)),
        tickfont=dict(color=TXT),
        bgcolor=BG,
        bordercolor=GRID_CLR,
        len=0.80,
    )


# ── Figure builders ───────────────────────────────────────────────────────────

def _fig_surface(grid: np.ndarray, d_centers: list, t_centers: list, base_rate: float):
    import plotly.graph_objects as go

    # grid shape: (n_delta, n_time)
    # Surface: z[i][j] = P at y=d_centers[i], x=t_centers[j]
    trace = go.Surface(
        x=t_centers,
        y=d_centers,
        z=grid.tolist(),
        colorscale="RdYlGn_r",
        cmin=0.0, cmax=1.0,
        opacity=0.93,
        colorbar=_cbar("P(reversal)"),
        hovertemplate=(
            "<b>Time remaining:</b> %{x:.0f} s<br>"
            "<b>Δ price:</b> $%{y:+,.0f}<br>"
            "<b>P(reversal):</b> %{z:.3f}<extra></extra>"
        ),
    )

    fig = go.Figure(data=[trace])
    fig.update_layout(
        **_COMMON_LAYOUT,
        title=dict(
            text=f"P(reversal) — 3D surface · base rate = {base_rate:.3f}",
            font=dict(color=TITLE_CLR, size=15), x=0.03, xanchor="left",
        ),
        scene=dict(
            bgcolor=PLOT_BG,
            xaxis=dict(
                title="Time remaining (s)",
                gridcolor=GRID_CLR, backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT), titlefont=dict(color=TXT),
            ),
            yaxis=dict(
                title="Δ price (USD)",
                gridcolor=GRID_CLR, backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT), titlefont=dict(color=TXT),
            ),
            zaxis=dict(
                title="P(reversal)", range=[0, 1],
                gridcolor=GRID_CLR, backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT), titlefont=dict(color=TXT),
            ),
            camera=dict(eye=dict(x=1.65, y=-1.65, z=0.85)),
        ),
    )
    return fig


def _fig_heatmap(grid: np.ndarray, d_centers: list, t_centers: list, base_rate: float):
    import plotly.graph_objects as go

    hover = (
        "<b>Time remaining:</b> %{x:.0f} s<br>"
        "<b>Δ price:</b> $%{y:+,.0f}<br>"
        "<b>P(reversal):</b> %{z:.3f}<extra></extra>"
    )

    heatmap = go.Heatmap(
        x=t_centers, y=d_centers, z=grid.tolist(),
        colorscale="RdYlGn_r", zmin=0.0, zmax=1.0,
        colorbar=_cbar("P(reversal)"),
        hovertemplate=hover,
    )
    contour = go.Contour(
        x=t_centers, y=d_centers, z=grid.tolist(),
        colorscale="RdYlGn_r", showscale=False,
        contours=dict(
            start=0.1, end=0.9, size=0.1,
            coloring="none",
            showlabels=True,
            labelfont=dict(size=8, color="white"),
        ),
        line=dict(width=0.7),
        hoverinfo="skip",
    )

    fig = go.Figure(data=[heatmap, contour])
    fig.update_layout(
        **_COMMON_LAYOUT,
        title=dict(
            text=f"P(reversal) — heatmap · base rate = {base_rate:.3f}",
            font=dict(color=TITLE_CLR, size=15), x=0.03, xanchor="left",
        ),
        xaxis=dict(title="Time remaining (s)", **_AXIS_STYLE),
        yaxis=dict(title="Δ price (USD)", **_AXIS_STYLE),
    )
    return fig


def _fig_slices(grid: np.ndarray, d_centers: list, t_centers: list, base_rate: float):
    import plotly.graph_objects as go

    t_arr   = np.array(t_centers)
    targets = [30, 60, 90, 120, 150, 180, 240]
    traces  = []

    for idx, t_target in enumerate(targets):
        ti     = int(np.argmin(np.abs(t_arr - t_target)))
        actual = t_arr[ti]
        colour = _SLICE_COLOURS[idx % len(_SLICE_COLOURS)]
        traces.append(go.Scatter(
            x=d_centers,
            y=grid[:, ti].tolist(),
            mode="lines",
            line=dict(color=colour, width=2),
            name=f"{actual:.0f} s remaining",
            hovertemplate=(
                f"<b>Time remaining:</b> {actual:.0f} s<br>"
                "<b>Δ price:</b> $%{x:+,.0f}<br>"
                "<b>P(reversal):</b> %{y:.3f}<extra></extra>"
            ),
        ))

    # Base-rate reference
    traces.append(go.Scatter(
        x=[d_centers[0], d_centers[-1]],
        y=[base_rate, base_rate],
        mode="lines",
        line=dict(color="#64748b", width=1.5, dash="dash"),
        name=f"Base rate ({base_rate:.3f})",
        hoverinfo="skip",
    ))

    fig = go.Figure(data=traces)
    fig.update_layout(
        **_COMMON_LAYOUT,
        title=dict(
            text="P(reversal) vs Δ price — time-slice view",
            font=dict(color=TITLE_CLR, size=15), x=0.03, xanchor="left",
        ),
        xaxis=dict(title="Δ price from window open (USD)", **_AXIS_STYLE),
        yaxis=dict(title="P(reversal)", range=[0, 1], **_AXIS_STYLE),
    )
    return fig


# ── Dataset-info HTML panel ───────────────────────────────────────────────────

def _html_info(meta: dict[str, Any]) -> str:
    """Returns a styled HTML panel summarising the dataset and model config."""

    def _row(label: str, value: str, note: str = "") -> str:
        note_span = f'<span class="note">{note}</span>' if note else ""
        return (
            f'<tr><td class="lbl">{label}</td>'
            f'<td class="val">{value}{note_span}</td></tr>'
        )

    def _section(title: str, rows: str) -> str:
        return f"""
        <div class="card">
          <div class="card-title">{title}</div>
          <table>{rows}</table>
        </div>"""

    # ── Training data ─────────────────────────────────────────────────────────
    first_ts   = meta.get("first_ts", "unknown")
    last_ts    = meta.get("last_ts", "unknown")
    n_seconds  = meta.get("n_seconds")
    n_windows  = meta.get("n_windows_total", "unknown")
    n_kept     = meta.get("n_windows_kept", n_windows)
    n_samples  = meta.get("n_samples", "unknown")
    base_rate  = meta.get("base_rate", 0.0)
    delta_min  = meta.get("delta_min", 0.0)
    delta_max  = meta.get("delta_max", 0.0)

    duration_str = ""
    if isinstance(n_seconds, (int, float)):
        days  = int(n_seconds / 86_400)
        hours = int((n_seconds % 86_400) / 3600)
        duration_str = f"≈ {days:,} days {hours} h"

    training_rows = (
        _row("First candle",       str(first_ts))
        + _row("Last candle",      str(last_ts))
        + _row("1-second candles", f"{n_seconds:,}" if isinstance(n_seconds, (int, float)) else str(n_seconds),
               duration_str)
        + _row("5-min windows",    f"{n_windows:,}" if isinstance(n_windows, int) else str(n_windows))
        + _row("Labeled samples",  f"{n_samples:,}" if isinstance(n_samples, int) else str(n_samples),
               "each second t ∈ [1, 298] per window, δ≠0")
        + _row("Overall reversal rate", f"{base_rate:.3f}",
               "P(flip) unconditional — model base rate")
        + _row("Δ price range",    f"${delta_min:+,.0f} → ${delta_max:+,.0f}")
    )

    # ── Filter settings ───────────────────────────────────────────────────────
    fmh = meta.get("filter_market_hours", False)
    if fmh:
        fmh_str   = "✅ Enabled"
        fmh_note  = "only Mon–Fri 09:30–16:00 US/Eastern"
        hol_str   = "✅ Excluded (US Federal holiday calendar)"
        wknd_str  = "✅ Excluded (Monday–Friday only)"
        kept_str  = (f"{n_kept:,}" if isinstance(n_kept, int) else str(n_kept))
        kept_note = (
            f"{n_kept/n_windows*100:.1f}% of total"
            if isinstance(n_kept, int) and isinstance(n_windows, int) and n_windows > 0
            else ""
        )
    else:
        fmh_str   = "❌ Disabled (all hours used)"
        fmh_note  = "BTC trades 24/7 including weekends"
        hol_str   = "❌ Not applied"
        wknd_str  = "❌ Not applied"
        kept_str  = "all"
        kept_note = ""

    filter_rows = (
        _row("Market-hours filter", fmh_str, fmh_note)
        + _row("Weekends",          wknd_str)
        + _row("NYSE holidays",     hol_str,
               "US Federal calendar (Good Friday missing; Columbus/Veterans Day included)")
        + _row("Windows used",      kept_str, kept_note)
    )

    # ── Model config ──────────────────────────────────────────────────────────
    model_rows = (
        _row("Method",          "Gaussian kernel regression (Nadaraya–Watson)")
        + _row("Δ price bandwidth",
               f"{meta.get('delta_bw', 50.0):.1f} USD",
               "wider → smoother curve, less sensitive to small moves")
        + _row("Time bandwidth",
               f"{meta.get('time_bw', 30.0):.1f} s",
               "wider → smoother across the time axis")
        + _row("Grid δ bins",   str(meta.get("grid_delta_bins", 200)),
               "resolution of pre-computed lookup table")
        + _row("Grid time bins", str(meta.get("grid_time_bins", 60)))
        + _row("Lookup speed",  "< 0.1 ms per query", "O(1) after grid build")
    )

    # ── Interpretation guide ─────────────────────────────────────────────────
    interp_html = """
    <div class="card">
      <div class="card-title">How to Read the Charts</div>
      <ul class="interp">
        <li><b>Δ price (x-axis of heatmap / slices)</b> — signed USD move from window open.
            Positive = BTC has risen, negative = BTC has fallen.</li>
        <li><b>Time remaining (y-axis of heatmap, x-axis of surface)</b> — seconds left in
            the 5-minute Polymarket window (range 1–299).</li>
        <li><b>P(reversal) (colour / z-axis)</b> — probability that the current direction
            flips before the window ends.  Low = current trend likely holds.
            High = caution, reversal likely.</li>
        <li><b>Base rate dashed line</b> — unconditional reversal probability over the whole
            dataset.  Points above this line are "riskier than average".</li>
        <li><b>Colour scale</b> — green = low reversal risk, red = high reversal risk.</li>
      </ul>
    </div>"""

    # ── Assemble ──────────────────────────────────────────────────────────────
    return f"""
    <style>
      .info-wrap {{
        display: flex; flex-wrap: wrap; gap: 20px;
        padding: 28px; max-width: 1100px; margin: auto;
        font-family: 'Inter', 'Segoe UI', sans-serif;
      }}
      .card {{
        background: {PLOT_BG}; border: 1px solid {GRID_CLR};
        border-radius: 10px; padding: 20px 24px;
        flex: 1 1 300px; min-width: 280px;
      }}
      .card-title {{
        color: {TITLE_CLR}; font-size: 14px; font-weight: 600;
        margin-bottom: 14px; letter-spacing: .03em;
        border-bottom: 1px solid {GRID_CLR}; padding-bottom: 8px;
      }}
      table {{ border-collapse: collapse; width: 100%; }}
      td {{ padding: 5px 0; vertical-align: top; }}
      td.lbl {{ color: {TXT}; font-size: 12.5px; width: 42%; padding-right: 10px; }}
      td.val {{ color: {TXT_LIGHT}; font-size: 12.5px; font-weight: 500; }}
      span.note {{ display: block; color: #64748b; font-size: 11px; font-weight: 400; }}
      ul.interp {{ margin: 0; padding-left: 18px; }}
      ul.interp li {{ color: {TXT}; font-size: 12.5px; margin-bottom: 9px; line-height: 1.5; }}
      ul.interp li b {{ color: {TXT_LIGHT}; }}
    </style>
    <div class="info-wrap">
      {_section("Training Data", training_rows)}
      {_section("Filter Settings", filter_rows)}
      {_section("Model Configuration", model_rows)}
      {interp_html}
    </div>"""


# ── HTML template ─────────────────────────────────────────────────────────────

_TAB_CSS = f"""
<style>
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: {BG}; color: {TXT};
    font-family: 'Inter', 'Segoe UI', system-ui, sans-serif;
  }}
  .page-header {{
    padding: 22px 28px 0;
    border-bottom: 1px solid {GRID_CLR};
  }}
  .page-title {{
    color: {TITLE_CLR}; font-size: 20px; font-weight: 700;
    letter-spacing: -.01em; margin-bottom: 4px;
  }}
  .page-sub {{
    color: #64748b; font-size: 12.5px; margin-bottom: 18px;
  }}
  .tab-bar {{
    display: flex; gap: 4px;
  }}
  .tab-btn {{
    background: none; border: none; border-bottom: 3px solid transparent;
    color: {TXT}; font-size: 13.5px; font-weight: 500;
    padding: 8px 18px 10px; cursor: pointer;
    transition: color .15s, border-color .15s;
  }}
  .tab-btn:hover  {{ color: {TXT_LIGHT}; }}
  .tab-btn.active {{ color: {ACCENT}; border-bottom-color: {ACCENT}; }}
  .tab-content {{ display: none; }}
  .tab-content.active {{ display: block; }}
</style>
"""

_TAB_JS = """
<script>
function showTab(name, btn) {
  document.querySelectorAll('.tab-content').forEach(el => el.classList.remove('active'));
  document.querySelectorAll('.tab-btn').forEach(el => el.classList.remove('active'));
  document.getElementById('tab-' + name).classList.add('active');
  btn.classList.add('active');
}
</script>
"""


def _to_div(fig, first: bool = False) -> str:
    """Convert a Plotly figure to an HTML div string."""
    return fig.to_html(
        full_html=False,
        include_plotlyjs="cdn" if not first else True,
        config={"responsive": True},
    )


def _build_html(
    fig_surface,
    fig_heatmap,
    fig_slices,
    info_html: str,
    base_rate: float,
    first_ts: Any,
    last_ts: Any,
) -> str:
    period = ""
    if first_ts and first_ts != "unknown":
        period = f" · data: {str(first_ts)[:10]} → {str(last_ts)[:10]}"

    div_surface = _to_div(fig_surface, first=True)   # bundles Plotly JS inline
    div_heatmap = _to_div(fig_heatmap, first=False)
    div_slices  = _to_div(fig_slices,  first=False)

    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>BTC Reversal Model</title>
  {_TAB_CSS}
</head>
<body>
  <div class="page-header">
    <div class="page-title">BTC 5-min Reversal Probability Model</div>
    <div class="page-sub">
      P(price reverses direction before window ends) · base rate = {base_rate:.3f}{period}
    </div>
    <div class="tab-bar">
      <button class="tab-btn active"  onclick="showTab('surface',  this)">🗻 3D Surface</button>
      <button class="tab-btn"         onclick="showTab('heatmap',  this)">🗺 Heatmap</button>
      <button class="tab-btn"         onclick="showTab('slices',   this)">📈 Time Slices</button>
      <button class="tab-btn"         onclick="showTab('info',     this)">ℹ️ Dataset Info</button>
    </div>
  </div>

  <div id="tab-surface" class="tab-content active">{div_surface}</div>
  <div id="tab-heatmap" class="tab-content">{div_heatmap}</div>
  <div id="tab-slices"  class="tab-content">{div_slices}</div>
  <div id="tab-info"    class="tab-content">{info_html}</div>

  {_TAB_JS}
</body>
</html>"""


# ── Public API ────────────────────────────────────────────────────────────────

def save_html(
    output_path: str | Path = "output/reversal_model_viz.html",
    dataset_path: str | Path | None = None,
    filter_market_hours: bool = False,
    hours: int = 10_000,
    **model_kwargs,
) -> Path:
    """
    Load a ``ReversalModel`` and write the interactive HTML.

    Parameters
    ----------
    output_path           Destination HTML file.
    dataset_path          Path to reversal_dataset.parquet (model default if None).
    filter_market_hours   Whether the dataset was built with the NYSE filter.
                          Only used for the Dataset Info display; does NOT
                          re-filter data at viz time.
    hours                 Hours of history used when building the dataset
                          (for Dataset Info display only).
    **model_kwargs        Forwarded to ``ReversalModel`` (delta_bw, time_bw, etc.)
    """
    from btc_reversal_model.reversal_model import ReversalModel, DEFAULT_DATASET
    from btc_reversal_model.build_dataset import CACHE_FILE_1S

    ds_path = Path(dataset_path or DEFAULT_DATASET)
    print(f"Loading model from {ds_path} ...")
    model = ReversalModel(dataset_path=ds_path, **model_kwargs)

    # ── Gather metadata ───────────────────────────────────────────────────────
    cache_1s = Path(CACHE_FILE_1S)
    first_ts = last_ts = n_seconds = None
    if cache_1s.exists():
        try:
            import pandas as pd
            ts_series = pd.read_parquet(cache_1s).index
            first_ts  = str(ts_series.min())[:19]
            last_ts   = str(ts_series.max())[:19]
            n_seconds = len(ts_series)
        except Exception:
            pass

    n_samples = len(model._delta)
    # Each window of 300s contributes up to 298 samples (t=1..298, δ≠0).
    # Approximate total windows from sample count and average samples/window.
    n_windows_approx = round(n_samples / 280)   # ~280 non-zero samples per window

    meta: dict[str, Any] = {
        "first_ts":           first_ts,
        "last_ts":            last_ts,
        "n_seconds":          n_seconds,
        "n_windows_total":    n_windows_approx,
        "n_windows_kept":     n_windows_approx,
        "n_samples":          n_samples,
        "base_rate":          model.base_rate,
        "delta_min":          float(model._delta.min()),
        "delta_max":          float(model._delta.max()),
        "filter_market_hours": filter_market_hours,
        "hours":              hours,
        "delta_bw":           model_kwargs.get("delta_bw", 50.0),
        "time_bw":            model_kwargs.get("time_bw", 30.0),
        "grid_delta_bins":    model_kwargs.get("grid_delta_bins", 200),
        "grid_time_bins":     model_kwargs.get("grid_time_bins", 60),
    }

    # ── Build grid arrays ─────────────────────────────────────────────────────
    grid      = model._grid
    d_edges   = model._grid_delta_edges
    t_edges   = model._grid_time_edges
    d_centers = ((d_edges[:-1] + d_edges[1:]) / 2).tolist()
    t_centers = ((t_edges[:-1] + t_edges[1:]) / 2).tolist()
    base_rate = float(model.base_rate)

    print("Building figures ...")
    fig_s = _fig_surface(grid, d_centers, t_centers, base_rate)
    fig_h = _fig_heatmap(grid, d_centers, t_centers, base_rate)
    fig_l = _fig_slices(grid, d_centers, t_centers, base_rate)
    info  = _html_info(meta)

    html = _build_html(
        fig_s, fig_h, fig_l, info,
        base_rate=base_rate,
        first_ts=first_ts,
        last_ts=last_ts,
    )

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(html, encoding="utf-8")
    print(f"✓ Saved → {out.resolve()}")
    return out.resolve()


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate interactive HTML visualisation of the BTC reversal model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument("--output", "-o", default="output/reversal_model_viz.html",
                        help="Output HTML path.")
    parser.add_argument("--dataset", default=None,
                        help="reversal_dataset.parquet path (model default if omitted).")
    parser.add_argument("--filter-market-hours", action="store_true",
                        help="Mark the dataset as built with NYSE market-hours filter "
                             "(for display in the Dataset Info tab).")
    parser.add_argument("--hours", type=int, default=10_000,
                        help="Hours of BTC history used when building the dataset.")
    parser.add_argument("--delta-bins", type=int,  default=200)
    parser.add_argument("--time-bins",  type=int,  default=60)
    parser.add_argument("--delta-bw",   type=float, default=50.0)
    parser.add_argument("--time-bw",    type=float, default=30.0)
    args = parser.parse_args()

    save_html(
        output_path=args.output,
        dataset_path=args.dataset,
        filter_market_hours=args.filter_market_hours,
        hours=args.hours,
        grid_delta_bins=args.delta_bins,
        grid_time_bins=args.time_bins,
        delta_bw=args.delta_bw,
        time_bw=args.time_bw,
    )


if __name__ == "__main__":
    cli()
