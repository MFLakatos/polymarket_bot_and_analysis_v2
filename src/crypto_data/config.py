"""Configuration models for the crypto_data module.

Mirrors config/crypto_data.yaml; loaded by load_crypto_config().
"""
from __future__ import annotations

import os
from pathlib import Path
from typing import List, Optional

import yaml
from pydantic import BaseModel, Field


class TimeframeConfig(BaseModel):
    # Binance kline interval string (e.g. "1s", "1m", "1h", "1d")
    interval: str
    # How many hours of history to download
    hours: int
    # Filename relative to storage.base_path/{coin_id}/
    filename: str


class CoinConfig(BaseModel):
    # Short identifier used in CLI and as directory name (e.g. "BTC")
    id: str
    # Binance trading pair symbol (e.g. "BTCUSDT")
    symbol: str
    # Human-readable name for labels and titles
    name: str
    # Timeframes to download for this coin
    timeframes: List[TimeframeConfig] = Field(default_factory=list)


class SourceConfig(BaseModel):
    # Exchange backend (currently only "binance")
    exchange: str = "binance"
    # Binance REST API base URL
    base_url: str = "https://api.binance.com"
    # Records per request page
    page_size: int = 1000
    # Seconds between pages to respect rate limits
    rate_limit_delay: float = 0.08
    # HTTP timeout per request
    timeout_seconds: int = 15


class StorageConfig(BaseModel):
    # Root directory for all downloaded data
    base_path: str = "data/crypto"
    # Skip download if file already exists
    use_cache: bool = True
    # Parquet compression codec
    parquet_compression: str = "snappy"


class IndicatorsConfig(BaseModel):
    sma: List[int] = Field(default_factory=lambda: [20, 50, 200])
    ema: List[int] = Field(default_factory=lambda: [12, 26])
    rsi_period: int = 14
    macd_fast: int = 12
    macd_slow: int = 26
    macd_signal: int = 9
    bb_period: int = 20
    bb_std: float = 2.0


class AnalysisConfig(BaseModel):
    indicators: IndicatorsConfig = Field(default_factory=IndicatorsConfig)
    # 0 = load all available rows
    default_lookback: int = 0


class VisualizationConfig(BaseModel):
    style: str = "dark"
    figsize: List[int] = Field(default_factory=lambda: [16, 9])
    output_path: str = "output/charts"
    auto_open: bool = False


class LoggingConfig(BaseModel):
    level: str = "INFO"
    use_json: bool = False


class SessionFilterModel(BaseModel):
    # Master switch: if false, no session filtering is applied
    enabled: bool = False
    # Sessions to KEEP — all others are dropped.
    # Available: Asia, Europe, New_York, Overlap_EU_NY, Overlap_Asia_EU,
    #            New_York_Extended, All
    active_sessions: List[str] = Field(default_factory=list)


class FiltersConfig(BaseModel):
    # Drop Saturday and Sunday candles (UTC day)
    exclude_weekends: bool = False
    # Drop candles outside NYSE regular hours (09:30-16:00 ET, DST-aware)
    exclude_outside_nyse: bool = False
    # Drop candles on known NYSE public holidays
    exclude_nyse_holidays: bool = False
    # Keep only candles within the listed trading sessions
    sessions: SessionFilterModel = Field(default_factory=SessionFilterModel)

    def to_filter_config(self):
        """Convert to the FilterConfig object used by apply_filters()."""
        from crypto_data.filters import FilterConfig, SessionFilterConfig
        return FilterConfig(
            exclude_weekends=self.exclude_weekends,
            exclude_outside_nyse=self.exclude_outside_nyse,
            exclude_nyse_holidays=self.exclude_nyse_holidays,
            sessions=SessionFilterConfig(
                enabled=self.sessions.enabled,
                active=list(self.sessions.active_sessions),
            ),
        )


class CryptoDataConfig(BaseModel):
    source: SourceConfig = Field(default_factory=SourceConfig)
    coins: List[CoinConfig] = Field(default_factory=list)
    storage: StorageConfig = Field(default_factory=StorageConfig)
    analysis: AnalysisConfig = Field(default_factory=AnalysisConfig)
    visualization: VisualizationConfig = Field(default_factory=VisualizationConfig)
    filters: FiltersConfig = Field(default_factory=FiltersConfig)
    logging: LoggingConfig = Field(default_factory=LoggingConfig)

    def get_coin(self, coin_id: str) -> Optional[CoinConfig]:
        """Lookup a coin config by its id (case-insensitive)."""
        for c in self.coins:
            if c.id.upper() == coin_id.upper():
                return c
        return None

    def data_path(self, coin_id: str, filename: str) -> Path:
        """Resolve the full path for a coin/timeframe file."""
        return Path(self.storage.base_path) / coin_id.upper() / filename


def load_crypto_config(path: Optional[str | Path] = None) -> CryptoDataConfig:
    """Load crypto_data.yaml; falls back to config/crypto_data.yaml or defaults."""
    candidate = path or os.getenv("CRYPTO_CONFIG_PATH") or "config/crypto_data.yaml"
    cfg_path = Path(candidate)
    raw: dict = {}
    if cfg_path.exists():
        with cfg_path.open("r", encoding="utf-8") as fh:
            raw = yaml.safe_load(fh) or {}
    return CryptoDataConfig(**raw)
