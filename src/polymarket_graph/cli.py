"""Click-based CLI surface — one command per use case."""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import click

try:
    from dotenv import load_dotenv
    for root in [Path.cwd(), Path(__file__).parent.parent.parent.parent]:
        env_file = root / ".env"
        if env_file.exists():
            load_dotenv(env_file)
            break
except Exception:
    pass

from polymarket_graph.application.use_cases import (
    BuildInfluenceGraphUseCase, BuildMarketCorrelationUseCase,
    ClusterWalletsUseCase, ExportResultsUseCase, IngestUseCase,
    LoadGraphUseCase, RunAllUseCase, TransformUseCase,
)
from polymarket_graph.infrastructure import AppConfig, Neo4jClient, configure_logging, load_config


def _bootstrap(config_path: Optional[str]) -> AppConfig:
    cfg = load_config(config_path)
    configure_logging(level=cfg.logging.level, use_json=cfg.logging.use_json)
    return cfg


def _echo(payload: dict) -> None:
    click.echo(json.dumps(payload, indent=2, default=str))


@click.group(context_settings={"help_option_names": ["-h", "--help"]})
@click.option("--config", "config_path", type=click.Path(dir_okay=False), default=None,
              help="Path to polymarket.yaml (defaults to config/polymarket.yaml).")
@click.pass_context
def cli(ctx: click.Context, config_path: Optional[str]) -> None:
    """Polymarket Graph — analytics over Polymarket trading data using Neo4j + ML."""
    ctx.ensure_object(dict)
    ctx.obj["config"] = _bootstrap(config_path)


@cli.command("init-schema")
@click.pass_context
def init_schema(ctx: click.Context) -> None:
    """Create Neo4j constraints/indexes (idempotent)."""
    cfg: AppConfig = ctx.obj["config"]
    with Neo4jClient(cfg.neo4j) as client:
        client.init_schema()
    click.echo("✓ Neo4j schema initialized.")


@cli.command("ingest")
@click.option("--since", type=click.DateTime(), default=None, help="UTC start timestamp.")
@click.option("--until", type=click.DateTime(), default=None, help="UTC end timestamp.")
@click.pass_context
def ingest(ctx: click.Context, since: Optional[datetime], until: Optional[datetime]) -> None:
    """Pull data from Polymarket APIs and persist raw JSONL."""
    if since and since.tzinfo is None:
        since = since.replace(tzinfo=timezone.utc)
    if until and until.tzinfo is None:
        until = until.replace(tzinfo=timezone.utc)
    cfg: AppConfig = ctx.obj["config"]
    _echo(IngestUseCase(cfg).execute(since=since, until=until))


@cli.command("transform")
@click.pass_context
def transform(ctx: click.Context) -> None:
    """Normalize, deduplicate, and compute wallet features."""
    _echo(TransformUseCase(ctx.obj["config"]).execute())


@cli.command("load")
@click.pass_context
def load(ctx: click.Context) -> None:
    """Push cleaned data into Neo4j."""
    _echo(LoadGraphUseCase(ctx.obj["config"]).execute())


@cli.command("cluster-wallets")
@click.pass_context
def cluster_wallets_cmd(ctx: click.Context) -> None:
    """Run smart-money clustering on the wallet feature frame."""
    _echo(ClusterWalletsUseCase(ctx.obj["config"]).execute())


@cli.command("build-influence-graph")
@click.pass_context
def build_influence_graph_cmd(ctx: click.Context) -> None:
    """Construct the temporal influence (lead/lag) graph."""
    _echo(BuildInfluenceGraphUseCase(ctx.obj["config"]).execute())


@cli.command("build-market-correlation")
@click.pass_context
def build_market_correlation_cmd(ctx: click.Context) -> None:
    """Construct the market correlation (co-trade similarity) graph."""
    _echo(BuildMarketCorrelationUseCase(ctx.obj["config"]).execute())


@cli.command("export-results")
@click.pass_context
def export_results(ctx: click.Context) -> None:
    """Write a manifest of all generated output artifacts."""
    _echo(ExportResultsUseCase(ctx.obj["config"]).execute())


@cli.command("run-all")
@click.pass_context
def run_all(ctx: click.Context) -> None:
    """Run the full pipeline end-to-end (ingest → transform → load → analyze → export)."""
    _echo(RunAllUseCase(ctx.obj["config"]).execute())


@cli.command("visualize")
@click.pass_context
def visualize(ctx: click.Context) -> None:
    """Re-generate all visualizations from existing intermediate data."""
    cfg: AppConfig = ctx.obj["config"]
    inter_dir = Path(cfg.ingestion.intermediate_path)
    out_dir = Path(cfg.output.base_path)
    out_dir.mkdir(parents=True, exist_ok=True)
    import pandas as pd
    from polymarket_graph.analytics.visualization import (
        plot_wallet_clusters, plot_wallet_clusters_interactive,
        plot_influence_graph, plot_market_correlation_graph,
    )
    feats_path = inter_dir / "wallet_features.csv"
    if feats_path.exists():
        df = pd.read_csv(feats_path)
        if "cluster" in df.columns:
            plot_wallet_clusters(df, out_dir / "wallet_clusters.png")
            try:
                plot_wallet_clusters_interactive(df, out_dir / "wallet_clusters.html")
            except Exception:
                pass
    click.echo(f"✓ Visualizations saved to {out_dir}")


if __name__ == "__main__":
    cli()
