"""Configuration loading with YAML + environment overrides.

Pattern: YAML provides defaults & tuning; environment variables provide
secrets and infra overrides. Pydantic validates everything at the boundary.

Config file resolution order:
  1. path argument
  2. $POLYMARKET_CONFIG_PATH env var
  3. $CONFIG_PATH env var (legacy)
  4. config/polymarket.yaml
  5. config.yaml (root fallback)
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import Literal, Optional

import yaml
from pydantic import BaseModel, Field, SecretStr


class GammaApiConfig(BaseModel):
    # Base URL for market metadata endpoints
    base_url: str = "https://gamma-api.polymarket.com"
    # Base URL for trade history endpoints
    trades_base_url: str = "https://data-api.polymarket.com"
    # Records per HTTP page
    batch_size: int = 100
    # Max requests per second
    rate_limit_per_sec: float = 5.0
    # Retry count on failure
    max_retries: int = 5
    # Per-request timeout (seconds)
    timeout_seconds: int = 30


class ClobApiConfig(BaseModel):
    # CLOB API base URL
    base_url: str = "https://clob.polymarket.com"
    # Records per page (larger than Gamma for efficiency)
    batch_size: int = 500
    # Max requests per second
    rate_limit_per_sec: float = 8.0
    # Retry count
    max_retries: int = 5
    # Timeout (seconds)
    timeout_seconds: int = 30


class IngestionConfig(BaseModel):
    gamma_api: GammaApiConfig = Field(default_factory=GammaApiConfig)
    clob_api: ClobApiConfig = Field(default_factory=ClobApiConfig)
    # JSON file that tracks the last ingested timestamp
    checkpoint_path: str = "data/checkpoints/ingestion_state.json"
    # Directory for raw JSONL from APIs
    raw_path: str = "data/raw"
    # Directory for cleaned data
    intermediate_path: str = "data/intermediate"


class Neo4jConfig(BaseModel):
    # Bolt or neo4j+s connection string
    uri: str = "bolt://localhost:7687"
    # Database user
    user: str = "neo4j"
    # Database password (override with NEO4J_PASSWORD env var)
    password: SecretStr = SecretStr("neo4j")
    # Target database name
    database: str = "neo4j"
    # Nodes/relationships per Cypher batch
    batch_size: int = 5000


class FeaturesConfig(BaseModel):
    # Exclude wallets with fewer trades than this
    min_trades_per_wallet: int = 5
    # Time window for "early trader" flag
    early_window_minutes: int = 60
    # Run PCA for 2D visualization
    enable_pca: bool = True
    # Run UMAP (requires viz extra)
    enable_umap: bool = False
    # Push live updates to Neo4j during analysis
    enable_live_updates: bool = False


class KMeansConfig(BaseModel):
    # Number of clusters
    k: int = 6
    # Reproducibility seed
    random_state: int = 42


class DBSCANConfig(BaseModel):
    # Neighborhood radius
    eps: float = 0.5
    # Minimum cluster size
    min_samples: int = 10


class ClusteringConfig(BaseModel):
    # kmeans | dbscan
    algorithm: Literal["kmeans", "dbscan"] = "kmeans"
    kmeans: KMeansConfig = Field(default_factory=KMeansConfig)
    dbscan: DBSCANConfig = Field(default_factory=DBSCANConfig)
    # PCA dimensions before clustering
    pca_components: int = 2


class InfluenceConfig(BaseModel):
    # Look-back window for lead/lag detection (minutes)
    lag_window_minutes: int = 30
    # Min followers to qualify as influencer
    min_followers: int = 3
    # PageRank damping factor
    pagerank_alpha: float = 0.85


class MarketCorrelationConfig(BaseModel):
    # Min shared-wallet count to link two markets
    min_shared_wallets: int = 5
    # Cosine similarity threshold
    cosine_threshold: float = 0.3


class OutputConfig(BaseModel):
    # Root output directory
    base_path: str = "output"
    # csv | json
    export_format: Literal["csv", "json"] = "csv"


class LoggingConfig(BaseModel):
    # DEBUG | INFO | WARNING | ERROR
    level: str = "INFO"
    # True = JSON, False = human-readable
    use_json: bool = False


class AppConfig(BaseModel):
    # gamma_api | clob_api | hybrid
    api_mode: Literal["gamma_api", "clob_api", "hybrid"] = "hybrid"
    ingestion: IngestionConfig = Field(default_factory=IngestionConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    features: FeaturesConfig = Field(default_factory=FeaturesConfig)
    clustering: ClusteringConfig = Field(default_factory=ClusteringConfig)
    influence: InfluenceConfig = Field(default_factory=InfluenceConfig)
    market_correlation: MarketCorrelationConfig = Field(default_factory=MarketCorrelationConfig)
    output: OutputConfig = Field(default_factory=OutputConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def apply_env_overrides(self) -> "AppConfig":
        """Apply environment-based overrides for secrets and infra."""
        data = self.model_dump()
        if uri := os.getenv("NEO4J_URI"):
            data["neo4j"]["uri"] = uri
        if user := os.getenv("NEO4J_USER"):
            data["neo4j"]["user"] = user
        if pwd := os.getenv("NEO4J_PASSWORD"):
            data["neo4j"]["password"] = pwd
        if db := os.getenv("NEO4J_DATABASE"):
            data["neo4j"]["database"] = db
        if lvl := os.getenv("LOG_LEVEL"):
            data["logging"]["level"] = lvl
        return AppConfig(**data)


def load_config(path: Optional[str | Path] = None) -> AppConfig:
    """Load polymarket config YAML and apply env overrides.

    Resolution order:
      1. path argument
      2. $POLYMARKET_CONFIG_PATH
      3. $CONFIG_PATH (legacy)
      4. config/polymarket.yaml
      5. config.yaml
    """
    candidates = [
        path,
        os.getenv("POLYMARKET_CONFIG_PATH"),
        os.getenv("CONFIG_PATH"),
        "config/polymarket.yaml",
        "config.yaml",
    ]
    raw: dict = {}
    for candidate in candidates:
        if candidate:
            cfg_path = Path(candidate)
            if cfg_path.exists():
                with cfg_path.open("r", encoding="utf-8") as fh:
                    raw = yaml.safe_load(fh) or {}
                break
    return AppConfig(**raw).apply_env_overrides()
