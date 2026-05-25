# Candle Pattern Analyzer

Scans OHLCV price data and computes directional transition probabilities for sequences of N candles.

Each candle is classified as **UP** (`Close > Open`) or **DOWN** (`Close ≤ Open`). For a window of `num_candles=3`, every possible 3-candle sequence (8 total) is tracked, along with how often the **next** candle is UP vs DOWN.

---

## What It Does

Starting from the oldest candle available, for every window of `num_candles`:

1. Records a `+1` to the **total count** for that exact pattern.
2. Records a `+1` to either `UP` or `DOWN` depending on the next candle's direction.
3. Slides forward by 1 candle and repeats until the end of the data.

This produces, for every pattern like `UP → DOWN → UP`, the empirical probabilities:
- `P(next = UP | pattern)` = UP count / total count
- `P(next = DOWN | pattern)` = DOWN count / total count

---

## Quick Start

```bash
# 1. Download BTC hourly data first (if not already done)
poetry run crypto-data download --coin BTC --timeframe 1h

# 2. Run the analyzer — 3-candle patterns on BTC 1h
poetry run candle-patterns run --coin BTC --timeframe 1h --num-candles 3

# 3. Query a specific pattern
poetry run candle-patterns query output/candle_patterns/btc_1h_n3.json UP DOWN UP

# 4. Show all patterns sorted by count
poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json

# 5. Try different candle lengths
poetry run candle-patterns run --coin BTC --timeframe 1d --num-candles 5
poetry run candle-patterns run --coin BTC --timeframe 5m --num-candles 4
```

---

## CLI Reference

### `run` — Build the probability model

```bash
poetry run candle-patterns run \
  --coin BTC \
  --timeframe 1h \
  --num-candles 3 \
  --output-dir output/candle_patterns
```

Options:
| Flag | Default | Description |
|------|---------|-------------|
| `--coin` | `BTC` | Coin ID matching `config/crypto_data.yaml` |
| `--timeframe` | `1h` | Kline interval (1m, 5m, 1h, 1d, …) |
| `--num-candles` | `3` | Pattern length (1–10 recommended) |
| `--data-path` | — | Direct path to parquet file (bypasses coin/timeframe lookup) |
| `--output-dir` | `output/candle_patterns` | Where to save JSON + CSV results |

Outputs:
- `output/candle_patterns/btc_1h_n3.json` — model file (fast reload)
- `output/candle_patterns/btc_1h_n3.csv` — human-readable probability table

### `query` — Lookup a single pattern

```bash
poetry run candle-patterns query output/candle_patterns/btc_1h_n3.json UP DOWN UP
```

Output:
```
Pattern:  UP → DOWN → UP
Count:    1842 observations
P(UP):    0.5412  (54.1%)
P(DOWN):  0.4588  (45.9%)
```

### `show` — Print full probability table

```bash
# Sort by most frequent pattern
poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json

# Sort by highest P(UP)
poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json --sort-by p_up

# Only show patterns with at least 100 observations
poetry run candle-patterns show output/candle_patterns/btc_1h_n3.json --min-count 100
```

---

## Using the Model in Code

```python
from candle_pattern_analyzer import CandlePatternModel

# Load the pre-built model (fast — loads once, O(1) queries)
model = CandlePatternModel("output/candle_patterns/btc_1h_n3.json")

# Query probabilities for the last 3 candles
p_up   = model.p_up(["UP", "DOWN", "UP"])    # → 0.5412
p_down = model.p_down(["UP", "DOWN", "UP"])  # → 0.4588

# Full info dict
info = model.query(["DOWN", "DOWN", "DOWN"])
# → {"UP": 0.521, "DOWN": 0.479, "count": 1203, "pattern": ["DOWN","DOWN","DOWN"]}

# Iterate all patterns
for key, stats in model.all_patterns().items():
    print(key, stats["p_up"], stats["count"])
```

### Building a fresh model from a DataFrame

```python
import pandas as pd
from candle_pattern_analyzer import CandlePatternAnalyzer

df = pd.read_parquet("data/crypto/BTC/btc_1h.parquet")

analyzer = CandlePatternAnalyzer(num_candles=3)
analyzer.fit(df)

# Check a pattern
result = analyzer.probabilities(("UP", "DOWN", "UP"))
# → {"UP": 0.54, "DOWN": 0.46, "count": 1842}

# Save for fast reloading
analyzer.save("output/candle_patterns/btc_1h_n3.json")

# Reload later
from candle_pattern_analyzer import CandlePatternModel
model = CandlePatternModel("output/candle_patterns/btc_1h_n3.json")
```

---

## Output Files

After running `poetry run candle-patterns run`:

```
output/candle_patterns/
├── btc_1h_n3.json    ← model (reload for fast queries)
└── btc_1h_n3.csv     ← human-readable table
```

CSV columns: `pattern`, `count`, `p_up`, `p_down`, `up_count`, `down_count`

---

## Notes

- Patterns are built from non-overlapping candles in temporal order, oldest first.
- A candle with `Close == Open` is classified as **DOWN** by convention.
- If a pattern was never observed, the model returns `P(UP) = P(DOWN) = 0.5` (no information).
- More data → more reliable probabilities. Recommended minimums:
  - `num_candles=3` → at least 5,000 candles
  - `num_candles=5` → at least 50,000 candles
  - `num_candles=7` → at least 500,000 candles (use 1m or shorter timeframes)
