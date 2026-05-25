"""
CLI for the Candle Pattern Analyzer.

Commands:
  run     Scan price data and build pattern probability tables.
  query   Query a specific pattern from a saved model.
  show    Print all patterns sorted by count.
"""
from __future__ import annotations

import json
from pathlib import Path

import click
import pandas as pd

from candle_pattern_analyzer.analyzer import CandlePatternAnalyzer, CandlePatternModel


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Candle Pattern Analyzer — compute directional probabilities from OHLCV data."""


@cli.command("run")
@click.option("--coin",        default="BTC",  show_default=True, help="Coin ID (must match config).")
@click.option("--timeframe",   default="1h",   show_default=True, help="Timeframe (e.g. 1h, 5m, 1d).")
@click.option("--num-candles", default=3,       show_default=True, help="Pattern length (number of candles).")
@click.option("--data-path",   default=None,   help="Direct path to parquet file (overrides coin/timeframe).")
@click.option("--output-dir",  default="output/candle_patterns", show_default=True)
@click.option("--crypto-config", default="config/crypto_data.yaml", show_default=True)
def run(coin: str, timeframe: str, num_candles: int, data_path: str | None,
        output_dir: str, crypto_config: str) -> None:
    """Scan OHLCV data and build pattern probability tables.

    \b
    Examples:
      poetry run candle-patterns run --coin BTC --timeframe 1h --num-candles 3
      poetry run candle-patterns run --coin BTC --timeframe 1d --num-candles 5
      poetry run candle-patterns run --data-path data/crypto/BTC/btc_1h.parquet --num-candles 4
    """
    # Resolve data path
    if data_path:
        parquet_path = Path(data_path)
    else:
        from crypto_data.config import load_crypto_config
        cfg = load_crypto_config(crypto_config)
        coin_cfg = cfg.get_coin(coin)
        if coin_cfg is None:
            raise click.BadParameter(f"Coin '{coin}' not found in {crypto_config}")
        tf = next((t for t in coin_cfg.timeframes if t.interval == timeframe), None)
        if tf is None:
            raise click.BadParameter(
                f"Timeframe '{timeframe}' not found for {coin}. "
                f"Available: {[t.interval for t in coin_cfg.timeframes]}"
            )
        parquet_path = cfg.data_path(coin, tf.filename)

    if not parquet_path.exists():
        raise click.ClickException(
            f"Data file not found: {parquet_path}\n"
            f"Run: poetry run crypto-data download --coin {coin} --timeframe {timeframe}"
        )

    click.echo(f"Loading {parquet_path} ...")
    df = pd.read_parquet(parquet_path)
    click.echo(f"  {len(df):,} candles  ({df['open_time'].min()} → {df['open_time'].max()})")

    click.echo(f"\nAnalysing {num_candles}-candle patterns ...")
    analyzer = CandlePatternAnalyzer(num_candles=num_candles)
    analyzer.fit(df)

    total = analyzer.total_patterns()
    all_probs = analyzer.all_probabilities()
    click.echo(f"  Total pattern observations : {total:,}")
    click.echo(f"  Unique patterns found      : {len(all_probs)}")
    click.echo(f"  Possible patterns          : {2 ** num_candles}")

    # Print table
    click.echo(f"\n{'Pattern':<30} {'Count':>8} {'P(UP)':>8} {'P(DOWN)':>8}")
    click.echo("─" * 58)
    for key, info in all_probs.items():
        pattern_str = " → ".join(info["pattern"])
        click.echo(
            f"  {pattern_str:<28} {info['count']:>8,} "
            f"{info['p_up']:>8.3f} {info['p_down']:>8.3f}"
        )

    # Save
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    tag = f"{coin.lower()}_{timeframe}_n{num_candles}"
    out_path = out_dir / f"{tag}.json"
    analyzer.save(out_path)
    click.echo(f"\nModel saved → {out_path}")

    # Also save a human-readable CSV
    csv_rows = []
    for key, info in all_probs.items():
        csv_rows.append({
            "pattern": " → ".join(info["pattern"]),
            "count": info["count"],
            "p_up": round(info["p_up"], 6),
            "p_down": round(info["p_down"], 6),
            "up_count": info["UP"],
            "down_count": info["DOWN"],
        })
    import csv
    csv_path = out_dir / f"{tag}.csv"
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["pattern", "count", "p_up", "p_down", "up_count", "down_count"])
        writer.writeheader()
        writer.writerows(csv_rows)
    click.echo(f"CSV saved   → {csv_path}")


@cli.command("query")
@click.argument("model_path")
@click.argument("pattern", nargs=-1, required=True)
def query(model_path: str, pattern: tuple[str, ...]) -> None:
    """Query P(UP) and P(DOWN) for a specific pattern from a saved model.

    \b
    PATTERN: space-separated candle directions, e.g.: UP DOWN UP

    Examples:
      poetry run candle-patterns query output/candle_patterns/btc_1h_n3.json UP DOWN UP
      poetry run candle-patterns query output/candle_patterns/btc_1d_n5.json DOWN DOWN UP UP DOWN
    """
    model = CandlePatternModel(model_path)
    result = model.query(list(pattern))

    click.echo(f"\nPattern:  {' → '.join(result['pattern'])}")
    click.echo(f"Count:    {result['count']:,} observations")
    click.echo(f"P(UP):    {result['UP']:.4f}  ({result['UP']*100:.1f}%)")
    click.echo(f"P(DOWN):  {result['DOWN']:.4f}  ({result['DOWN']*100:.1f}%)")

    if result["count"] == 0:
        click.echo("\n⚠ Pattern never observed in training data — returning 50/50 default.")


@cli.command("show")
@click.argument("model_path")
@click.option("--min-count", default=10, show_default=True, help="Minimum observations to show.")
@click.option("--sort-by", default="count", type=click.Choice(["count", "p_up", "p_down"]),
              show_default=True)
def show(model_path: str, min_count: int, sort_by: str) -> None:
    """Print all patterns from a saved model.

    \b
    Example:
      poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json
      poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json --sort-by p_up
    """
    model = CandlePatternModel(model_path)
    table = model.all_patterns()

    filtered = {k: v for k, v in table.items() if v["count"] >= min_count}
    if sort_by == "count":
        rows = sorted(filtered.values(), key=lambda x: -x["count"])
    elif sort_by == "p_up":
        rows = sorted(filtered.values(), key=lambda x: -x["p_up"])
    else:
        rows = sorted(filtered.values(), key=lambda x: -x["p_down"])

    click.echo(f"\nModel: {model_path}  |  num_candles={model.num_candles}  |  "
               f"showing {len(rows)} patterns (min_count={min_count})\n")
    click.echo(f"{'Pattern':<35} {'Count':>8} {'P(UP)':>8} {'P(DOWN)':>8}  {'Edge'}")
    click.echo("─" * 72)
    for v in rows:
        pattern_str = " → ".join(v["pattern"])
        edge = v["p_up"] - 0.5
        edge_str = f"{'+' if edge >= 0 else ''}{edge:.3f}"
        click.echo(
            f"  {pattern_str:<33} {v['count']:>8,} "
            f"{v['p_up']:>8.4f} {v['p_down']:>8.4f}  {edge_str}"
        )


if __name__ == "__main__":
    cli()
