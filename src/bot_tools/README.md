# bot_tools

Shared utilities for all Polymarket trading bots. Each sub-package is independent.

| Module | Purpose |
|--------|---------|
| `time_utils/` | 5-minute window manager + session/timezone filter |
| `clob_connection/` | Unified simulation and live CLOB executor |
| `atr_filter/` | Normalized ATR gate (skip volatile conditions) |
| `logger/` | Structured per-decision CSV logger |

## Quick usage

```python
from bot_tools.time_utils import WindowManager, TimeFilter
from bot_tools.clob_connection import ClobConnection
from bot_tools.atr_filter import ATRFilter
from bot_tools.logger import TradeLogger
```

See each sub-folder's README for details.
