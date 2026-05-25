"""CLI for downloading Polymarket wallet data."""
from __future__ import annotations

import click


@click.command()
@click.option("--address", "-a", required=True, help="Wallet address (0x...).")
@click.option("--output", "-o", default=None, help="Output directory (default: data/wallets/{address}).")
def cli(address: str, output: str | None) -> None:
    """Download all trades, positions, activity, and P&L for a Polymarket wallet.

    Example:
      poetry run wallet-download --address 0x476639d9845d7a0261cb005dae6473f089ff5a03
    """
    from download_wallet_positions.main import download
    download(address=address, output_dir=output)


if __name__ == "__main__":
    cli()
