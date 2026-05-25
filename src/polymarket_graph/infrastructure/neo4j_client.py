"""Thin wrapper around the official neo4j Python driver.

Centralises connection lifecycle, batched UNWIND writes, and schema setup.
"""
from __future__ import annotations

from contextlib import contextmanager
from typing import Any, Iterable, Iterator, Sequence

from polymarket_graph.infrastructure.config import Neo4jConfig
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)

SCHEMA_STATEMENTS: tuple[str, ...] = (
    # Uniqueness constraints (idempotent via IF NOT EXISTS)
    "CREATE CONSTRAINT wallet_id_unique IF NOT EXISTS "
    "FOR (w:Wallet) REQUIRE w.id IS UNIQUE",
    "CREATE CONSTRAINT market_id_unique IF NOT EXISTS "
    "FOR (m:Market) REQUIRE m.id IS UNIQUE",
    "CREATE CONSTRAINT outcome_id_unique IF NOT EXISTS "
    "FOR (o:Outcome) REQUIRE o.id IS UNIQUE",
    "CREATE CONSTRAINT trade_dedup_key IF NOT EXISTS "
    "FOR (t:Trade) REQUIRE (t.trade_id, t.wallet_id, t.market_id, t.timestamp) IS UNIQUE",
    # Range indexes for fast lookups
    "CREATE INDEX trade_ts IF NOT EXISTS FOR (t:Trade) ON (t.timestamp)",
    "CREATE INDEX market_category IF NOT EXISTS FOR (m:Market) ON (m.category)",
)


class Neo4jClient:
    """Lifecycle manager + write helper for Neo4j."""

    def __init__(self, config: Neo4jConfig) -> None:
        self._config = config
        self._driver = None

    # ── lifecycle ────────────────────────────────────────────────────────────

    def connect(self):
        if self._driver is None:
            from neo4j import GraphDatabase
            logger.info("neo4j.connect", uri=self._config.uri, user=self._config.user)
            try:
                self._driver = GraphDatabase.driver(
                    self._config.uri,
                    auth=(self._config.user, self._config.password.get_secret_value()),
                    encrypted=False,
                )
            except Exception as exc:
                logger.error("neo4j.connect.failed", error=str(exc))
                raise
        return self._driver

    def close(self) -> None:
        if self._driver is not None:
            self._driver.close()
            self._driver = None

    def __enter__(self) -> "Neo4jClient":
        self.connect()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()

    @contextmanager
    def session(self) -> Iterator[Any]:
        driver = self.connect()
        with driver.session(database=self._config.database) as s:
            yield s

    # ── schema ───────────────────────────────────────────────────────────────

    def init_schema(self) -> None:
        """Create constraints + indexes. Idempotent — safe to call multiple times."""
        from neo4j.exceptions import CypherSyntaxError
        with self.session() as s:
            for stmt in SCHEMA_STATEMENTS:
                logger.info("neo4j.schema.execute", statement=stmt[:80])
                try:
                    s.run(stmt)
                except CypherSyntaxError as exc:
                    logger.warning("neo4j.schema.syntax_error", error=str(exc))
                except Exception as exc:
                    logger.error("neo4j.schema.failed", error=str(exc))
                    raise

    # ── batched writes ───────────────────────────────────────────────────────

    def write_batched(
        self,
        cypher: str,
        rows: Iterable[dict],
        batch_size: int | None = None,
        param_key: str = "rows",
    ) -> int:
        """Execute a parameterised Cypher UNWIND in batches. Returns total row count."""
        bsz = batch_size or self._config.batch_size
        total = 0
        buffer: list[dict] = []
        with self.session() as s:
            for row in rows:
                buffer.append(row)
                if len(buffer) >= bsz:
                    s.execute_write(_run_write, cypher, {param_key: buffer})
                    total += len(buffer)
                    buffer = []
            if buffer:
                s.execute_write(_run_write, cypher, {param_key: buffer})
                total += len(buffer)
        logger.info("neo4j.write.complete", cypher=cypher.splitlines()[0][:80], rows=total)
        return total

    def run(self, cypher: str, **params: Any) -> list[dict]:
        """Run an arbitrary Cypher query and return results as list of dicts."""
        with self.session() as s:
            result = s.run(cypher, **params)
            return [r.data() for r in result]


def _run_write(tx, cypher: str, params: dict) -> None:
    tx.run(cypher, **params)


def chunked(seq: Sequence, size: int) -> Iterator[Sequence]:
    """Yield successive `size`-sized chunks from `seq`."""
    for i in range(0, len(seq), size):
        yield seq[i: i + size]
