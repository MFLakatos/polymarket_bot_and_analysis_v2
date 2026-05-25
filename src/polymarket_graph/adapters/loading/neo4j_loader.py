"""Idempotent Neo4j ingestion of wallets, markets, outcomes, trades.

All writes use `MERGE` so re-running the loader does not produce duplicates.
"""
from __future__ import annotations
from typing import Iterable, Iterator

from polymarket_graph.domain.entities import Market, Trade, Wallet
from polymarket_graph.infrastructure.logging import get_logger
from polymarket_graph.infrastructure.neo4j_client import Neo4jClient

logger = get_logger(__name__)

WALLET_CYPHER = """
UNWIND $rows AS row
MERGE (w:Wallet {id: row.id})
  ON CREATE SET w.first_seen = row.first_seen, w.last_seen = row.last_seen
  ON MATCH  SET
    w.first_seen = CASE WHEN w.first_seen IS NULL OR row.first_seen < w.first_seen
                        THEN row.first_seen ELSE w.first_seen END,
    w.last_seen  = CASE WHEN w.last_seen IS NULL OR row.last_seen > w.last_seen
                        THEN row.last_seen ELSE w.last_seen END
"""

MARKET_CYPHER = """
UNWIND $rows AS row
MERGE (m:Market {id: row.id})
  SET m.question = row.question, m.slug = row.slug, m.category = row.category,
      m.created_at = row.created_at, m.closed_at = row.closed_at, m.resolved = row.resolved
WITH m, row
UNWIND row.outcomes AS o
MERGE (out:Outcome {id: o.id})
  SET out.market_id = m.id, out.name = o.name, out.token_id = o.token_id, out.is_winner = o.is_winner
MERGE (m)-[:HAS_OUTCOME]->(out)
"""

TRADE_CYPHER = """
UNWIND $rows AS row
MERGE (w:Wallet {id: row.wallet_id})
MERGE (m:Market {id: row.market_id})
MERGE (t:Trade {trade_id: row.trade_id, wallet_id: row.wallet_id,
                market_id: row.market_id, timestamp: row.timestamp})
  ON CREATE SET t.side = row.side, t.price = row.price, t.size = row.size,
                t.outcome_id = row.outcome_id, t.tx_hash = row.tx_hash, t.notional = row.notional
MERGE (w)-[:EXECUTED]->(t)
MERGE (t)-[:ON_MARKET]->(m)
MERGE (w)-[r:TRADED {trade_id: row.trade_id}]->(m)
  ON CREATE SET r.timestamp = row.timestamp, r.price = row.price,
                r.size = row.size, r.side = row.side, r.outcome_id = row.outcome_id
FOREACH (_ IN CASE WHEN row.outcome_id IS NULL THEN [] ELSE [1] END |
  MERGE (out:Outcome {id: row.outcome_id})
    ON CREATE SET out.market_id = row.market_id
  MERGE (t)-[:ON_OUTCOME]->(out)
)
"""

CLUSTER_UPDATE_CYPHER = """
UNWIND $rows AS row
MATCH (w:Wallet {id: row.wallet_id})
SET w.cluster = row.cluster,
    w.pca1 = row.pca1,
    w.pca2 = row.pca2,
    w.win_rate = row.win_rate,
    w.pnl = row.pnl,
    w.total_volume = row.total_volume
"""

INFLUENCE_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (leader:Wallet {id: row.leader_id})
MATCH (follower:Wallet {id: row.follower_id})
MERGE (leader)-[r:LEADS]->(follower)
  ON CREATE SET r.weight = row.weight, r.market_count = row.market_count
  ON MATCH  SET r.weight = r.weight + row.weight,
                r.market_count = CASE WHEN row.market_count > r.market_count
                                      THEN row.market_count ELSE r.market_count END
"""

CORRELATION_EDGE_CYPHER = """
UNWIND $rows AS row
MATCH (m1:Market {id: row.market1_id})
MATCH (m2:Market {id: row.market2_id})
MERGE (m1)-[r:CORRELATED_WITH]->(m2)
  ON CREATE SET r.cosine = row.cosine, r.shared_wallets = row.shared_wallets
  ON MATCH  SET r.cosine = row.cosine, r.shared_wallets = row.shared_wallets
"""


class Neo4jLoader:
    """High-level loader that batches writes through Neo4jClient."""

    def __init__(self, client: Neo4jClient) -> None:
        self._client = client

    def load_wallets(self, wallets: Iterable[Wallet]) -> int:
        rows = (
            {"id": w.id,
             "first_seen": w.first_seen.isoformat() if w.first_seen else None,
             "last_seen": w.last_seen.isoformat() if w.last_seen else None}
            for w in wallets
        )
        return self._client.write_batched(WALLET_CYPHER, rows)

    def load_markets(self, markets: Iterable[Market]) -> int:
        rows = (
            {"id": m.id, "question": m.question, "slug": m.slug,
             "category": m.category,
             "created_at": m.created_at.isoformat() if m.created_at else None,
             "closed_at": m.closed_at.isoformat() if m.closed_at else None,
             "resolved": m.resolved,
             "outcomes": [{"id": o.id, "name": o.name, "token_id": o.token_id, "is_winner": o.is_winner}
                          for o in m.outcomes]}
            for m in markets
        )
        return self._client.write_batched(MARKET_CYPHER, rows)

    def load_trades(self, trades: Iterable[Trade]) -> int:
        return self._client.write_batched(TRADE_CYPHER, _trade_rows(trades))

    def update_wallet_clusters(self, df) -> int:
        import pandas as pd
        rows = [
            {"wallet_id": row["wallet_id"], "cluster": int(row["cluster"]),
             "pca1": float(row.get("pca1", 0.0)), "pca2": float(row.get("pca2", 0.0)),
             "win_rate": float(row.get("win_rate", 0.0)),
             "pnl": float(row.get("pnl", 0.0)),
             "total_volume": float(row.get("total_volume", 0.0))}
            for _, row in df.iterrows()
        ]
        return self._client.write_batched(CLUSTER_UPDATE_CYPHER, iter(rows))


def _trade_rows(trades: Iterable[Trade]) -> Iterator[dict]:
    for t in trades:
        yield {
            "trade_id": t.trade_id, "wallet_id": t.wallet_id,
            "market_id": t.market_id, "outcome_id": t.outcome_id,
            "side": t.side.value, "price": t.price, "size": t.size,
            "notional": t.notional, "timestamp": t.timestamp.isoformat(),
            "tx_hash": t.tx_hash,
        }
