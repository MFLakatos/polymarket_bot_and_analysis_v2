# BTC Reversal Model

Estimates the probability that BTC's price direction **reverses** before a 5-minute window ends, given the current signed move from window open and the seconds remaining.

This is a clean, importable version of the original `btc_price_1s` analysis, refactored for reuse across the codebase.

---

## Core Concept

Every Polymarket "BTC Up or Down 5m" market resolves based on whether BTC is higher or lower than the **opening price of the 5-minute window**. This model answers:

> *Given BTC moved +$X from the window open with Y seconds remaining — what is P(it reverses direction before the window ends)?*

**Low P(reversal) → high confidence the current direction holds → trade that direction.**

---

## How It Works

### Step 1 — Build the Dataset

```bash
poetry run build-reversal-dataset
```

This downloads ~10,000 hours of 1-second BTC price data from Binance (or loads from cache), slices it into non-overlapping 5-minute (300 s) windows, and labels each second:

| Column | Description |
|--------|-------------|
| `delta_usd` | `price[t] - price[0]` — signed USD move from window start |
| `remaining_seconds` | `300 - t` — seconds left in window |
| `flip` | `1` if the delta sign ever reverses at any second in `[t+1, 299]` |

Output: `data/crypto/BTC/reversal_dataset.parquet`

### Step 2 — Load the Model

```python
from btc_reversal_model import ReversalModel

model = ReversalModel()   # loads dataset, builds grid (~5 seconds first time)

# Query: BTC opened at $95,000, currently at $95,250, with 180 seconds left
p = model.probability(
    target_price=95_000,
    current_price=95_250,
    time_left=180,
)
print(f"P(reversal) = {p:.3f}")   # e.g. 0.127

# Or if you already have the delta
p = model.probability_from_delta(delta_usd=250.0, remaining_seconds=180)
```

---

## Performance

- **First load**: ~5 seconds (builds 200×60 grid via kernel regression)
- **Subsequent queries**: **<0.1 ms** (pure dict/array lookup)
- **Memory**: ~50 MB for the full dataset in RAM

---

## CLI

```bash
# Build / rebuild the dataset
poetry run build-reversal-dataset

# Build with more hours of data
poetry run build-reversal-dataset --hours 20000

# Build with custom output path
poetry run build-reversal-dataset --output data/crypto/BTC/my_reversal.parquet
```

---

## Files

```
src/btc_reversal_model/
├── __init__.py          # public API: ReversalModel, build
├── build_dataset.py     # downloads 1s data, builds reversal_dataset.parquet
├── reversal_model.py    # ReversalModel — fast probability query
└── README.md            # this file

data/crypto/BTC/
├── btc_1s.parquet           # cached 1-second price series
└── reversal_dataset.parquet # labeled (delta, remaining, flip) dataset
```

---

## Tuning

The `ReversalModel` constructor accepts bandwidth parameters:

```python
model = ReversalModel(
    dataset_path="data/crypto/BTC/reversal_dataset.parquet",
    delta_bw=50.0,      # USD bandwidth (wider = smoother, less sensitive to small moves)
    time_bw=30.0,       # seconds bandwidth (wider = smoother across time)
    grid_delta_bins=200,
    grid_time_bins=60,
)
```

- Increase `delta_bw` if probabilities are noisy for small delta values.
- Increase `grid_delta_bins` / `grid_time_bins` for finer resolution (slower init).
