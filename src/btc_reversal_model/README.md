# BTC Reversal Model

Estimates the probability that BTC's price direction **reverses** before a 5-minute window ends, given the current signed move from window open and the seconds remaining.

---

## Core Concept

Every Polymarket "BTC Up or Down 5m" market resolves based on whether BTC is higher or lower than the **opening price of the 5-minute window**. This model answers:

> *Given BTC moved +$X from the window open with Y seconds remaining — what is P(it reverses direction before the window ends)?*

**Low P(reversal) → high confidence the current direction holds → trade that direction.**

---

## How It Works

### Step 1 — Build the Dataset

```bash
# All hours, all sessions (BTC trades 24/7)
poetry run build-reversal-dataset

# Only windows inside NYSE trading hours (Mon–Fri 09:30–16:00 ET, excl. Federal holidays)
poetry run build-reversal-dataset --filter-market-hours

# More data, custom output
poetry run build-reversal-dataset --hours 20000 --output data/crypto/BTC/my_reversal.parquet
```

This downloads ~10,000 hours of 1-second BTC price data from Binance (or loads from cache at `data/crypto/BTC/btc_1s.parquet`), slices it into non-overlapping 5-minute (300 s) windows, and labels each second:

| Column | Description |
|--------|-------------|
| `delta_usd` | `price[t] - price[0]` — signed USD move from window start |
| `remaining_seconds` | `300 - t` — seconds left in window |
| `flip` | `1` if the delta sign ever reverses at any second in `[t+1, 299]` |

Output: `data/crypto/BTC/reversal_dataset.parquet`

#### Market-Hours Filter

BTC trades 24/7, but Polymarket markets mostly have active trading during NYSE hours. The `--filter-market-hours` flag drops any 5-minute window whose start timestamp falls outside:

- **Days**: Monday – Friday only (weekends excluded)
- **Hours**: 09:30 – 16:00 US/Eastern
- **Holidays**: US Federal holiday calendar (proxy for NYSE). Note: Good Friday is a NYSE holiday but not a Federal one, so it is **not** excluded. Columbus Day and Veterans Day are excluded by this filter but NYSE stays open — the discrepancy is negligible for modelling purposes.

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

### Step 3 — Visualize

```bash
# Generate interactive HTML (opens in any browser)
poetry run visualize-reversal --output output/reversal_viz.html

# If the dataset was built with --filter-market-hours, pass the flag so the
# Dataset Info tab shows the correct settings
poetry run visualize-reversal --filter-market-hours --output output/reversal_viz.html

open output/reversal_viz.html
```

The HTML has four tabs:

| Tab | What you see |
|-----|-------------|
| **3D Surface** | Rotate/zoom the full P(reversal) landscape — Δ price × time × probability |
| **Heatmap** | Top-down view with contour lines; hover for exact values |
| **Time slices** | P(reversal) vs Δ price at 30 / 60 / 90 / 120 / 150 / 180 / 240 s remaining |
| **Dataset Info** | Training period, sample counts, filter settings, kernel config |

Reading the charts:
- **Green** = low reversal risk (current trend likely holds)
- **Red** = high reversal risk (reversal likely before window ends)
- **Dashed line** = unconditional base rate across all windows

---

## Performance

| Phase | Time |
|-------|------|
| First load (grid build) | ~5 seconds |
| Subsequent queries | < 0.1 ms (O(1) grid lookup) |
| Memory | ~50 MB for full dataset |

---

## CLI Reference

### `build-reversal-dataset`

```bash
poetry run build-reversal-dataset [OPTIONS]

Options:
  --hours INT               Hours of 1-second history to download [default: 10000]
  --output PATH             Output parquet path [default: data/crypto/BTC/reversal_dataset.parquet]
  --filter-market-hours     Keep only NYSE-hours windows (Mon–Fri 09:30–16:00 ET,
                            excl. US Federal holidays)
```

### `visualize-reversal`

```bash
poetry run visualize-reversal [OPTIONS]

Options:
  -o, --output PATH         Output HTML file [default: output/reversal_model_viz.html]
  --dataset PATH            reversal_dataset.parquet path (model default if omitted)
  --filter-market-hours     Mark dataset as NYSE-filtered in the Dataset Info tab
  --hours INT               Hours used when building dataset (for display) [default: 10000]
  --delta-bins INT          Grid resolution on Δ price axis [default: 200]
  --time-bins INT           Grid resolution on time-remaining axis [default: 60]
  --delta-bw FLOAT          Kernel bandwidth in USD for Δ price [default: 50.0]
  --time-bw FLOAT           Kernel bandwidth in seconds for time axis [default: 30.0]
```

---

## Tuning

```python
model = ReversalModel(
    dataset_path="data/crypto/BTC/reversal_dataset.parquet",
    delta_bw=50.0,       # USD bandwidth — wider = smoother, less sensitive to small moves
    time_bw=30.0,        # seconds bandwidth — wider = smoother across the time axis
    grid_delta_bins=200, # lookup table resolution (higher = finer, slower first load)
    grid_time_bins=60,
)
```

---

## Files

```
src/btc_reversal_model/
├── __init__.py          # public API: ReversalModel, build
├── build_dataset.py     # downloads 1s data, builds reversal_dataset.parquet
├── reversal_model.py    # ReversalModel — fast O(1) probability query
├── visualize.py         # interactive Plotly HTML generator (4 tabs)
└── README.md            # this file

data/crypto/BTC/
├── btc_1s.parquet           # cached 1-second price series (auto-downloaded)
└── reversal_dataset.parquet # labeled (delta, remaining, flip) dataset

output/
└── reversal_model_viz.html  # generated visualization (open in browser)
```
