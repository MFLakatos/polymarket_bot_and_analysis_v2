"""Market correlation graph from wallet co-trading."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Iterable

import networkx as nx
import numpy as np
import pandas as pd

from polymarket_graph.domain.entities import Trade
from polymarket_graph.infrastructure.config import MarketCorrelationConfig
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


@dataclass
class MarketCorrelationResult:
    graph: nx.Graph
    similarity_matrix: pd.DataFrame
    co_occurrence: pd.DataFrame


def build_market_correlation_graph(trades: Iterable[Trade], config: MarketCorrelationConfig) -> MarketCorrelationResult:
    trade_list = list(trades)
    if not trade_list:
        empty = pd.DataFrame()
        return MarketCorrelationResult(graph=nx.Graph(), similarity_matrix=empty, co_occurrence=empty)

    df = pd.DataFrame([{"wallet_id": t.wallet_id, "market_id": t.market_id, "notional": t.notional} for t in trade_list])
    pivot = (df.groupby(["wallet_id", "market_id"])["notional"].sum()
               .reset_index()
               .pivot(index="wallet_id", columns="market_id", values="notional")
               .fillna(0.0))

    try:
        from scipy.sparse import csr_matrix
        from sklearn.metrics.pairwise import cosine_similarity
        sparse = csr_matrix(pivot.values)
        binary = (sparse > 0).astype(int)
        co_occ = (binary.T @ binary).toarray()
        np.fill_diagonal(co_occ, 0)
        sim = cosine_similarity(sparse.T)
        np.fill_diagonal(sim, 0.0)
    except ImportError:
        arr = pivot.values
        binary = (arr > 0).astype(float)
        co_occ = binary.T @ binary
        np.fill_diagonal(co_occ, 0)
        norms = np.linalg.norm(arr, axis=0, keepdims=True)
        norms[norms == 0] = 1.0
        normalized = arr / norms
        sim = normalized.T @ normalized
        np.fill_diagonal(sim, 0.0)

    market_index = pivot.columns
    co_df = pd.DataFrame(co_occ, index=market_index, columns=market_index)
    sim_df = pd.DataFrame(sim, index=market_index, columns=market_index)

    g: nx.Graph = nx.Graph()
    for m in market_index:
        g.add_node(m)

    n = len(market_index)
    for i in range(n):
        for j in range(i + 1, n):
            shared = int(co_occ[i, j])
            cos = float(sim[i, j])
            if shared >= config.min_shared_wallets and cos >= config.cosine_threshold:
                g.add_edge(market_index[i], market_index[j], shared_wallets=shared, cosine=cos)

    logger.info("market_correlation.built", markets=n, edges=g.number_of_edges())
    return MarketCorrelationResult(graph=g, similarity_matrix=sim_df, co_occurrence=co_df)
