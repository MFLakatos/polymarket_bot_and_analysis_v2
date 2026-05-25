"""CLI entry point for the BTC 5m reversal bot."""
from __future__ import annotations

import click


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """BTC 5-Minute Reversal Bot — live trading and simulation."""


@cli.command("run")
@click.option("--config", default="config/btc_5m_bot.yaml", show_default=True,
              help="Path to btc_5m_bot.yaml config.")
def run(config: str) -> None:
    """Run the live (or monitor-only) bot.

    Set trading.enabled=false in config for monitor-only mode.

    Example:
      poetry run btc-5m-bot run
      poetry run btc-5m-bot run --config config/btc_5m_bot.yaml
    """
    from btc_5m_trader.bot import BTC5mBot
    bot = BTC5mBot(config_path=config)
    bot.run()


@cli.command("simulate")
@click.argument("trades_csv")
@click.option("--config", default="config/btc_5m_bot.yaml", show_default=True)
@click.option("--output", default="output/simulation/sim_report.csv", show_default=True)
@click.option("--keyword", default="5m", show_default=True,
              help="Filter trades by market title keyword.")
def simulate(trades_csv: str, config: str, output: str, keyword: str) -> None:
    """Simulate bot performance against a historical detected_trades CSV.

    TRADES_CSV: path to detected_trades_*.csv from the copy trading bot.

    Example:
      poetry run btc-5m-bot simulate data/copy_trading/detected_trades_0x476639_20260522.csv
      poetry run btc-5m-bot simulate data/copy_trading/detected_trades_0x476639.csv --keyword "5m"
    """
    import yaml
    from pathlib import Path
    from btc_reversal_model import ReversalModel
    from btc_5m_trader.simulator import run_simulation

    p = Path(config)
    if not p.exists():
        raise click.ClickException(f"Config not found: {p}")
    cfg = yaml.safe_load(p.read_text()) or {}

    general = cfg.get("general", {})
    dataset = general.get("reversal_dataset_path", "data/crypto/BTC/reversal_dataset.parquet")

    click.echo("Loading reversal model...")
    model = ReversalModel(
        dataset_path=dataset,
        delta_bw=float(general.get("delta_bandwidth_usd", 50.0)),
        time_bw=float(general.get("time_bandwidth_seconds", 30.0)),
    )

    tiers = cfg.get("trading", {}).get("tiers", [
        {"max_reversal_prob": 0.10, "shares": 3},
        {"max_reversal_prob": 0.20, "shares": 2},
        {"max_reversal_prob": 0.30, "shares": 1},
    ])

    click.echo(f"Running simulation on {trades_csv}...")
    run_simulation(
        detected_trades_path=trades_csv,
        model=model,
        tiers=tiers,
        output_path=output,
        target_market_keyword=keyword,
    )


@cli.command("build-dataset")
@click.option("--hours", default=10000, show_default=True, help="Hours of 1s data to download.")
@click.option("--output", default="data/crypto/BTC/reversal_dataset.parquet", show_default=True)
def build_dataset(hours: int, output: str) -> None:
    """Download 1s BTC data and build the reversal probability dataset.

    Example:
      poetry run btc-5m-bot build-dataset
      poetry run btc-5m-bot build-dataset --hours 20000
    """
    from btc_reversal_model.build_dataset import build
    build(hours=hours, output=output)


if __name__ == "__main__":
    cli()
