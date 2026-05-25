"""Wallet clustering pipeline: scaling → PCA → KMeans/DBSCAN → silhouette."""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd
from sklearn.cluster import DBSCAN, KMeans
from sklearn.decomposition import PCA
from sklearn.metrics import silhouette_score
from sklearn.preprocessing import StandardScaler

from polymarket_graph.infrastructure.config import ClusteringConfig
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)

CLUSTERING_FEATURES: tuple[str, ...] = (
    "total_trades", "total_volume", "avg_trade_size", "pnl",
    "win_rate", "early_participation_score", "market_diversity", "avg_holding_time_hours",
)


@dataclass
class ClusteringResult:
    labels: np.ndarray
    embedding: np.ndarray       # 2D PCA projection
    silhouette: float | None
    algorithm: Literal["kmeans", "dbscan"]
    feature_names: tuple[str, ...]


def cluster_wallets(
    features_df: pd.DataFrame,
    config: ClusteringConfig,
    *,
    feature_columns: tuple[str, ...] = CLUSTERING_FEATURES,
) -> tuple[pd.DataFrame, ClusteringResult]:
    """Cluster wallets and return (annotated DataFrame, ClusteringResult)."""
    if features_df.empty:
        raise ValueError("features_df is empty — nothing to cluster")
    missing = [c for c in feature_columns if c not in features_df.columns]
    if missing:
        raise ValueError(f"Missing feature columns: {missing}")

    X = features_df[list(feature_columns)].to_numpy(dtype=float, copy=True)
    X = np.nan_to_num(X, nan=0.0, posinf=0.0, neginf=0.0)

    scaler = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    n_components = max(2, min(config.pca_components, X_scaled.shape[1]))
    pca = PCA(n_components=n_components, random_state=42)
    embedding = pca.fit_transform(X_scaled)

    if config.algorithm == "kmeans":
        model = KMeans(n_clusters=config.kmeans.k, random_state=config.kmeans.random_state, n_init=10)
        labels = model.fit_predict(X_scaled)
    elif config.algorithm == "dbscan":
        model = DBSCAN(eps=config.dbscan.eps, min_samples=config.dbscan.min_samples)
        labels = model.fit_predict(X_scaled)
    else:
        raise ValueError(f"Unknown algorithm: {config.algorithm}")

    unique = set(labels) - {-1}
    sil: float | None = None
    if len(unique) > 1 and len(labels) > len(unique):
        try:
            sil = float(silhouette_score(X_scaled, labels))
        except ValueError:
            sil = None

    logger.info("clustering.done", algorithm=config.algorithm,
                n_clusters=len(unique), silhouette=sil, n_wallets=len(labels))

    annotated = features_df.copy()
    annotated["cluster"] = labels
    annotated["pca1"] = embedding[:, 0]
    annotated["pca2"] = embedding[:, 1] if embedding.shape[1] > 1 else 0.0

    return annotated, ClusteringResult(
        labels=labels, embedding=embedding, silhouette=sil,
        algorithm=config.algorithm, feature_names=tuple(feature_columns),
    )
