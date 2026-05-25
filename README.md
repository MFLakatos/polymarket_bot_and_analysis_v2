# Polymarket Graph — Refactored

Production-ready analytics, trading bots, and crypto data tools for Polymarket, built with Poetry, Docker, and Neo4j.

---

## Project Layout

```
polymarket_graph/
├── config/                         ← All configuration files (one per module)
│   ├── polymarket.yaml             ← Graph analytics pipeline settings
│   ├── crypto_data.yaml            ← Crypto price download & analysis settings
│   ├── copy_trading.yaml           ← Copy trading bot settings
│   └── btc_5m_bot.yaml             ← BTC 5m reversal bot settings
│
├── src/
│   ├── polymarket_graph/           ← Graph analytics pipeline (Neo4j + ML)
│   │   ├── domain/                 ← Core entities: Wallet, Market, Trade, Outcome
│   │   ├── application/            ← Use cases (orchestration, no I/O details)
│   │   ├── adapters/
│   │   │   ├── extraction/         ← Gamma API + Data API + CLOB API clients
│   │   │   ├── transform/          ← Normalization & feature engineering
│   │   │   └── loading/            ← Neo4j ingestion
│   │   ├── analytics/
│   │   │   ├── clustering/         ← KMeans/DBSCAN wallet grouping
│   │   │   ├── influence_graph/    ← Temporal lead/lag detection (PageRank)
│   │   │   ├── market_correlation/ ← Co-trading + cosine similarity
│   │   │   └── visualization/      ← PCA scatter, NetworkX, PyVis HTML
│   │   ├── infrastructure/         ← Config loader, logging, Neo4j client
│   │   └── cli.py                  ← polymarket-graph CLI
│   │
│   ├── crypto_data/                ← NEW: reusable crypto price data module
│   │   ├── downloaders/            ← Binance OHLCV downloader (extensible)
│   │   ├── loaders/                ← Load parquet + compute indicators
│   │   ├── visualization/          ← Interactive Plotly charts
│   │   ├── config.py               ← Pydantic config models
│   │   └── cli.py                  ← crypto-data CLI
│   │
│   ├── copy_wallets_positions/     ← Copy trading bot
│   ├── btc_price_1s/               ← BTC 5m reversal bot
│   ├── download_wallet_positions/  ← Wallet data download utility
│   └── save_wallet_positions/      ← CSV saving helpers
│
├── data/
│   ├── raw/                        ← Raw JSONL from Polymarket APIs
│   ├── intermediate/               ← Cleaned & normalized data
│   ├── checkpoints/                ← Ingestion state tracking
│   └── crypto/
│       └── BTC/                    ← Downloaded BTC price files (parquet)
│
├── output/                         ← Generated charts, CSVs, HTML dashboards
├── neo4j/import/                   ← Neo4j import directory
├── tests/
│   ├── unit/                       ← Fast, no-network tests
│   └── integration/                ← Require running Neo4j
│
├── docs/                           ← Documentation
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
└── .env.example
```

---

## Quickstart

### 1. Install

```bash
pyenv local 3.12.9
# Install all dependencies
poetry install

# With optional extras
poetry install --extras viz          # UMAP + PyVis
poetry install --extras copy-trading # CLOB trading client
poetry install --extras all          # Everything
```

### 2. Configure

```bash
cp .env.example .env
# Edit .env with Neo4j password and wallet keys
```

Review and edit the config files in `config/`:
- `config/polymarket.yaml` — graph analytics settings
- `config/crypto_data.yaml` — crypto download settings
- `config/copy_trading.yaml` — copy bot settings
- `config/btc_5m_bot.yaml` — BTC reversal bot settings

### 3. Start Neo4j

```bash
docker compose up -d neo4j
```

### 4. Run

```bash
# Full analytics pipeline
poetry run polymarket-graph run-all

# Download BTC price data
poetry run crypto-data download

# Copy trading bot
poetry run copy-trading

# BTC 5m bot
poetry run btc-5m-bot
```

---

## CLIs

### `polymarket-graph` — Graph Analytics Pipeline

```bash
# Full pipeline (ingest → transform → load → analyze → export)
poetry run polymarket-graph run-all

# Step by step
poetry run polymarket-graph init-schema        # Create Neo4j indexes/constraints
poetry run polymarket-graph ingest             # Pull from Polymarket APIs
poetry run polymarket-graph ingest --since "2026-04-01T00:00:00" --until "2026-05-01T00:00:00"
poetry run polymarket-graph transform          # Normalize & feature engineer
poetry run polymarket-graph load               # Push to Neo4j
poetry run polymarket-graph cluster-wallets    # KMeans/DBSCAN clustering
poetry run polymarket-graph build-influence-graph
poetry run polymarket-graph build-market-correlation
poetry run polymarket-graph export-results     # Save CSVs + open HTML dashboards
poetry run polymarket-graph visualize          # Generate PNG + HTML charts

# Use a specific config
poetry run polymarket-graph --config config/polymarket.yaml run-all
```

