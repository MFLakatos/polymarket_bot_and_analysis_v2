"""Temporal influence graph: lead/lag detection via PageRank."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import timedelta
from typing import Iterable

import networkx as nx
import pandas as pd

from polymarket_graph.domain.entities import Trade, TradeSide
from polymarket_graph.infrastructure.config import InfluenceConfig
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


@dataclass
class InfluenceResult:
    graph: nx.DiGraph
    pagerank: dict[str, float]
    betweenness: dict[str, float]
    early_traders: pd.DataFrame   # wallet_id, markets_led, avg_lead_time_minutes


def build_influence_graph(trades: Iterable[Trade], config: InfluenceConfig) -> InfluenceResult:
    """Build a directed influence graph from trade sequences.

    For each market the first wallet to trade within a lag window "leads"
    any subsequent wallets that trade in the same direction at a confirming price.
    """
    trade_list = sorted(trades, key=lambda t: t.timestamp)
    by_market: dict[str, list[Trade]] = defaultdict(list)
    for t in trade_list:
        by_market[t.market_id].append(t)

    g = nx.DiGraph()
    lead_counts: dict[str, int] = defaultdict(int)
    lead_times: dict[str, list[float]] = defaultdict(list)
    window = timedelta(minutes=config.lag_window_minutes)

    for market_id, market_trades in by_market.items():
        if len(market_trades) < 2:
            continue
        leader = market_trades[0]
        first_ts = leader.timestamp
        lead_counts[leader.wallet_id] += 1
        followers_seen: set[str] = set()

        for follower in market_trades[1:]:
            if follower.timestamp - first_ts > window:
                break
            if follower.wallet_id == leader.wallet_id:
                continue
            same_direction = follower.side == leader.side
            price_confirms = (
                (leader.side == TradeSide.BUY and follower.price >= leader.price)
                or (leader.side == TradeSide.SELL and follower.price <= leader.price)
            )
            if not (same_direction and price_confirms):
                continue

            lead_minutes = (follower.timestamp - first_ts).total_seconds() / 60.0
            lead_times[leader.wallet_id].append(lead_minutes)

            if g.has_edge(leader.wallet_id, follower.wallet_id):
                g[leader.wallet_id][follower.wallet_id]["weight"] += 1
                g[leader.wallet_id][follower.wallet_id]["markets"].add(market_id)
            else:
                g.add_edge(leader.wallet_id, follower.wallet_id, weight=1, markets={market_id})
            followers_seen.add(follower.wallet_id)

        # Prune leaders with too few followers
        if len(followers_seen) < config.min_followers:
            for f in followers_seen:
                if g.has_edge(leader.wallet_id, f):
                    g.remove_edge(leader.wallet_id, f)

    # Serialize market sets as counts
    for u, v, data in g.edges(data=True):
        data["market_count"] = len(data.pop("markets", set()))

    pagerank: dict[str, float] = {}
    betweenness: dict[str, float] = {}
    if g.number_of_nodes() > 0:
        try:
            pagerank = nx.pagerank(g, alpha=config.pagerank_alpha, weight="weight")
        except nx.PowerIterationFailedConvergence:
            pagerank = {n: 0.0 for n in g.nodes}
        k = min(500, g.number_of_nodes())
        betweenness = nx.betweenness_centrality(g, weight="weight", k=k if g.number_of_nodes() > 2000 else None)

    early_rows = [
        {
            "wallet_id": wallet_id,
            "markets_led": n_markets,
            "followers": int(g.out_degree(wallet_id, weight="weight")) if wallet_id in g else 0,
            "avg_lead_time_minutes": float(sum(lead_times.get(wallet_id, [])) / len(lead_times[wallet_id]))
                                     if lead_times.get(wallet_id) else 0.0,
            "pagerank": pagerank.get(wallet_id, 0.0),
            "betweenness": betweenness.get(wallet_id, 0.0),
        }
        for wallet_id, n_markets in lead_counts.items()
    ]
    early_df = (pd.DataFrame(early_rows)
                .sort_values(["pagerank", "markets_led"], ascending=False)
                .reset_index(drop=True))

    logger.info("influence.built", nodes=g.number_of_nodes(), edges=g.number_of_edges())
    return InfluenceResult(graph=g, pagerank=pagerank, betweenness=betweenness, early_traders=early_df)
