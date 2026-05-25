"""Trade/market normalization and deduplication."""
from __future__ import annotations
from datetime import timezone
from typing import Iterable, Iterator
from polymarket_graph.domain.entities import Trade

def normalize_trades(trades: Iterable[Trade]) -> Iterator[Trade]:
    """Lowercase wallet IDs, force UTC timestamps, strip whitespace from IDs."""
    for t in trades:
        ts = t.timestamp
        if ts.tzinfo is None:
            ts = ts.replace(tzinfo=timezone.utc)
        else:
            ts = ts.astimezone(timezone.utc)
        yield t.model_copy(update={
            "wallet_id": t.wallet_id.strip().lower(),
            "market_id": t.market_id.strip(),
            "outcome_id": t.outcome_id.strip() if t.outcome_id else None,
            "trade_id": t.trade_id.strip(),
            "timestamp": ts,
        })

def deduplicate_trades(trades: Iterable[Trade]) -> Iterator[Trade]:
    """Yield trades with unique dedup_key; preserves first occurrence."""
    seen: set = set()
    for t in trades:
        key = t.dedup_key
        if key in seen:
            continue
        seen.add(key)
        yield t
