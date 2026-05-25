"""CLI for wallet position analysis."""
from __future__ import annotations

import click


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
def cli() -> None:
    """Analyze and visualize wallet trade positions."""


@cli.command("analyze")
@click.argument("wallet_id")
@click.option("--data-dir",   default="data/wallets",          show_default=True)
@click.option("--output-dir", default="output/wallet_analysis", show_default=True)
def analyze(wallet_id: str, data_dir: str, output_dir: str) -> None:
    """Analyze all trades for a wallet.

    WALLET_ID: wallet address or folder name under data/wallets/.

    \b
    Examples:
      poetry run analyze-wallet analyze 0x476639d9845d7a0261cb005dae6473f089ff5a03
      poetry run analyze-wallet analyze 0x476639  (partial match)
    """
    from analyze_wallet_positions.analyze_trades import analyze as _analyze
    from pathlib import Path

    # Support partial address match
    data_path = Path(data_dir)
    if not (data_path / wallet_id).exists():
        matches = [d.name for d in data_path.iterdir() if d.name.startswith(wallet_id)]
        if len(matches) == 1:
            wallet_id = matches[0]
            click.echo(f"Resolved wallet: {wallet_id}")
        elif len(matches) > 1:
            raise click.BadParameter(f"Ambiguous wallet prefix. Matches: {matches}")
        else:
            raise click.BadParameter(f"No wallet folder found matching '{wallet_id}' in {data_dir}")

    _analyze(wallet_id=wallet_id, data_dir=data_dir, output_dir=output_dir)


@cli.command("plot")
@click.argument("wallet_id")
@click.option("--output-dir", default="output/wallet_analysis", show_default=True)
@click.option("--data-dir",   default="data/wallets",          show_default=True)
def plot(wallet_id: str, output_dir: str, data_dir: str) -> None:
    """Generate per-window plots for a wallet.

    Requires analyze to have been run first (needs trades_enriched.csv).

    \b
    Examples:
      poetry run analyze-wallet plot 0x476639d9845d7a0261cb005dae6473f089ff5a03
    """
    from pathlib import Path
    from analyze_wallet_positions.plot_windows import plot_all

    # Resolve partial address
    data_path = Path(data_dir)
    if not (data_path / wallet_id).exists():
        matches = [d.name for d in data_path.iterdir() if d.name.startswith(wallet_id)]
        if len(matches) == 1:
            wallet_id = matches[0]

    trades_csv = Path(output_dir) / wallet_id / "trades_enriched.csv"
    if not trades_csv.exists():
        raise click.ClickException(
            f"trades_enriched.csv not found at {trades_csv}\n"
            f"Run: poetry run analyze-wallet analyze {wallet_id}"
        )

    plot_all(trades_csv=trades_csv, output_dir=output_dir)


@cli.command("run-all")
@click.argument("wallet_id")
@click.option("--data-dir",   default="data/wallets",          show_default=True)
@click.option("--output-dir", default="output/wallet_analysis", show_default=True)
def run_all(wallet_id: str, data_dir: str, output_dir: str) -> None:
    """Run analyze + plot for a wallet in one step.

    \b
    Example:
      poetry run analyze-wallet run-all 0x476639d9845d7a0261cb005dae6473f089ff5a03
    """
    from pathlib import Path
    from analyze_wallet_positions.analyze_trades import analyze as _analyze
    from analyze_wallet_positions.plot_windows import plot_all

    out_dir = _analyze(wallet_id=wallet_id, data_dir=data_dir, output_dir=output_dir)
    trades_csv = Path(out_dir) / "trades_enriched.csv"
    if trades_csv.exists():
        plot_all(trades_csv=trades_csv, output_dir=output_dir)


if __name__ == "__main__":
    cli()
