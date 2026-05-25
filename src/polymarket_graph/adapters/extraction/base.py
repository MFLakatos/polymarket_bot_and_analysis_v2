"""Base HTTP client and TradeSource protocol shared by extraction adapters."""
from __future__ import annotations

import time
from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any, Iterable, Iterator, Optional, Protocol

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from polymarket_graph.domain.entities import Market, Trade
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


class TradeSource(Protocol):
    """Any object that can stream Trade entities for a time window."""

    def iter_trades(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        market_ids: Optional[Iterable[str]] = None,
    ) -> Iterator[Trade]: ...

    def iter_markets(self) -> Iterator[Market]: ...


class BaseApiClient(ABC):
    """Common HTTP plumbing: session, retries, rate limiting."""

    def __init__(
        self,
        base_url: str,
        *,
        rate_limit_per_sec: float = 5.0,
        max_retries: int = 5,
        timeout_seconds: int = 30,
        api_key: str | None = None,
        session: requests.Session | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._rate_limit = max(rate_limit_per_sec, 0.001)
        self._min_interval = 1.0 / self._rate_limit
        self._max_retries = max_retries
        self._timeout = timeout_seconds
        self._session = session or requests.Session()
        if api_key:
            self._session.headers.update({"Authorization": api_key})
        self._last_call = 0.0

    @abstractmethod
    def iter_trades(
        self,
        since: Optional[datetime] = None,
        until: Optional[datetime] = None,
        market_ids: Optional[Iterable[str]] = None,
    ) -> Iterator[Trade]: ...

    @abstractmethod
    def iter_markets(self) -> Iterator[Market]: ...

    def _throttle(self) -> None:
        now = time.monotonic()
        delta = now - self._last_call
        if delta < self._min_interval:
            time.sleep(self._min_interval - delta)
        self._last_call = time.monotonic()

    def _get(self, path: str, params: dict | None = None, *, base_url: str | None = None) -> Any:
        effective_base = (base_url or self.base_url).rstrip("/")

        @retry(
            reraise=True,
            stop=stop_after_attempt(self._max_retries),
            wait=wait_exponential(multiplier=0.5, min=0.5, max=8),
            retry=retry_if_exception_type((requests.ConnectionError, requests.Timeout)),
        )
        def _do() -> Any:
            self._throttle()
            url = f"{effective_base}{path}"
            logger.debug("http.get", url=url, params=params)
            resp = self._session.get(url, params=params, timeout=self._timeout)
            if resp.status_code == 429:
                time.sleep(float(resp.headers.get("Retry-After", "1")))
                resp.raise_for_status()
            resp.raise_for_status()
            return resp.json()

        return _do()
