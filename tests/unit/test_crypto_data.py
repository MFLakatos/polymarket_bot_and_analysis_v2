"""Unit tests for crypto_data config and PriceLoader."""
from __future__ import annotations

import tempfile
from pathlib import Path

import pandas as pd
import pytest

from crypto_data.config import CryptoDataConfig, load_crypto_config
from crypto_data.loaders.price_loader import PriceLoader


# ── Config tests ─────────────────────────────────────────────────────────────

def test_default_config_loads():
    cfg = CryptoDataConfig()
    assert cfg.source.exchange == "binance"
    assert cfg.storage.base_path == "data/crypto"


def test_load_crypto_config_from_yaml(tmp_path):
    yaml_content = """
source:
  exchange: binance
  page_size: 500
coins:
  - id: BTC
    symbol: BTCUSDT
    name: Bitcoin
    timeframes:
      - interval: "1h"
        hours: 100
        filename: btc_1h.parquet
storage:
  base_path: data/crypto
  use_cache: true
"""
    cfg_file = tmp_path / "test_crypto.yaml"
    cfg_file.write_text(yaml_content)
    cfg = load_crypto_config(str(cfg_file))
    assert cfg.source.page_size == 500
    assert len(cfg.coins) == 1
    assert cfg.coins[0].id == "BTC"
    assert cfg.coins[0].timeframes[0].interval == "1h"


def test_get_coin_case_insensitive():
    cfg = load_crypto_config.__wrapped__ if hasattr(load_crypto_config, "__wrapped__") else None
    from crypto_data.config import CryptoDataConfig, CoinConfig, TimeframeConfig
    cfg = CryptoDataConfig(
        coins=[CoinConfig(id="BTC", symbol="BTCUSDT", name="Bitcoin",
                          timeframes=[TimeframeConfig(interval="1h", hours=100, filename="f.parquet")])]
    )
    assert cfg.get_coin("btc") is not None
    assert cfg.get_coin("BTC") is not None
    assert cfg.get_coin("ETH") is None


def test_data_path_resolution():
    from crypto_data.config import CryptoDataConfig
    cfg = CryptoDataConfig()
    p = cfg.data_path("BTC", "btc_1h.parquet")
    assert "BTC" in str(p)
    assert "btc_1h.parquet" in str(p)


# ── PriceLoader tests ────────────────────────────────────────────────────────

@pytest.fixture
def sample_df():
    import numpy as np
    n = 300
    dates = pd.date_range("2025-01-01", periods=n, freq="1h", tz="UTC")
    close = 50000 + np.cumsum(np.random.randn(n) * 100)
    return pd.DataFrame({
        "open_time": dates,
        "open": close - 50,
        "high": close + 100,
        "low": close - 100,
        "close": close,
        "volume": np.random.rand(n) * 1000,
        "close_time": dates + pd.Timedelta(hours=1),
    })


@pytest.fixture
def loader_with_data(tmp_path, sample_df):
    from crypto_data.config import CryptoDataConfig, CoinConfig, TimeframeConfig, StorageConfig
    cfg = CryptoDataConfig(
        coins=[CoinConfig(
            id="BTC", symbol="BTCUSDT", name="Bitcoin",
            timeframes=[TimeframeConfig(interval="1h", hours=720, filename="btc_1h.parquet")]
        )],
        storage=StorageConfig(base_path=str(tmp_path / "crypto")),
    )
    out_path = cfg.data_path("BTC", "btc_1h.parquet")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    sample_df.to_parquet(out_path, index=False)
    return PriceLoader(cfg)


def test_load_raw(loader_with_data, sample_df):
    df = loader_with_data.load_raw("BTC", "1h")
    assert len(df) == len(sample_df)
    assert "close" in df.columns


def test_load_with_lookback(loader_with_data):
    df = loader_with_data.load("BTC", "1h", lookback=50)
    assert len(df) == 50


def test_load_adds_indicators(loader_with_data):
    df = loader_with_data.load("BTC", "1h", compute_indicators=True)
    assert "sma_20" in df.columns
    assert "rsi" in df.columns
    assert "macd" in df.columns
    assert "bb_upper" in df.columns


def test_load_missing_coin_raises(loader_with_data):
    with pytest.raises(ValueError, match="not in config"):
        loader_with_data.load_raw("ETH", "1h")


def test_load_missing_file_raises(loader_with_data):
    from crypto_data.config import CoinConfig, TimeframeConfig
    loader_with_data.config.coins.append(
        CoinConfig(id="ETH", symbol="ETHUSDT", name="Ethereum",
                   timeframes=[TimeframeConfig(interval="1h", hours=100, filename="eth_1h.parquet")])
    )
    with pytest.raises(FileNotFoundError):
        loader_with_data.load_raw("ETH", "1h")


def test_list_available(loader_with_data):
    avail = loader_with_data.list_available()
    assert "BTC" in avail
    assert "1h" in avail["BTC"]


def test_info_existing(loader_with_data):
    info = loader_with_data.info("BTC", "1h")
    assert info["exists"] is True
    assert info["rows"] == 300
