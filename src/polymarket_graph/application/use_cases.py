"""Use cases — one class per CLI verb. Pure orchestration, no I/O details."""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Optional

import pandas as pd

from polymarket_graph.adapters.extraction import (
    ClobApiClient, GammaApiClient, HybridTradeSource, TradeSource,
)
from polymarket_graph.adapters.loading import Neo4jLoader
from polymarket_graph.adapters.transform import (
    build_wallet_feature_frame, deduplicate_trades, normalize_trades,
)
from polymarket_graph.analytics.clustering import cluster_wallets
from polymarket_graph.analytics.influence_graph import build_influence_graph
from polymarket_graph.analytics.market_correlation import build_market_correlation_graph
from polymarket_graph.analytics.visualization import (
    plot_influence_graph, plot_market_correlation_graph,
    plot_wallet_clusters, plot_wallet_clusters_interactive,
)
from polymarket_graph.application.checkpoint import CheckpointState, CheckpointStore
from polymarket_graph.domain.entities import Market, Trade, Wallet
from polymarket_graph.infrastructure.config import AppConfig
from polymarket_graph.infrastructure.logging import get_logger
from polymarket_graph.infrastructure.neo4j_client import Neo4jClient

logger = get_logger(__name__)


# ── factory ──────────────────────────────────────────────────────────────────

def make_trade_source(config: AppConfig) -> TradeSource:
    gamma_client = GammaApiClient(
        base_url=config.ingestion.gamma_api.base_url,
        rate_limit_per_sec=config.ingestion.gamma_api.rate_limit_per_sec,
        max_retries=config.ingestion.gamma_api.max_retries,
        timeout_seconds=config.ingestion.gamma_api.timeout_seconds,
        batch_size=config.ingestion.gamma_api.batch_size,
        trades_base_url=config.ingestion.gamma_api.trades_base_url,
    )
    clob_client = ClobApiClient(
        base_url=config.ingestion.clob_api.base_url,
        rate_limit_per_sec=config.ingestion.clob_api.rate_limit_per_sec,
        max_retries=config.ingestion.clob_api.max_retries,
        timeout_seconds=config.ingestion.clob_api.timeout_seconds,
        batch_size=config.ingestion.clob_api.batch_size,
    )
    if config.api_mode == "gamma_api":
        return gamma_client
    if config.api_mode == "clob_api":
        return clob_client
    return HybridTradeSource(historical=gamma_client, live=clob_client)


# ── I/O helpers ───────────────────────────────────────────────────────────────

