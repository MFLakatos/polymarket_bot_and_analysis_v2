"""
BTC Reversal Model – Interactive Visualizer
============================================
Generates a self-contained HTML file with three interactive Plotly views of
the pre-computed P(reversal) surface:

  1. 3D Surface  – rotate & zoom the full probability landscape.
  2. Heatmap     – top-down view; hover to read exact values.
  3. Time slices – P(reversal) vs Δprice at a set of fixed time-remaining
                   thresholds (good for finding decision thresholds).

Usage
-----
    # From Python
    from btc_reversal_model.visualize import save_html
    save_html("output/reversal_viz.html")

    # From CLI (after poetry install)
    visualize-reversal --output output/reversal_viz.html

The HTML is fully self-contained (Plotly JS bundled inline, ~3 MB).
No internet connection required to open it.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np

# ── Colour palette (matches dark dashboard theme) ─────────────────────────────
BG       = "#0f172a"
PAPER_BG = "#0f172a"
PLOT_BG  = "#1e293b"
GRID_CLR = "#334155"
TXT      = "#94a3b8"
TITLE_CLR = "#e2e8f0"

# Time-slice highlight colours (one per slice line)
_SLICE_COLOURS = [
    "#3b82f6", "#06b6d4", "#22c55e", "#eab308",
    "#f97316", "#ef4444", "#a855f7",
]


# ── Main builder ─────────────────────────────────────────────────────────────

def build_figure(model):  # type: ignore[no-untyped-def]
    """
    Build an interactive Plotly figure from a loaded ``ReversalModel``.

    The figure contains three view modes selectable via a dropdown:
      - 3D Surface
      - Heatmap
      - Time slices (P vs Δprice at several remaining-time thresholds)

    Parameters
    ----------
    model   A loaded ``btc_reversal_model.ReversalModel`` instance.

    Returns
    -------
    plotly.graph_objects.Figure
    """
    import plotly.graph_objects as go

    grid      = model._grid                          # shape (n_delta, n_time)
    d_edges   = model._grid_delta_edges
    t_edges   = model._grid_time_edges
    d_centers = ((d_edges[:-1] + d_edges[1:]) / 2).tolist()
    t_centers = ((t_edges[:-1] + t_edges[1:]) / 2).tolist()
    base_rate = float(model.base_rate)

    nd, nt = grid.shape

    # ── 1. 3D Surface ─────────────────────────────────────────────────────────
    # Plotly Surface: z[i][j] = value at y=d_centers[i], x=t_centers[j]
    surf_z = grid.tolist()

    hover_3d = (
        "<b>Time remaining:</b> %{x:.0f} s<br>"
        "<b>Δ price:</b> $%{y:,.0f}<br>"
        "<b>P(reversal):</b> %{z:.3f}<extra></extra>"
    )

    trace_3d = go.Surface(
        x=t_centers,
        y=d_centers,
        z=surf_z,
        colorscale="RdYlGn_r",   # green=low risk → red=high risk
        cmin=0.0,
        cmax=1.0,
        opacity=0.92,
        hovertemplate=hover_3d,
        colorbar=dict(
            title=dict(text="P(reversal)", font=dict(color=TXT, size=12)),
            tickfont=dict(color=TXT),
            bgcolor=BG,
            bordercolor=GRID_CLR,
            len=0.75,
        ),
        name="3D Surface",
        visible=True,
    )

    # ── 2. Heatmap ────────────────────────────────────────────────────────────
    hover_hm = (
        "<b>Time remaining:</b> %{x:.0f} s<br>"
        "<b>Δ price:</b> $%{y:,.0f}<br>"
        "<b>P(reversal):</b> %{z:.3f}<extra></extra>"
    )

    trace_hm = go.Heatmap(
        x=t_centers,
        y=d_centers,
        z=surf_z,
        colorscale="RdYlGn_r",
        zmin=0.0,
        zmax=1.0,
        hovertemplate=hover_hm,
        colorbar=dict(
            title=dict(text="P(reversal)", font=dict(color=TXT, size=12)),
            tickfont=dict(color=TXT),
            bgcolor=BG,
            bordercolor=GRID_CLR,
            len=0.75,
        ),
        name="Heatmap",
        visible=False,
    )

    # Contour overlay on heatmap
    trace_contour = go.Contour(
        x=t_centers,
        y=d_centers,
        z=surf_z,
        colorscale="RdYlGn_r",
        showscale=False,
        contours=dict(
            start=0.1, end=0.9, size=0.1,
            coloring="none",
            showlabels=True,
            labelfont=dict(size=9, color="white"),
        ),
        line=dict(width=0.8),
        hoverinfo="skip",
        name="Contours",
        visible=False,
    )

    # ── 3. Time-slice lines ───────────────────────────────────────────────────
    slice_seconds = [30, 60, 90, 120, 150, 180, 240]
    slice_traces: list[go.Scatter] = []

    for idx, t_target in enumerate(slice_seconds):
        # Find nearest time-center index
        ti = int(np.argmin(np.abs(np.array(t_centers) - t_target)))
        actual_t = t_centers[ti]
        probs = grid[:, ti].tolist()
        colour = _SLICE_COLOURS[idx % len(_SLICE_COLOURS)]

        slice_traces.append(
            go.Scatter(
                x=d_centers,
                y=probs,
                mode="lines",
                line=dict(color=colour, width=2),
                name=f"{actual_t:.0f}s remaining",
                hovertemplate=(
                    f"<b>Time remaining:</b> {actual_t:.0f} s<br>"
                    "<b>Δ price:</b> $%{x:,.0f}<br>"
                    "<b>P(reversal):</b> %{y:.3f}<extra></extra>"
                ),
                visible=False,
            )
        )

    # Base-rate reference line (shown in slice view only)
    base_line = go.Scatter(
        x=[d_centers[0], d_centers[-1]],
        y=[base_rate, base_rate],
        mode="lines",
        line=dict(color="#64748b", width=1.5, dash="dash"),
        name=f"Base rate ({base_rate:.3f})",
        hoverinfo="skip",
        visible=False,
    )

    # ── Assemble all traces ───────────────────────────────────────────────────
    n_slice = len(slice_traces)
    all_traces = [trace_3d, trace_hm, trace_contour] + slice_traces + [base_line]

    def _vis(surface=False, heatmap=False, slices=False):
        return (
            [surface, heatmap, heatmap]   # 3d, hm, contour
            + [slices] * n_slice
            + [slices]                    # base_line
        )

    buttons = [
        dict(
            label="🗻  3D Surface",
            method="update",
            args=[
                {"visible": _vis(surface=True)},
                {
                    "scene.xaxis.title": "Time remaining (s)",
                    "scene.yaxis.title": "Δ price (USD)",
                    "scene.zaxis.title": "P(reversal)",
                    "xaxis.visible": False,
                    "yaxis.visible": False,
                    "xaxis.title.text": "",
                    "yaxis.title.text": "",
                },
            ],
        ),
        dict(
            label="🗺  Heatmap",
            method="update",
            args=[
                {"visible": _vis(heatmap=True)},
                {
                    "xaxis.visible": True,
                    "yaxis.visible": True,
                    "xaxis.title.text": "Time remaining (s)",
                    "yaxis.title.text": "Δ price (USD)",
                },
            ],
        ),
        dict(
            label="📈  Time slices",
            method="update",
            args=[
                {"visible": _vis(slices=True)},
                {
                    "xaxis.visible": True,
                    "yaxis.visible": True,
                    "xaxis.title.text": "Δ price (USD)",
                    "yaxis.title.text": "P(reversal)",
                },
            ],
        ),
    ]

    fig = go.Figure(data=all_traces)

    fig.update_layout(
        title=dict(
            text=(
                "BTC 5-min Reversal Probability Model"
                f"<br><sup style='color:{TXT}'>P(price reverses direction before window end)"
                f" · base rate = {base_rate:.3f}</sup>"
            ),
            font=dict(color=TITLE_CLR, size=18),
            x=0.03,
            xanchor="left",
        ),
        paper_bgcolor=PAPER_BG,
        plot_bgcolor=PLOT_BG,
        font=dict(color=TXT, size=12),
        height=700,
        margin=dict(l=70, r=40, t=100, b=70),

        # Axis labels (visible in heatmap / slice views)
        xaxis=dict(
            title=dict(text="Time remaining (s)", font=dict(color=TXT)),
            gridcolor=GRID_CLR,
            zerolinecolor=GRID_CLR,
            tickfont=dict(color=TXT),
            visible=False,   # start hidden (3D surface is first)
        ),
        yaxis=dict(
            title=dict(text="Δ price (USD)", font=dict(color=TXT)),
            gridcolor=GRID_CLR,
            zerolinecolor=GRID_CLR,
            tickfont=dict(color=TXT),
            visible=False,
        ),

        # 3D scene styling
        scene=dict(
            bgcolor=PLOT_BG,
            xaxis=dict(
                title="Time remaining (s)",
                gridcolor=GRID_CLR,
                backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT),
                titlefont=dict(color=TXT),
            ),
            yaxis=dict(
                title="Δ price (USD)",
                gridcolor=GRID_CLR,
                backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT),
                titlefont=dict(color=TXT),
            ),
            zaxis=dict(
                title="P(reversal)",
                range=[0, 1],
                gridcolor=GRID_CLR,
                backgroundcolor=PLOT_BG,
                tickfont=dict(color=TXT),
                titlefont=dict(color=TXT),
            ),
            camera=dict(eye=dict(x=1.6, y=-1.6, z=0.9)),
        ),

        # Legend
        legend=dict(
            bgcolor=BG,
            bordercolor=GRID_CLR,
            borderwidth=1,
            font=dict(color=TXT),
            x=1.02,
            y=0.5,
        ),

        # View selector dropdown
        updatemenus=[
            dict(
                type="buttons",
                direction="left",
                x=0.0,
                xanchor="left",
                y=1.09,
                yanchor="top",
                bgcolor=PLOT_BG,
                bordercolor=GRID_CLR,
                font=dict(color=TXT),
                buttons=buttons,
                showactive=True,
                active=0,
            )
        ],

        # Hover style
        hoverlabel=dict(
            bgcolor="#1e293b",
            bordercolor="#475569",
            font=dict(color="white", size=12),
        ),
    )

    return fig


# ── Public API ────────────────────────────────────────────────────────────────

def save_html(
    output_path: str | Path = "output/reversal_model_viz.html",
    dataset_path: str | Path | None = None,
    **model_kwargs,
) -> Path:
    """
    Load (or build) a ``ReversalModel`` and write the interactive HTML.

    Parameters
    ----------
    output_path     Where to write the HTML file.
    dataset_path    Path to the reversal_dataset.parquet file.
                    Defaults to the model's ``DEFAULT_DATASET``.
    **model_kwargs  Extra kwargs forwarded to ``ReversalModel.__init__``
                    (e.g. ``delta_bw``, ``time_bw``, ``grid_delta_bins``).

    Returns
    -------
    Path  Resolved path of the written HTML file.
    """
    from btc_reversal_model.reversal_model import ReversalModel, DEFAULT_DATASET

    ds_path = dataset_path or DEFAULT_DATASET
    print(f"Loading model from {ds_path} ...")
    model = ReversalModel(dataset_path=ds_path, **model_kwargs)

    print("Building figure ...")
    fig = build_figure(model)

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs=True, full_html=True)
    print(f"✓ Saved → {out.resolve()}")
    return out.resolve()


# ── CLI ───────────────────────────────────────────────────────────────────────

def cli() -> None:
    parser = argparse.ArgumentParser(
        description="Generate an interactive HTML visualisation of the BTC reversal model.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    parser.add_argument(
        "--output", "-o",
        default="output/reversal_model_viz.html",
        help="Path for the output HTML file.",
    )
    parser.add_argument(
        "--dataset",
        default=None,
        help="Path to reversal_dataset.parquet (defaults to model default).",
    )
    parser.add_argument(
        "--delta-bins", type=int, default=200,
        help="Grid resolution on the Δprice axis.",
    )
    parser.add_argument(
        "--time-bins", type=int, default=60,
        help="Grid resolution on the time-remaining axis.",
    )
    parser.add_argument(
        "--delta-bw", type=float, default=50.0,
        help="Kernel bandwidth (USD) for the Δprice dimension.",
    )
    parser.add_argument(
        "--time-bw", type=float, default=30.0,
        help="Kernel bandwidth (s) for the time-remaining dimension.",
    )
    args = parser.parse_args()

    save_html(
        output_path=args.output,
        dataset_path=args.dataset,
        grid_delta_bins=args.delta_bins,
        grid_time_bins=args.time_bins,
        delta_bw=args.delta_bw,
        time_bw=args.time_bw,
    )


if __name__ == "__main__":
    cli()
