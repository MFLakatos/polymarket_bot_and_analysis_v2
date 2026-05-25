"""Hybrid source: Data API for backfill + CLOB API for incremental tail."""
from __future__ import annotations
from datetime import datetime
from typing import Iterable, Iterator, Optional

from polymarket_graph.adapters.extraction.base import TradeSource
from polymarket_graph.domain.entities import Market, Trade
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


class HybridTradeSource:
    """Composes a historical source with a live source, deduplicates on dedup_key."""

    def __init__(self, historical: TradeSource, live: TradeSource) -> None:
        self._historical = historical
        self._live = live

    def iter_trades(self, since: Optional[datetime] = None, until: Optional[datetime] = None,
                    market_ids: Optional[Iterable[str]] = None) -> Iterator[Trade]:
        seen: set = set()
        for source_name, source in (("historical", self._historical), ("live", self._live)):
            count = 0
            for t in source.iter_trades(since=since, until=until, market_ids=market_ids):
                key = t.dedup_key
                if key in seen:
                    continue
                seen.add(key)
                count += 1
                yield t
            logger.info("hybrid.source.done", source=source_name, trades=count)

    def iter_markets(self) -> Iterator[Market]:
        seen_ids: set[str] = set()
        for source in (self._historical, self._live):
            for m in source.iter_markets():
                if m.id in seen_ids:
                    continue
                seen_ids.add(m.id)
                yield m
