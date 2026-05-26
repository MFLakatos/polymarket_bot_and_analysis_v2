# BTC 5-Minute Reversal Bot

Monitors Polymarket's **"BTC Up or Down 5m"** market and uses a statistical reversal probability model to decide when to trade. Includes a full **simulator** that replays historical trade logs to show what P&L would have been.

---

## Quick Start

### 1. Build the reversal model dataset (one time)

```bash
poetry run btc-5m-bot build-dataset
# Downloads ~10,000h of 1s BTC data and builds data/crypto/BTC/reversal_dataset.parquet
```

### 2. Run in monitor-only mode

```bash
# Ensure trading.enabled = false in config/btc_5m_bot.yaml
poetry run btc-5m-bot run
```

### 3. Simulate against historical trades

```bash
poetry run btc-5m-bot simulate data/copy_trading/detected_trades_0x476639_20260522.csv
```

---

## CLI Commands

### `run` — Live bot

```bash
poetry run btc-5m-bot run --config config/btc_5m_bot.yaml
```

Set `trading.enabled: true` in config and `POLYMARKET_PRIVATE_KEY` env var for live orders.

### `simulate` — Replay historical trades

```bash
poetry run btc-5m-bot simulate <path_to_detected_trades.csv> \
  --config config/btc_5m_bot.yaml \
  --output output/simulation/sim_report.csv \
  --keyword "5m"
```

Output:
```
══════════════════════════════════════════════════════════════════════
  BTC 5m Reversal Bot — Simulation Report
══════════════════════════════════════════════════════════════════════
  Total windows analysed :  47
  Trades placed (BUY)    :  23
  Trades skipped         :  91
  Total USDC spent       : $34.50
  Net P&L if ALL UP win  : +$14.20
  Net P&L if ALL DOWN win: -$12.80

  Window               Trades    Spent    If UP    If DN  P(rev)avg
  ──────────────────────────────────────────────────────────────────
  2026-05-22T10:05         2   $ 3.00   +1.20   -1.50      0.081
  2026-05-22T10:10         1   $ 1.00   +0.40   -0.60      0.094
  ...
```

### `build-dataset` — Download 1s data and build dataset

```bash
poetry run btc-5m-bot build-dataset --hours 20000
```

---

## How It Works

```
Every 5 minutes:
  1. CAPTURE  — Fetch BTC/USD at window boundary (= "target price")
  2. POLL     — Every N seconds, fetch current BTC price
  3. COMPUTE  — delta_usd = current - target_price
                remaining  = 300 - seconds_elapsed
  4. MODEL    — P(reversal) = ReversalModel(delta_usd, remaining)
  5. TIERS    — If P(rev) < threshold → BUY shares
  6. DISPLAY  — Show live terminal dashboard
```

### Tier System (from `config/btc_5m_bot.yaml`)

```yaml
trading:
  tiers:
    - max_reversal_prob: 0.10   # P(rev) < 10% → very confident → buy 3 shares
      shares: 3
    - max_reversal_prob: 0.20   # P(rev) < 20% → confident → buy 2 shares
      shares: 2
    - max_reversal_prob: 0.30   # P(rev) < 30% → moderate → buy 1 share
      shares: 1
    # P(rev) >= 30% → SKIP (too risky)
```

---

## Configuration (`config/btc_5m_bot.yaml`)

Key settings:

| Setting | Default | Description |
|---------|---------|-------------|
| `general.poll_interval_seconds` | `2.0` | Seconds between price polls |
| `general.reversal_dataset_path` | `data/crypto/BTC/reversal_dataset.parquet` | Model dataset |
| `trading.enabled` | `false` | Set to `true` for live orders |
| `trading.max_trades_per_window` | `3` | Max trades per 5-min window |
| `trading.min_seconds_remaining` | `30` | Don't trade in the last N seconds |
| `trading.signature_type` | `0` | CLOB signature type (0=EOA, 1=Magic) |

---

## Files

```
src/btc_5m_trader/
├── __init__.py      # exports BTC5mBot, run_simulation
├── bot.py           # main bot loop with live price polling
├── simulator.py     # replay historical trades → P&L report
├── cli.py           # CLI: run, simulate, build-dataset
└── README.md        # this file

Related:
  src/btc_reversal_model/   # ReversalModel used by this bot
  config/btc_5m_bot.yaml    # all settings
```
