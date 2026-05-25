"""Polymarket Gamma + Data API client.

- Markets from Gamma API (https://gamma-api.polymarket.com)
- Trades from Data API (https://data-api.polymarket.com) — no auth required
"""
from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Iterable, Iterator, Optional

from polymarket_graph.adapters.extraction.base import BaseApiClient
from polymarket_graph.domain.entities import Market, Outcome, Trade, TradeSide
from polymarket_graph.infrastructure.logging import get_logger

logger = get_logger(__name__)


class GammaApiClient(BaseApiClient):
    """Markets from Gamma API, trades from Data API."""

    MARKETS_PATH = "/markets"
    TRADES_PATH = "/trades"
    ACTIVITY_PATH = "/activity"

    def __init__(self, *args: Any, batch_size: int = 100,
                 trades_base_url: str = "https://data-api.polymarket.com", **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.batch_size = batch_size
        self._trades_base_url = trades_base_url.rstrip("/")
        self._discovered_markets: dict[str, dict] = {}

    def iter_trades(self, since: Optional[datetime] = None, until: Optional[datetime] = None,
                    market_ids: Optional[Iterable[str]] = None) -> Iterator[Trade]:
        import requests as _req
        seen_tx: set[str] = set()
        wallets_discovered: set[str] = set()
        params: dict[str, Any] = {"limit": self.batch_size}
        offset = 0
        phase1_count = 0
        while True:
            params["offset"] = offset
            try:
                payload = self._get(self.TRADES_PATH, params=params, base_url=self._trades_base_url)
            except _req.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (400, 422):
                    break
                raise
            rows = payload if isinstance(payload, list) else payload.get("data", [])
            if not rows:
                break
            for row in rows:
                trade = _parse_trade(row)
                if trade is not None:
                    wallets_discovered.add(trade.wallet_id)
                    if since and trade.timestamp < since:
                        continue
                    if until and trade.timestamp > until:
                        continue
                    tx_key = trade.tx_hash or trade.trade_id
                    if tx_key and tx_key not in seen_tx:
                        seen_tx.add(tx_key)
                        phase1_count += 1
                        yield trade
            if len(rows) < self.batch_size:
                break
            offset += len(rows)

        MAX_WALLETS_PHASE2 = 100
        wallet_list = list(wallets_discovered)[:MAX_WALLETS_PHASE2]
        logger.info("gamma_api.trades.phase2_start", wallets=len(wallet_list))
        phase2_count = 0
        for wallet_id in wallet_list:
            for trade in self._iter_wallet_activity(wallet_id, since=since, until=until):
                tx_key = trade.tx_hash or trade.trade_id
                if tx_key and tx_key in seen_tx:
                    continue
                seen_tx.add(tx_key)
                phase2_count += 1
                yield trade
        logger.info("gamma_api.trades.done", phase1=phase1_count, phase2=phase2_count)

    def _iter_wallet_activity(self, wallet_id: str, since: Optional[datetime] = None,
                               until: Optional[datetime] = None) -> Iterator[Trade]:
        import requests as _req
        params: dict[str, Any] = {"user": wallet_id, "limit": self.batch_size}
        offset = 0
        while True:
            params["offset"] = offset
            try:
                payload = self._get(self.ACTIVITY_PATH, params=params, base_url=self._trades_base_url)
            except _req.HTTPError as exc:
                if exc.response is not None and exc.response.status_code in (400, 422):
                    return
                raise
            rows = payload if isinstance(payload, list) else payload.get("data", [])
            if not rows:
                return
            for row in rows:
                trade = _parse_trade(row)
                if trade is not None:
                    if since and trade.timestamp < since:
                        continue
                    if until and trade.timestamp > until:
                        continue
                    yield trade
            if len(rows) < self.batch_size:
                return
            offset += len(rows)

    def iter_markets(self) -> Iterator[Market]:
        import requests as _req
        seen_ids: set[str] = set()
        for extra in ({"active": "true"}, {"closed": "true"}):
            params: dict[str, Any] = {"limit": self.batch_size, "order": "id", "ascending": "true", **extra}
            offset = 0
            while True:
                params["offset"] = offset
                try:
                    payload = self._get(self.MARKETS_PATH, params=params)
                except _req.HTTPError as exc:
                    if exc.response is not None and exc.response.status_code == 422:
                        break
                    raise
                rows = payload if isinstance(payload, list) else payload.get("data", [])
                if not rows:
                    break
                for row in rows:
                    market = _parse_market(row)
                    if market is not None and market.id not in seen_ids:
                        seen_ids.add(market.id)
                        yield market
                if len(rows) < self.batch_size:
                    break
                offset += self.batch_size

    def fetch_markets_by_ids(self, condition_ids: Iterable[str]) -> Iterator[Market]:
        import requests as _req
        for cid in condition_ids:
            try:
                payload = self._get(self.MARKETS_PATH, params={"condition_id": cid})
            except Exception:
                continue
            rows = payload if isinstance(payload, list) else payload.get("data", [])
            for row in rows:
                market = _parse_market(row)
                if market is not None:
                    yield market


def _parse_trade(row: dict) -> Optional[Trade]:
    try:
        ts_raw = row.get("timestamp") or row.get("ts") or row.get("created_at")
        if isinstance(ts_raw, (int, float)):
            ts = datetime.fromtimestamp(float(ts_raw), tz=timezone.utc)
        elif isinstance(ts_raw, str):
            ts = datetime.fromisoformat(ts_raw.replace("Z", "+00:00"))
        else:
            return None

        side_raw = str(row.get("side", "")).upper()
        side = TradeSide.BUY if side_raw in {"BUY", "B", "BID"} else TradeSide.SELL

        wallet = (row.get("proxyWallet") or row.get("maker_address") or row.get("taker_address")
                  or row.get("wallet") or row.get("maker") or row.get("user"))
        market = (row.get("conditionId") or row.get("condition_id") or row.get("market") or row.get("market_id"))
        outcome = (row.get("asset") or row.get("asset_id") or row.get("outcome") or row.get("outcome_id"))

        size = float(row.get("size") or row.get("amount") or 0.0)
        if size <= 0.0:
            return None   # ← skip: redemptions, splits, non-fill events
        return Trade(
            trade_id=str(row.get("trade_id") or row.get("id") or row.get("transactionHash") or row.get("tx_hash") or ""),
            wallet_id=str(wallet or ""),
            market_id=str(market or ""),
            outcome_id=str(outcome) if outcome else None,
            side=side,
            price=float(row.get("price", 0.0)),
            size=size,
            timestamp=ts,
            tx_hash=row.get("transactionHash") or row.get("tx_hash"),
        )
    except Exception as exc:
        logger.warning("gamma_api.parse_trade.failed", error=str(exc))
        return None


def _parse_market(row: dict) -> Optional[Market]:
    try:
        import json as _json
        outcomes_raw = row.get("outcomes") or row.get("tokens") or []
        market_id = str(row.get("conditionId") or row.get("condition_id") or row.get("id") or row.get("market_id"))

        if isinstance(outcomes_raw, str):
            try:
                outcomes_raw = _json.loads(outcomes_raw)
            except Exception:
                outcomes_raw = []

        outcome_prices_raw = row.get("outcomePrices") or "[]"
        if isinstance(outcome_prices_raw, str):
            try:
                outcome_prices = _json.loads(outcome_prices_raw)
            except Exception:
                outcome_prices = []
        else:
            outcome_prices = outcome_prices_raw

        clob_ids_raw = row.get("clobTokenIds") or "[]"
        if isinstance(clob_ids_raw, str):
            try:
                clob_ids = _json.loads(clob_ids_raw)
            except Exception:
                clob_ids = []
        else:
            clob_ids = clob_ids_raw

        resolved = bool(row.get("resolved") or row.get("closed") or row.get("resolutionTime"))
        outcomes: list[Outcome] = []

        if isinstance(outcomes_raw, list) and outcomes_raw:
            for i, o in enumerate(outcomes_raw):
                if isinstance(o, dict):
                    o_id = str(o.get("id") or o.get("conditionId") or o.get("token_id") or f"{market_id}-{i}")
                    o_name = str(o.get("name") or o.get("title") or o.get("outcome") or o_id)
                    is_winner = o.get("winner") or o.get("is_winner")
                    token_id = str(o.get("tokenId") or o.get("token_id") or o_id)
                elif isinstance(o, str):
                    clob_id = clob_ids[i] if i < len(clob_ids) else f"{market_id}-{i}"
                    o_id = str(clob_id)
                    o_name = o
                    price = float(outcome_prices[i]) if i < len(outcome_prices) else 0.0
                    is_winner = resolved and price >= 0.99
                    token_id = str(clob_id)
                else:
                    continue
                outcomes.append(Outcome(id=o_id, market_id=market_id, name=o_name,
                                        token_id=token_id, is_winner=bool(is_winner) if is_winner is not None else None))

        created_at = None
        closed_at = None
        for field in ("createdAt", "created_at", "startDate"):
            v = row.get(field)
            if v:
                try:
                    created_at = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                    break
                except Exception:
                    pass
        for field in ("endDate", "closed_at", "resolutionTime"):
            v = row.get(field)
            if v:
                try:
                    closed_at = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
                    break
                except Exception:
                    pass

        return Market(
            id=market_id,
            question=str(row.get("question") or row.get("title") or row.get("description") or market_id),
            slug=row.get("slug") or row.get("marketSlug"),
            category=row.get("category") or row.get("groupItemTitle"),
            created_at=created_at,
            closed_at=closed_at,
            resolved=resolved,
            outcomes=tuple(outcomes),
        )
    except Exception as exc:
        logger.warning("gamma_api.parse_market.failed", error=str(exc))
        return None