### `crypto-data` — Crypto Price Data

```bash
# Download everything from config
poetry run crypto-data download

# Download specific coin/timeframe
poetry run crypto-data download --coin BTC --timeframe 1h
poetry run crypto-data download --coin BTC --timeframe 5m --force  # bypass cache

# See what's available locally
poetry run crypto-data info
poetry run crypto-data info --coin BTC

# Interactive price chart (opens in browser)
poetry run crypto-data plot --coin BTC --timeframe 1h --lookback 200 --open

# Print stats + indicators
poetry run crypto-data analyze --coin BTC --timeframe 1d

# Custom config
poetry run crypto-data --config config/crypto_data.yaml download
```

### `copy-trading` — Copy Trading Bot

```bash
export POLYMARKET_PRIVATE_KEY="0x..."
poetry run copy-trading --config config/copy_trading.yaml
```

### `btc-5m-bot` — BTC Reversal Bot

```bash
# First generate the reversal probability dataset
poetry run python src/btc_price_1s/price_reversal_probability_estimator.py

# Run the bot
poetry run btc-5m-bot --config config/btc_5m_bot.yaml
```

### `wallet-download` — Wallet Position Download

```bash
poetry run wallet-download --address 0x... --output data/wallets/
```

---

## Docker

```bash
# Start Neo4j
docker compose up -d neo4j

# Run full analytics pipeline
docker compose run app run-all

# Run specific step
docker compose run app ingest

# Download crypto data
docker compose --profile crypto run crypto-data download

# Build and start everything
docker compose up -d
```

---

## Testing

```bash
# Unit tests (fast, no external deps)
poetry run pytest tests/unit/ -v

# Integration tests (require running Neo4j)
NEO4J_INTEGRATION_TESTS=1 poetry run pytest tests/integration/ -v

# All tests with coverage
poetry run pytest --cov=src --cov-report=html
```

### Testing Neo4j Specifically

```bash
# Start Neo4j first
docker compose up -d neo4j

# Then run integration tests
NEO4J_INTEGRATION_TESTS=1 poetry run pytest tests/integration/test_neo4j.py -v
```

---

## Configuration Reference

Each config file in `config/` is self-documented with comments. Here's a summary:

| File | Purpose | Key Settings |
|------|---------|--------------|
| `polymarket.yaml` | Graph analytics | api_mode, neo4j, clustering.k, influence.lag_window_minutes |
| `crypto_data.yaml` | Price data | coins[], timeframes[], storage.base_path, indicators |
| `copy_trading.yaml` | Copy bot | wallets[], risk.max_trade_amount_usdc, monitor.poll_interval_seconds |
| `btc_5m_bot.yaml` | BTC reversal bot | trading.enabled, trading.tiers[], general.poll_interval_seconds |

All secrets (passwords, private keys) go in `.env` — never in YAML config files.

---

## Adding a New Coin

1. Edit `config/crypto_data.yaml`, add under `coins:`:

```yaml
- id: "ETH"
  symbol: "ETHUSDT"
  name: "Ethereum"
  timeframes:
    - interval: "1h"
      hours: 8760
      filename: "eth_1h.parquet"
    - interval: "1d"
      hours: 87600
      filename: "eth_1d.parquet"
```

2. Download:

```bash
poetry run crypto-data download --coin ETH
```

3. Analyze:

```bash
poetry run crypto-data analyze --coin ETH --timeframe 1h
poetry run crypto-data plot --coin ETH --timeframe 1d --open
```

---

## Neo4j Graph Schema

See `docs/graph_schema.md` for full documentation of nodes, relationships, properties, and indexes.

Access Neo4j Browser at `http://localhost:7474` after starting with Docker.

```cypher
-- Find smart money clusters
MATCH (w:Wallet)-[:TRADED]->(m:Market)
WHERE w.cluster = 0
RETURN w, m LIMIT 50

-- Correlated markets
MATCH (m1:Market)-[r:CORRELATED_WITH]-(m2:Market)
WHERE r.cosine > 0.5
RETURN m1.question, m2.question, r.cosine
```

---

## Data Flow

```
Binance API
    ↓
crypto_data.downloaders (BinanceDownloader)
    ↓
data/crypto/{COIN}/{timeframe}.parquet
    ↓
crypto_data.loaders (PriceLoader + indicators)
    ↓
Analysis / Visualization / Bots

Polymarket APIs (Gamma + Data + CLOB)
    ↓
polymarket_graph.adapters.extraction
    ↓
data/raw/ (JSONL)
    ↓
polymarket_graph.adapters.transform
    ↓
data/intermediate/ (normalized)
    ↓
polymarket_graph.adapters.loading → Neo4j
    ↓
polymarket_graph.analytics (clustering, influence, correlation)
    ↓
output/ (CSV, PNG, HTML)
```