def _trades_to_jsonl(trades: Iterable[Trade], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for t in trades:
            fh.write(t.model_dump_json() + "\n")
            count += 1
    return count


def _trades_from_jsonl(path: Path) -> list[Trade]:
    if not path.exists():
        return []
    results = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            results.append(Trade.model_validate_json(line))
        except Exception:
            skipped += 1
    if skipped:
        logger.warning("jsonl.trades.skipped_invalid_lines", path=str(path), skipped=skipped)
    return results


def _markets_to_jsonl(markets: Iterable[Market], path: Path) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for m in markets:
            try:
                fh.write(m.model_dump_json() + "\n")
                count += 1
            except Exception:
                pass  # skip markets that can't be serialised
    return count


def _markets_from_jsonl(path: Path) -> list[Market]:
    if not path.exists():
        return []
    results = []
    skipped = 0
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            results.append(Market.model_validate_json(line))
        except Exception:
            skipped += 1
    if skipped:
        logger.warning("jsonl.markets.skipped_invalid_lines", path=str(path), skipped=skipped)
    return results


# ── use cases ────────────────────────────────────────────────────────────────

@dataclass
class IngestUseCase:
    config: AppConfig

    def execute(self, since: Optional[datetime] = None, until: Optional[datetime] = None) -> dict:
        store = CheckpointStore(self.config.ingestion.checkpoint_path)
        state = store.load()
        effective_since = since or CheckpointStore.from_iso(state.last_gamma_api_ts)

        source = make_trade_source(self.config)
        raw_dir = Path(self.config.ingestion.raw_path)
        trades_path = raw_dir / "trades.jsonl"
        markets_path = raw_dir / "markets.jsonl"

        markets_count = _markets_to_jsonl(source.iter_markets(), markets_path)
        trades_count = _trades_to_jsonl(
            source.iter_trades(since=effective_since, until=until), trades_path
        )

        new_state = CheckpointState(
            last_gamma_api_ts=CheckpointStore.to_iso(until or datetime.now(timezone.utc)),
            last_clob_api_ts=state.last_clob_api_ts,
            total_trades_ingested=state.total_trades_ingested + trades_count,
        )
        store.save(new_state)
        return {"trades_fetched": trades_count, "markets_fetched": markets_count,
                "since": str(effective_since), "until": str(until)}


@dataclass
class TransformUseCase:
    config: AppConfig

    def execute(self) -> dict:
        raw_dir = Path(self.config.ingestion.raw_path)
        inter_dir = Path(self.config.ingestion.intermediate_path)

        trades = _trades_from_jsonl(raw_dir / "trades.jsonl")
        markets = _markets_from_jsonl(raw_dir / "markets.jsonl")

        trades_normalized = list(deduplicate_trades(normalize_trades(trades)))
        _trades_to_jsonl(trades_normalized, inter_dir / "trades.jsonl")
        _markets_to_jsonl(markets, inter_dir / "markets.jsonl")

        features_df = build_wallet_feature_frame(
            trades_normalized, markets,
            min_trades_per_wallet=self.config.features.min_trades_per_wallet,
            early_window_minutes=self.config.features.early_window_minutes,
        )
        feats_path = inter_dir / "wallet_features.csv"
        inter_dir.mkdir(parents=True, exist_ok=True)
        features_df.to_csv(feats_path, index=False)

        return {"trades_normalized": len(trades_normalized),
                "wallets_with_features": len(features_df),
                "markets": len(markets)}


@dataclass
class LoadGraphUseCase:
    config: AppConfig

    def execute(self) -> dict:
        inter_dir = Path(self.config.ingestion.intermediate_path)
        trades = _trades_from_jsonl(inter_dir / "trades.jsonl")
        markets = _markets_from_jsonl(inter_dir / "markets.jsonl")

        wallets = {
            t.wallet_id: Wallet(id=t.wallet_id, first_seen=t.timestamp, last_seen=t.timestamp)
            for t in trades
        }

        with Neo4jClient(self.config.neo4j) as client:
            client.init_schema()
            loader = Neo4jLoader(client)
            w_count = loader.load_wallets(wallets.values())
            m_count = loader.load_markets(markets)
            t_count = loader.load_trades(trades)

        return {"wallets_loaded": w_count, "markets_loaded": m_count, "trades_loaded": t_count}


@dataclass
class ClusterWalletsUseCase:
    config: AppConfig

    def execute(self) -> dict:
        inter_dir = Path(self.config.ingestion.intermediate_path)
        feats_path = inter_dir / "wallet_features.csv"
        if not feats_path.exists():
            return {"error": "wallet_features.csv not found — run transform first"}

        features_df = pd.read_csv(feats_path)
        if features_df.empty:
            return {"error": "No wallets to cluster"}

        annotated, result = cluster_wallets(features_df, self.config.clustering)

        out_dir = Path(self.config.output.base_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / "wallet_clusters.csv"
        annotated.to_csv(out_path, index=False)

        plot_path = plot_wallet_clusters(annotated, out_dir / "wallet_clusters.png")
        interactive_path: Optional[Path] = None
        try:
            interactive_path = plot_wallet_clusters_interactive(annotated, out_dir / "wallet_clusters.html")
        except Exception as exc:
            logger.warning("clustering.interactive_plot.failed", error=str(exc))

        # Push cluster labels to Neo4j
        try:
            with Neo4jClient(self.config.neo4j) as client:
                client.write_batched(
                    """
                    UNWIND $rows AS row
                    MERGE (w:Wallet {id: row.wallet_id})
                      SET w.cluster = row.cluster, w.win_rate = row.win_rate,
                          w.pnl = row.pnl, w.total_volume = row.total_volume,
                          w.pca1 = row.pca1, w.pca2 = row.pca2
                    """,
                    annotated[["wallet_id", "cluster", "win_rate", "pnl", "total_volume", "pca1", "pca2"]]
                    .to_dict(orient="records"),
                )
        except Exception as exc:
            logger.warning("clustering.neo4j_write.failed", error=str(exc))

        return {
            "wallets_clustered": len(annotated),
            "n_clusters": len(set(result.labels) - {-1}),
            "silhouette": result.silhouette,
            "csv_path": str(out_path),
            "plot_path": str(plot_path),
            "interactive_path": str(interactive_path) if interactive_path else None,
        }


@dataclass
class BuildInfluenceGraphUseCase:
    config: AppConfig

    def execute(self) -> dict:
        inter_dir = Path(self.config.ingestion.intermediate_path)
        trades = _trades_from_jsonl(inter_dir / "trades.jsonl")
        if not trades:
            return {"nodes": 0, "edges": 0, "early_traders_csv": None}

        result = build_influence_graph(trades, self.config.influence)
        out_dir = Path(self.config.output.base_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        early_path = out_dir / "influence_scores.csv"
        result.early_traders.to_csv(early_path, index=False)
        edges_path = out_dir / "influence_edges.csv"
        pd.DataFrame(
            [{"source": u, "target": v, **{k: d.get(k) for k in ("weight", "market_count")}}
             for u, v, d in result.graph.edges(data=True)]
        ).to_csv(edges_path, index=False)

        plot_path: Optional[Path] = None
        if result.graph.number_of_nodes() > 0:
            plot_path = plot_influence_graph(result.graph, out_dir / "influence_graph.png", pagerank=result.pagerank)

        # Push LEADS edges to Neo4j
        try:
            with Neo4jClient(self.config.neo4j) as client:
                client.write_batched(
                    """
                    UNWIND $rows AS row
                    MERGE (l:Wallet {id: row.leader_id})
                    MERGE (f:Wallet {id: row.follower_id})
                    MERGE (l)-[r:LEADS]->(f)
                      ON CREATE SET r.weight = row.weight, r.market_count = row.market_count
                      ON MATCH  SET r.weight = r.weight + row.weight
                    """,
                    [{"leader_id": u, "follower_id": v, "weight": d.get("weight", 1),
                      "market_count": d.get("market_count", 0)}
                     for u, v, d in result.graph.edges(data=True)],
                )
        except Exception as exc:
            logger.warning("influence.neo4j_write.failed", error=str(exc))

        return {"nodes": result.graph.number_of_nodes(), "edges": result.graph.number_of_edges(),
                "early_traders_csv": str(early_path), "edges_csv": str(edges_path),
                "plot_path": str(plot_path) if plot_path else None}


@dataclass
class BuildMarketCorrelationUseCase:
    config: AppConfig

    def execute(self) -> dict:
        inter_dir = Path(self.config.ingestion.intermediate_path)
        trades = _trades_from_jsonl(inter_dir / "trades.jsonl")
        if not trades:
            return {"markets": 0, "edges": 0}

        result = build_market_correlation_graph(trades, self.config.market_correlation)
        out_dir = Path(self.config.output.base_path)
        out_dir.mkdir(parents=True, exist_ok=True)
        result.similarity_matrix.to_csv(out_dir / "market_similarity.csv")
        result.co_occurrence.to_csv(out_dir / "market_co_occurrence.csv")

        plot_path: Optional[Path] = None
        if result.graph.number_of_nodes() > 0:
            plot_path = plot_market_correlation_graph(result.graph, out_dir / "market_correlation.png")

        try:
            with Neo4jClient(self.config.neo4j) as client:
                client.write_batched(
                    """
                    UNWIND $rows AS row
                    MERGE (a:Market {id: row.source})
                    MERGE (b:Market {id: row.target})
                    MERGE (a)-[r:CORRELATED_WITH]->(b)
                      SET r.cosine = row.cosine, r.shared_wallets = row.shared_wallets
                    """,
                    [{"source": u, "target": v, "cosine": d.get("cosine"), "shared_wallets": d.get("shared_wallets")}
                     for u, v, d in result.graph.edges(data=True)],
                )
        except Exception as exc:
            logger.warning("market_correlation.neo4j_write.failed", error=str(exc))

        return {"markets": result.graph.number_of_nodes(), "edges": result.graph.number_of_edges(),
                "plot_path": str(plot_path) if plot_path else None}


@dataclass
class ExportResultsUseCase:
    config: AppConfig

    def execute(self) -> dict:
        out_dir = Path(self.config.output.base_path)
        manifest = {}
        for f in sorted(out_dir.glob("*")):
            if f.is_file():
                manifest[f.name] = {"size_bytes": f.stat().st_size, "path": str(f)}
        return {"output_dir": str(out_dir), "files": manifest}


@dataclass
class RunAllUseCase:
    config: AppConfig

    def execute(self) -> dict:
        results = {}
        logger.info("run_all.ingest.start")
        results["ingest"] = IngestUseCase(self.config).execute()
        logger.info("run_all.transform.start")
        results["transform"] = TransformUseCase(self.config).execute()
        logger.info("run_all.load.start")
        results["load"] = LoadGraphUseCase(self.config).execute()
        logger.info("run_all.cluster.start")
        results["cluster_wallets"] = ClusterWalletsUseCase(self.config).execute()
        logger.info("run_all.influence.start")
        results["influence_graph"] = BuildInfluenceGraphUseCase(self.config).execute()
        logger.info("run_all.correlation.start")
        results["market_correlation"] = BuildMarketCorrelationUseCase(self.config).execute()
        logger.info("run_all.export.start")
        results["export"] = ExportResultsUseCase(self.config).execute()
        return results