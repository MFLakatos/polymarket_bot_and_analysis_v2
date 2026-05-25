"""Polymarket CLOB API client — markets and trades."""
from __future__ import annotations
from datetime import datetime
from typing import Any, Iterable, Iterator, Optional

from polymarket_graph.adapters.extraction.base import BaseApiClient
from polymarket_graph.adapters.extraction.gamma_api import _parse_market, _parse_trade
from polymarket_graph.domain.entities import Market, Trade
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)
_CLOB_CURSOR_DONE = {"LTE=", "", "0", None}


class ClobApiClient(BaseApiClient):
    """Live/historical source using the Polymarket CLOB API."""

    TRADES_PATH = "/trades"
    MARKETS_PATH = "/markets"

    def __init__(self, *args: Any, batch_size: int = 500, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.batch_size = batch_size

    def iter_markets(self) -> Iterator[Market]:
        payload = self._get(self.MARKETS_PATH, params={})
        rows = payload if isinstance(payload, list) else payload.get("data", [])
        for row in rows or []:
            market = _parse_market(row)
            if market is not None:
                yield market

    def iter_trades(self, since: Optional[datetime] = None, until: Optional[datetime] = None,
                    market_ids: Optional[Iterable[str]] = None) -> Iterator[Trade]:
        import requests as _req
        params: dict[str, Any] = {"limit": self.batch_size}
        if since:
            params["after"] = int(since.timestamp())
        if until:
            params["before"] = int(until.timestamp())

        next_cursor: Optional[str] = None
        while True:
            if next_cursor:
                params["next_cursor"] = next_cursor
            try:
                payload = self._get(self.TRADES_PATH, params=params)
            except _req.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (401, 403):
                    logger.warning("clob_api.trades.auth_required")
                    return
                raise

            if isinstance(payload, list):
                rows = payload
                new_cursor = None
            else:
                rows = payload.get("data", [])
                new_cursor = payload.get("next_cursor") or payload.get("cursor")

            if not rows:
                return
            for row in rows:
                trade = _parse_trade(row)
                if trade is not None:
                    yield trade

            if new_cursor in _CLOB_CURSOR_DONE or new_cursor == next_cursor:
                return
            next_cursor = new_cursor
