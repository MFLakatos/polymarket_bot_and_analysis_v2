"""Price chart visualization for crypto OHLCV data."""
from __future__ import annotations

import webbrowser
from pathlib import Path
from typing import Optional

import pandas as pd


def plot_price(
    df: pd.DataFrame,
    coin_id: str,
    interval: str,
    output_path: Optional[str | Path] = None,
    style: str = "dark",
    figsize: tuple[int, int] = (16, 9),
    auto_open: bool = False,
    show_indicators: bool = True,
) -> Path:
    """Generate an interactive HTML price chart using Plotly.

    Args:
        df:            OHLCV DataFrame (with optional indicator columns).
        coin_id:       Coin name for chart title.
        interval:      Timeframe string for chart title.
        output_path:   Directory to save the HTML file. Defaults to output/charts.
        style:         "dark" or "light".
        figsize:       Figure size in pixels (width, height).
        auto_open:     Open browser after generating.
        show_indicators: Add SMA/BB overlays if present in DataFrame.

    Returns:
        Path to the saved HTML file.
    """
    try:
        import plotly.graph_objects as go
        from plotly.subplots import make_subplots
    except ImportError:
        raise ImportError("plotly is required: poetry install")

    template = "plotly_dark" if style == "dark" else "plotly_white"

    # Build subplots: price + volume + RSI
    has_rsi = "rsi" in df.columns
    rows = 3 if has_rsi else 2
    row_heights = [0.6, 0.2, 0.2] if has_rsi else [0.7, 0.3]

    fig = make_subplots(
        rows=rows, cols=1,
        shared_xaxes=True,
        vertical_spacing=0.02,
        row_heights=row_heights,
        subplot_titles=[
            f"{coin_id} / USDT — {interval}",
            "Volume",
            "RSI (14)" if has_rsi else None,
        ],
    )

    # ── Candlestick ──────────────────────────────────────────────────────────
    fig.add_trace(
        go.Candlestick(
            x=df["open_time"],
            open=df["open"], high=df["high"],
            low=df["low"], close=df["close"],
            name="OHLC",
            increasing_line_color="#26a69a",
            decreasing_line_color="#ef5350",
        ),
        row=1, col=1,
    )

    # ── Indicator overlays ───────────────────────────────────────────────────
    if show_indicators:
        colors_sma = ["#ff9800", "#2196f3", "#9c27b0"]
        for i, col in enumerate([c for c in df.columns if c.startswith("sma_")]):
            period = col.split("_")[1]
            fig.add_trace(
                go.Scatter(
                    x=df["open_time"], y=df[col],
                    mode="lines", name=f"SMA {period}",
                    line=dict(width=1, color=colors_sma[i % len(colors_sma)]),
                ),
                row=1, col=1,
            )

        if "bb_upper" in df.columns:
            fig.add_trace(
                go.Scatter(
                    x=df["open_time"], y=df["bb_upper"],
                    mode="lines", name="BB Upper",
                    line=dict(width=1, color="#78909c", dash="dot"),
                ),
                row=1, col=1,
            )
            fig.add_trace(
                go.Scatter(
                    x=df["open_time"], y=df["bb_lower"],
                    mode="lines", name="BB Lower",
                    line=dict(width=1, color="#78909c", dash="dot"),
                    fill="tonexty", fillcolor="rgba(120,144,156,0.05)",
                ),
                row=1, col=1,
            )

    # ── Volume bars ──────────────────────────────────────────────────────────
    colors = ["#26a69a" if c >= o else "#ef5350"
              for c, o in zip(df["close"], df["open"])]
    fig.add_trace(
        go.Bar(x=df["open_time"], y=df["volume"], name="Volume",
               marker_color=colors, opacity=0.6),
        row=2, col=1,
    )

    # ── RSI ──────────────────────────────────────────────────────────────────
    if has_rsi:
        fig.add_trace(
            go.Scatter(x=df["open_time"], y=df["rsi"], name="RSI",
                       line=dict(width=1.5, color="#ff5722")),
            row=3, col=1,
        )
        fig.add_hline(y=70, line_dash="dash", line_color="red", opacity=0.5, row=3, col=1)
        fig.add_hline(y=30, line_dash="dash", line_color="green", opacity=0.5, row=3, col=1)

    # ── Layout ───────────────────────────────────────────────────────────────
    fig.update_layout(
        template=template,
        title=f"{coin_id} — {interval} chart",
        xaxis_rangeslider_visible=False,
        width=figsize[0] * 80,
        height=figsize[1] * 80,
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
        margin=dict(l=60, r=30, t=80, b=40),
    )

    # ── Save ─────────────────────────────────────────────────────────────────
    out_dir = Path(output_path or "output/charts")
    out_dir.mkdir(parents=True, exist_ok=True)
    out_file = out_dir / f"{coin_id.lower()}_{interval}_chart.html"
    fig.write_html(str(out_file), include_plotlyjs="cdn")

    if auto_open:
        webbrowser.open(f"file://{out_file.resolve()}")

    return out_file
