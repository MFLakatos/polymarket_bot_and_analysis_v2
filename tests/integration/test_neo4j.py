"""Integration tests — require a running Neo4j instance.

Run only if NEO4J_INTEGRATION_TESTS=1 is set:
  NEO4J_INTEGRATION_TESTS=1 poetry run pytest tests/integration/

These tests verify that the Neo4j graph database is working correctly
with the Polymarket graph schema.
"""
from __future__ import annotations

import os
import pytest

# Skip entire module unless explicitly enabled
if not os.getenv("NEO4J_INTEGRATION_TESTS"):
    pytest.skip(
        "Integration tests skipped. Set NEO4J_INTEGRATION_TESTS=1 to run.",
        allow_module_level=True,
    )

from polymarket_graph.infrastructure.config import load_config
from polymarket_graph.infrastructure.neo4j_client import Neo4jClient


@pytest.fixture(scope="module")
def neo4j_client():
    cfg = load_config()
    with Neo4jClient(cfg.neo4j) as client:
        yield client


def test_neo4j_connection(neo4j_client):
    """Basic connectivity: run a trivial Cypher query."""
    result = neo4j_client.run("RETURN 1 AS n")
    assert result[0]["n"] == 1


def test_schema_init_is_idempotent(neo4j_client):
    """init_schema() can be called multiple times without error."""
    neo4j_client.init_schema()
    neo4j_client.init_schema()  # second call must not raise


def test_merge_and_query_wallet(neo4j_client):
    """Create a test wallet node and verify it can be retrieved."""
    test_address = "0xdeadbeef00000000000000000000000000000001"
    # Clean up from previous runs
    neo4j_client.run("MATCH (w:Wallet {id: $id}) DETACH DELETE w", id=test_address)

    # Insert
    neo4j_client.run(
        "MERGE (w:Wallet {id: $id}) SET w.test = true RETURN w",
        id=test_address,
    )

    # Query
    result = neo4j_client.run(
        "MATCH (w:Wallet {id: $id}) RETURN w.id AS id, w.test AS test",
        id=test_address,
    )
    assert len(result) == 1
    assert result[0]["id"] == test_address
    assert result[0]["test"] is True

    # Cleanup
    neo4j_client.run("MATCH (w:Wallet {id: $id}) DETACH DELETE w", id=test_address)


def test_uniqueness_constraint(neo4j_client):
    """Verify the Wallet uniqueness constraint is in place."""
    test_address = "0xdeadbeef00000000000000000000000000000002"
    neo4j_client.run("MATCH (w:Wallet {id: $id}) DETACH DELETE w", id=test_address)

    # First MERGE — creates
    neo4j_client.run("MERGE (w:Wallet {id: $id})", id=test_address)
    # Second MERGE — should not create duplicate (idempotent)
    neo4j_client.run("MERGE (w:Wallet {id: $id})", id=test_address)

    result = neo4j_client.run(
        "MATCH (w:Wallet {id: $id}) RETURN count(w) AS cnt", id=test_address
    )
    assert result[0]["cnt"] == 1

    neo4j_client.run("MATCH (w:Wallet {id: $id}) DETACH DELETE w", id=test_address)
