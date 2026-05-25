"""CLI for the crypto_data module.

Entry point: `poetry run crypto-data`

Commands:
  download   Download price data for all configured coins/timeframes.
  info       Show available datasets and their metadata.
  plot       Generate an interactive HTML price chart.
  analyze    Print basic statistics + indicator summary for a dataset.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

import click

from crypto_data.config import load_crypto_config
from crypto_data.filters import FilterConfig, SessionFilterConfig, AVAILABLE_SESSIONS, session_info
from crypto_data.downloaders import BinanceDownloader
from crypto_data.loaders import PriceLoader


def _get_config(config_path: Optional[str]):
    return load_crypto_config(config_path)


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option(
    "--config", "config_path",
    type=click.Path(dir_okay=False),
    default=None,
    help="Path to crypto_data.yaml (defaults to config/crypto_data.yaml).",
)
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]) -> None:
    """Crypto price data — download, load, and visualize OHLCV data."""
    ctx.ensure_object(dict)
    ctx.obj["config_path"] = config_path
    ctx.obj["config"] = _get_config(config_path)


@cli.command("download")
@click.option("--coin", default=None, help="Specific coin to download (default: all from config).")
@click.option("--timeframe", default=None, help="Specific timeframe (e.g. 1h). Default: all.")
@click.option("--force", is_flag=True, default=False, help="Re-download even if cached.")
@click.pass_context
def download(ctx: click.Context, coin: Optional[str], timeframe: Optional[str], force: bool) -> None:
    """Download OHLCV price data from Binance.

    Examples:
      poetry run crypto-data download               # all coins + timeframes
      poetry run crypto-data download --coin BTC    # all BTC timeframes
      poetry run crypto-data download --coin BTC --timeframe 1h
      poetry run crypto-data download --force       # ignore cache
    """
    cfg = ctx.obj["config"]
    dl = BinanceDownloader(cfg)

    if coin and timeframe:
        click.echo(f"Downloading {coin} {timeframe}...")
        df = dl.download(coin, timeframe, force=force, verbose=True)
        click.echo(f"Done: {len(df):,} candles.")
    elif coin:
        coin_cfg = cfg.get_coin(coin)
        if coin_cfg is None:
            raise click.BadParameter(f"Coin '{coin}' not found in config.")
        click.echo(f"Downloading all timeframes for {coin}...")
        for tf in coin_cfg.timeframes:
            click.echo(f"\n  [{coin}] {tf.interval}...")
            dl.download(coin, tf.interval, force=force, verbose=True)
    else:
        click.echo("Downloading all coins and timeframes from config...")
        dl.download_all(force=force, verbose=True)

    click.echo("\nDownload complete.")


@cli.command("info")
@click.option("--coin", default=None, help="Filter by coin.")
@click.pass_context
def info(ctx: click.Context, coin: Optional[str]) -> None:
    """Show available datasets and their metadata (rows, date range, file size)."""
    cfg = ctx.obj["config"]
    loader = PriceLoader(cfg)

    coins_to_check = [cfg.get_coin(coin)] if coin else cfg.coins
    if coin and coins_to_check[0] is None:
        raise click.BadParameter(f"Coin '{coin}' not found in config.")

    for coin_cfg in coins_to_check:
        click.echo(f"\n{'═' * 50}")
        click.echo(f"  {coin_cfg.name} ({coin_cfg.id})  —  symbol: {coin_cfg.symbol}")
        click.echo(f"{'═' * 50}")
        for tf in coin_cfg.timeframes:
            meta = loader.info(coin_cfg.id, tf.interval)
            if meta["exists"]:
                click.echo(
                    f"  {tf.interval:>5}  {meta['rows']:>9,} rows  "
                    f"{meta['start'][:10]} → {meta['end'][:10]}  "
                    f"({meta['size_mb']} MB)"
                )
            else:
                click.echo(f"  {tf.interval:>5}  [not downloaded]  path: {meta['path']}")


@cli.command("plot")
@click.option("--coin", required=True, help="Coin to plot (e.g. BTC).")
@click.option("--timeframe", required=True, help="Timeframe to plot (e.g. 1h).")
@click.option("--lookback", default=500, show_default=True, help="Number of candles to show.")
@click.option("--no-indicators", is_flag=True, default=False, help="Disable indicator overlays.")
@click.option("--open", "auto_open", is_flag=True, default=False, help="Auto-open in browser.")
@click.option("--no-weekends", is_flag=True, default=False, help="Exclude weekend candles.")
@click.option("--nyse-only", is_flag=True, default=False, help="Keep only NYSE trading hours.")
@click.option("--no-holidays", is_flag=True, default=False, help="Exclude NYSE holidays.")
@click.option("--session", "sessions", multiple=True, default=(),
              help="Keep only this session (repeat for multiple).")
@click.pass_context
def plot(
    ctx: click.Context,
    coin: str,
    timeframe: str,
    lookback: int,
    no_indicators: bool,
    auto_open: bool,
    no_weekends: bool,
    nyse_only: bool,
    no_holidays: bool,
    sessions: tuple,
) -> None:
    """Generate an interactive HTML candlestick chart.

    \b
    Examples:
      poetry run crypto-data plot --coin BTC --timeframe 1h --lookback 200 --open
      poetry run crypto-data plot --coin BTC --timeframe 1h --no-weekends --open
      poetry run crypto-data plot --coin BTC --timeframe 1h --session New_York --open
    """
    from crypto_data.visualization import plot_price

    cfg = ctx.obj["config"]
    loader = PriceLoader(cfg)

    filters = _build_filters(cfg, no_weekends, nyse_only, no_holidays, sessions)
    click.echo(f"Loading {coin} {timeframe} (last {lookback} candles)...")
    df = loader.load(coin, timeframe, lookback=lookback,
                     compute_indicators=not no_indicators, filters=filters)

    click.echo("Generating chart...")
    out_path = plot_price(
        df,
        coin_id=coin,
        interval=timeframe,
        output_path=cfg.visualization.output_path,
        style=cfg.visualization.style,
        figsize=tuple(cfg.visualization.figsize),
        auto_open=auto_open,
        show_indicators=not no_indicators,
    )
    click.echo(f"Chart saved: {out_path}")
    if not auto_open:
        click.echo(f"Open with: open {out_path}")


@cli.command("analyze")
@click.option("--coin", required=True, help="Coin to analyze.")
@click.option("--timeframe", required=True, help="Timeframe to analyze.")
@click.option("--lookback", default=200, show_default=True, help="Candles to include.")
@click.option("--no-weekends", is_flag=True, default=False, help="Exclude weekend candles.")
@click.option("--nyse-only", is_flag=True, default=False, help="Keep only NYSE trading hours.")
@click.option("--no-holidays", is_flag=True, default=False, help="Exclude NYSE holidays.")
@click.option("--session", "sessions", multiple=True, default=(),
              help=f"Keep only this session (repeat for multiple). Available: {', '.join(AVAILABLE_SESSIONS)}")
@click.pass_context
def analyze(ctx: click.Context, coin: str, timeframe: str, lookback: int,
            no_weekends: bool, nyse_only: bool, no_holidays: bool,
            sessions: tuple) -> None:
    """Print basic statistics and latest indicator values.

    \b
    Examples:
      poetry run crypto-data analyze --coin BTC --timeframe 1d
      poetry run crypto-data analyze --coin BTC --timeframe 1h --no-weekends
      poetry run crypto-data analyze --coin BTC --timeframe 1h --session New_York
      poetry run crypto-data analyze --coin BTC --timeframe 1h --nyse-only --no-weekends
    """
    cfg = ctx.obj["config"]
    loader = PriceLoader(cfg)

    filters = _build_filters(cfg, no_weekends, nyse_only, no_holidays, sessions)
    df = loader.load(coin, timeframe, lookback=lookback, filters=filters)
    last = df.iloc[-1]

    click.echo(f"\n{'═' * 50}")
    click.echo(f"  {coin} / {timeframe}  —  {len(df)} candles")
    click.echo(f"  {df['open_time'].iloc[0]} → {df['open_time'].iloc[-1]}")
    click.echo(f"{'═' * 50}")
    click.echo(f"  Close:   ${last['close']:>12,.2f}")
    click.echo(f"  High:    ${df['high'].max():>12,.2f}")
    click.echo(f"  Low:     ${df['low'].min():>12,.2f}")
    click.echo(f"  Volume:  {df['volume'].sum():>15,.0f}")

    if "rsi" in df.columns:
        click.echo(f"\n  RSI:     {last.get('rsi', float('nan')):.1f}")
    for col in [c for c in df.columns if c.startswith("sma_")]:
        click.echo(f"  {col.upper():10}: ${last[col]:>12,.2f}")
    if "macd" in df.columns:
        click.echo(f"  MACD:    {last['macd']:.2f}  Signal: {last['macd_signal']:.2f}")
    if "bb_upper" in df.columns:
        click.echo(f"  BB:      [{last['bb_lower']:.0f} — {last['bb_upper']:.0f}]  "
                   f"Width: {last['bb_width']:.3f}")

# ── Helper to build FilterConfig from CLI flags ───────────────────────────────

def _build_filters(cfg, no_weekends: bool, nyse_only: bool,
                   no_holidays: bool, sessions: tuple) -> FilterConfig:
    """Build FilterConfig merging config defaults with CLI flag overrides."""
    base = cfg.filters.to_filter_config()
    return FilterConfig(
        exclude_weekends=base.exclude_weekends or no_weekends,
        exclude_outside_nyse=base.exclude_outside_nyse or nyse_only,
        exclude_nyse_holidays=base.exclude_nyse_holidays or no_holidays,
        sessions=SessionFilterConfig(
            enabled=base.sessions.enabled or bool(sessions),
            active=list(base.sessions.active) + list(sessions) if sessions
                   else list(base.sessions.active),
        ),
    )


@cli.command("sessions")
def sessions_cmd() -> None:
    """List all available trading sessions and their UTC windows.

    Example:
      poetry run crypto-data sessions
    """
    click.echo("\nAvailable trading sessions:\n")
    click.echo(session_info())
    click.echo()
    click.echo("Usage examples:")
    click.echo("  poetry run crypto-data analyze --coin BTC --timeframe 1h --session New_York")
    click.echo("  poetry run crypto-data plot --coin BTC --timeframe 1h --session Europe --session New_York")
    click.echo("  poetry run crypto-data analyze --coin BTC --timeframe 1h --no-weekends --nyse-only")
    click.echo()
    click.echo("Or set in config/crypto_data.yaml:")
    click.echo("  filters:")
    click.echo("    exclude_weekends: true")
    click.echo("    sessions:")
    click.echo("      enabled: true")
    click.echo("      active_sessions: [New_York, Europe]")

