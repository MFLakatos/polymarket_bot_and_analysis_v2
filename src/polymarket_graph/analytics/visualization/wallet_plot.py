"""Visualization helpers — wallet clusters, influence graph, market correlation."""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import networkx as nx
import numpy as np
import pandas as pd

from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


def _scale_sizes(values: pd.Series, min_size: float = 20.0, max_size: float = 400.0) -> np.ndarray:
    v = values.to_numpy(dtype=float, copy=True)
    v = np.nan_to_num(v, nan=0.0)
    lo, hi = float(v.min()), float(v.max())
    if hi <= lo:
        return np.full_like(v, (min_size + max_size) / 2.0)
    return min_size + (v - lo) / (hi - lo) * (max_size - min_size)


def plot_wallet_clusters(
    df: pd.DataFrame,
    output_path: str | Path,
    *,
    min_volume: float = 0.0,
    title: str = "Wallet clusters (PCA projection, size = win rate)",
) -> Path:
    """Static matplotlib scatter: x=pca1, y=pca2, color=cluster, size=win_rate."""
    required = {"pca1", "pca2", "cluster", "win_rate", "total_volume", "wallet_id"}
    missing = required - set(df.columns)
    if missing:
        raise ValueError(f"plot_wallet_clusters: missing columns {missing}")

    plot_df = df[df["total_volume"] >= min_volume].copy()
    if plot_df.empty:
        raise ValueError("No wallets remain after volume filter")

    sizes = _scale_sizes(plot_df["win_rate"])
    fig, ax = plt.subplots(figsize=(11, 8))
    clusters = sorted(plot_df["cluster"].unique())
    cmap = plt.get_cmap("tab10" if len(clusters) <= 10 else "tab20")

    for i, cluster in enumerate(clusters):
        sub = plot_df["cluster"] == cluster
        ax.scatter(
            plot_df.loc[sub, "pca1"], plot_df.loc[sub, "pca2"],
            s=sizes[sub.values], c=[cmap(i % cmap.N)],
            alpha=0.75, edgecolors="white", linewidths=0.4,
            label=f"cluster {cluster}" if cluster != -1 else "noise",
        )

    ax.set_xlabel("PCA 1")
    ax.set_ylabel("PCA 2")
    ax.set_title(title)
    ax.legend(loc="best", fontsize=8, frameon=True)
    ax.grid(True, alpha=0.2)
    fig.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    logger.info("viz.wallet_clusters.saved", path=str(out), wallets=len(plot_df))
    return out


def plot_wallet_clusters_interactive(
    df: pd.DataFrame,
    output_path: str | Path,
    *,
    min_volume: float = 0.0,
) -> Path:
    """Interactive Plotly scatter with hover metadata."""
    try:
        import plotly.express as px
    except ImportError:
        raise ImportError("plotly is required for interactive charts. poetry install")

    plot_df = df[df["total_volume"] >= min_volume].copy()
    plot_df["cluster_label"] = plot_df["cluster"].astype(str)
    plot_df["win_rate_pct"] = plot_df["win_rate"] * 100.0
    plot_df["size_metric"] = plot_df["win_rate"].clip(lower=1e-3)

    fig = px.scatter(
        plot_df, x="pca1", y="pca2",
        color="cluster_label", size="size_metric", size_max=40,
        hover_data={"wallet_id": True, "pnl": ":.2f", "total_volume": ":.2f",
                    "total_trades": True, "win_rate_pct": ":.2f",
                    "size_metric": False, "pca1": False, "pca2": False},
        title="Polymarket wallet clusters — size = win rate, color = cluster",
    )
    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.write_html(str(out), include_plotlyjs="cdn")
    logger.info("viz.wallet_clusters_interactive.saved", path=str(out))
    return out


def plot_influence_graph(
    graph: nx.DiGraph,
    output_path: str | Path,
    *,
    top_n: int = 100,
    pagerank: Optional[dict[str, float]] = None,
) -> Path:
    """Static visualization of the top-N influencers."""
    if graph.number_of_nodes() == 0:
        raise ValueError("Influence graph is empty")

    if pagerank is None:
        pagerank = nx.pagerank(graph, weight="weight") if graph.number_of_nodes() > 1 else {}
    top_nodes = sorted(pagerank.items(), key=lambda kv: kv[1], reverse=True)[:top_n]
    sub = graph.subgraph([n for n, _ in top_nodes]).copy()

    pos = nx.spring_layout(sub, seed=42, k=0.3)
    pr_max = max(pagerank.values()) if pagerank else 1.0
    sizes = [600 * pagerank.get(n, 0.001) / pr_max + 20 for n in sub.nodes]
    weights = [d.get("weight", 1) for _, _, d in sub.edges(data=True)]

    fig, ax = plt.subplots(figsize=(12, 9))
    nx.draw_networkx_edges(sub, pos, alpha=0.3, width=[0.3 + 0.6 * w for w in weights], ax=ax)
    nx.draw_networkx_nodes(sub, pos, node_size=sizes, node_color="#1f77b4", alpha=0.85, ax=ax)
    nx.draw_networkx_labels(sub, pos, labels={n: n[:6] + "…" for n in sub.nodes}, font_size=7, ax=ax)
    ax.set_title(f"Influence graph — top {len(sub)} wallets by PageRank")
    ax.axis("off")
    fig.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    logger.info("viz.influence.saved", path=str(out))
    return out


def plot_market_correlation_graph(
    graph: nx.Graph,
    output_path: str | Path,
    *,
    labels: Optional[dict[str, str]] = None,
) -> Path:
    """Static visualization of the market correlation graph."""
    if graph.number_of_nodes() == 0:
        raise ValueError("Market correlation graph is empty")

    pos = nx.spring_layout(graph, seed=42)
    weights = [d.get("cosine", 0.1) for _, _, d in graph.edges(data=True)]

    fig, ax = plt.subplots(figsize=(12, 9))
    nx.draw_networkx_nodes(graph, pos, node_size=120, node_color="#2ca02c", alpha=0.85, ax=ax)
    nx.draw_networkx_edges(graph, pos, alpha=0.4, width=[0.5 + 2.5 * w for w in weights], ax=ax)
    if labels:
        nx.draw_networkx_labels(graph, pos, labels=labels, font_size=7, ax=ax)
    ax.set_title("Market correlation graph (cosine similarity)")
    ax.axis("off")
    fig.tight_layout()

    out = Path(output_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, dpi=160)
    plt.close(fig)
    logger.info("viz.market_correlation.saved", path=str(out))
    return out
