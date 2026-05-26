# BaseBot

Abstract base class for all Polymarket trading bots.

## Creating a new bot

```python
from base_bot import BaseBot, BotConfig

class MyBot(BaseBot):
    def on_window_open(self, open_price, boundary):
        self._open = open_price

    def on_tick(self, current_price, delta, remaining):
        direction = "UP" if delta >= 0 else "DOWN"
        prob = 0.15  # your signal here
        # maybe_trade handles all gates automatically:
        #   MAX_ENTRY_PRICE → ATRFilter → TimeFilter → ClobConnection
        self.maybe_trade(direction, current_price, prob, remaining,
                         shares_to_add=1, tier_label="my_signal")

    def on_window_close(self, close_price, winner, pnl):
        print(f"Window closed: {winner}  P&L={pnl:+.3f}")

cfg = BotConfig(
    name="my_bot",
    coin="BTC",
    mode="simulation",          # "simulation" or "live"
    max_entry_price=0.72,       # skip if share price > $0.72
    max_natr=2.0,               # skip if NATR% > 2.0
    time_filter={"exclude_weekends": True},
    log_dir="output/logs",
)
bot = MyBot(cfg)
bot.run()
```

## Built-in gates (checked in maybe_trade)

1. **MAX_ENTRY_PRICE** — `if est_share_price > config.max_entry_price: skip`
2. **ATR gate** — `if NATR% > config.max_natr: skip`
3. **TimeFilter gate** — weekend / NYSE / session checks

All skips are logged to the CSV with the reason.

## BotConfig from YAML

```python
cfg = BotConfig.from_yaml("config/btc_5m_bot.yaml")
```
