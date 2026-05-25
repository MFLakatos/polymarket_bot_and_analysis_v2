# Analyze Wallet Positions

Loads a wallet's trade history (from copy trading bot logs or raw wallet downloads) and produces enriched CSVs, per-window P&L summaries, and BTC-overlaid charts for each 5-minute window.

---

## Quick Start

```bash
# 1. Download wallet data first (if not done)
poetry run wallet-download --address 0x476639d9845d7a0261cb005dae6473f089ff5a03

# 2. Analyze + plot in one step
poetry run analyze-wallet run-all 0x476639d9845d7a0261cb005dae6473f089ff5a03

# 3. Or run steps separately
poetry run analyze-wallet analyze 0x476639d9845d7a0261cb005dae6473f089ff5a03
poetry run analyze-wallet plot    0x476639d9845d7a0261cb005dae6473f089ff5a03
```

---

## CLI Reference

### `analyze` — Build enriched CSV and summary

```bash
poetry run analyze-wallet analyze <wallet_id> \
  --data-dir data/wallets \
  --output-dir output/wallet_analysis
```

Reads from `data/wallets/{wallet_id}/`:
- `detected_trades_*.csv` — from copy trading bot (preferred)
- `trades.csv` — from `wallet-download` CLI

Produces in `output/wallet_analysis/{wallet_id}/`:
- `trades_enriched.csv` — one row per trade with `secs_in_window`, P&L scenarios
- `windows_summary.csv` — one row per 5-min window with aggregate stats
- `windows.json` — full nested JSON

### `plot` — Generate per-window charts

```bash
poetry run analyze-wallet plot <wallet_id>
```

Requires `analyze` to have been run first.

Produces `output/wallet_analysis/{wallet_id}/window_plots/window_YYYYMMDD_HHMMSS.png` for each window.

Each chart shows:
- 🔵 **Blue dots** — UP buys at their implied probability
- 🔴 **Red dots** — DOWN buys at their implied probability
- 🟢 **Green step line** — cumulative net return if UP wins (right axis)
- 🟠 **Orange step line** — cumulative net return if DOWN wins (right axis)
- ⬜ **White line** — BTC 1-second price, normalised to 0.5 at window open

### `run-all` — Analyze + plot in one command

```bash
poetry run analyze-wallet run-all 0x476639d9845d7a0261cb005dae6473f089ff5a03
```

---

## Data Sources

### From copy trading bot

The bot saves detected trades to `data/copy_trading/detected_trades_*.csv`. Copy or symlink them to the wallet folder:

```bash
mkdir -p data/wallets/0x476639d9845d7a0261cb005dae6473f089ff5a03
cp data/copy_trading/detected_trades_0x476639_*.csv \
   data/wallets/0x476639d9845d7a0261cb005dae6473f089ff5a03/
```

### From wallet download

```bash
poetry run wallet-download --address 0x476639d9845d7a0261cb005dae6473f089ff5a03
# Creates data/wallets/0x476639.../trades.csv automatically
```

---

## Output Files

```
output/wallet_analysis/{wallet_id}/
├── trades_enriched.csv     ← one row per trade
├── windows_summary.csv     ← one row per 5-min window
├── windows.json            ← full nested data
└── window_plots/
    ├── window_20260522_1005.png
    ├── window_20260522_1010.png
    └── ...
```

### `trades_enriched.csv` columns

| Column | Description |
|--------|-------------|
| `window_start` | 5-minute window start (ISO) |
| `secs_in_window` | Seconds elapsed when trade was placed |
| `outcome` | `up` or `down` |
| `usdc_paid` | Cost of the trade |
| `return_if_up` | P&L if UP wins |
| `return_if_down` | P&L if DOWN wins |
| `up_price` / `down_price` | Implied probabilities |

### `windows_summary.csv` columns

| Column | Description |
|--------|-------------|
| `net_return_if_UP_wins` | Total P&L for this window if UP resolved |
| `net_return_if_DOWN_wins` | Total P&L for this window if DOWN resolved |
| `up_wavg_price` | Volume-weighted avg price of UP buys |
| `first_trade_secs` | Earliest trade in window |
| `last_trade_secs` | Latest trade in window |

---

## Using in Code

```python
from analyze_wallet_positions import analyze, plot_all

# Analyze
out_dir = analyze("0x476639d9845d7a0261cb005dae6473f089ff5a03")

# Plot
plot_all(out_dir / "trades_enriched.csv")
```
