"""Monitor target wallets for new trades via Data API."""
from __future__ import annotations

import time
from datetime import datetime, timezone
from typing import Any

import requests

from copy_wallets_positions.config import CopyTradingConfig, WalletTarget


class TradeEvent:
    """A detected trade from a target wallet."""

    def __init__(
        self,
        wallet: WalletTarget,
        trade_id: str,
        market_id: str,
        market_title: str,
        token_id: str,
        outcome: str,
        side: str,
        price: float,
        size: float,
        usdc_amount: float,
        timestamp: datetime,
        raw: dict[str, Any],
    ):
        self.wallet = wallet
        self.trade_id = trade_id
        self.market_id = market_id
        self.market_title = market_title
        self.token_id = token_id
        self.outcome = outcome
        self.side = side
        self.price = price
        self.size = size
        self.usdc_amount = usdc_amount
        self.timestamp = timestamp
        self.raw = raw

    def __repr__(self) -> str:
        return (
            f"TradeEvent({self.wallet.label or self.wallet.address[:10]}... "
            f"{self.side} {self.outcome} @{self.price:.3f} ${self.usdc_amount:.2f})"
        )


class WalletMonitor:
    """Polls Data API for new activity from target wallets."""

    def __init__(self, config: CopyTradingConfig):
        self.config = config
        self._session = requests.Session()
        self._session.headers.update({"User-Agent": "PolymarketCopyBot/1.0"})
        self._base_url = config.monitor.data_api_base_url.rstrip("/")
        self._gamma_url = config.monitor.gamma_api_base_url.rstrip("/")
        self._last_seen: dict[str, str] = {}
        self._last_timestamps: dict[str, datetime] = {}
        self._market_cache: dict[str, dict] = {}

    def initialize(self, lookback_minutes: int) -> list[TradeEvent]:
        """Fetch recent activity to set baseline. Returns recent trades (not copied)."""
        recent: list[TradeEvent] = []
        for wallet in self.config.wallets:
            trades = self._fetch_wallet_activity(wallet, limit=20)
            if trades:
                self._last_seen[wallet.address] = trades[0].trade_id
                self._last_timestamps[wallet.address] = trades[0].timestamp
                recent.extend(trades[:5])
        return recent

    def poll(self) -> list[TradeEvent]:
        """Check all wallets for new trades since last poll."""
        new_trades: list[TradeEvent] = []
        for wallet in self.config.wallets:
            trades = self._fetch_wallet_activity(wallet, limit=50)
            if not trades:
                continue
            last_seen_id = self._last_seen.get(wallet.address)
            last_ts = self._last_timestamps.get(wallet.address)
            for trade in trades:
                if trade.trade_id == last_seen_id:
                    break
                if last_ts and trade.timestamp <= last_ts:
                    break
                new_trades.append(trade)
            if trades:
                self._last_seen[wallet.address] = trades[0].trade_id
                self._last_timestamps[wallet.address] = trades[0].timestamp
        return new_trades

    def get_wallet_positions(self, wallet_address: str) -> list[dict[str, Any]]:
        try:
            resp = self._session.get(
                f"{self._gamma_url}/positions",
                params={"user": wallet_address.lower(), "limit": 100},
                timeout=15,
            )
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else []
        except Exception:
            pass
        return []

    def get_market_info(self, condition_id: str) -> dict[str, Any]:
        if condition_id in self._market_cache:
            return self._market_cache[condition_id]
        try:
            resp = self._session.get(
                f"{self._gamma_url}/markets",
                params={"condition_id": condition_id, "limit": 1},
                timeout=15,
            )
            if resp.status_code == 200:
                markets = resp.json()
                if markets:
                    self._market_cache[condition_id] = markets[0]
                    return markets[0]
        except Exception:
            pass
        return {}

    def get_current_price(self, token_id: str) -> float | None:
        try:
            resp = self._session.get(
                "https://clob.polymarket.com/midpoint",
                params={"token_id": token_id},
                timeout=10,
            )
            if resp.status_code == 200:
                return float(resp.json().get("mid", 0))
        except Exception:
            pass
        return None

    def _fetch_wallet_activity(self, wallet: WalletTarget, limit: int = 50) -> list[TradeEvent]:
        try:
            resp = self._session.get(
                f"{self._base_url}/activity",
                params={"user": wallet.address.lower(), "limit": limit, "offset": 0},
                timeout=15,
            )
            if resp.status_code != 200:
                return []
            records = resp.json()
            if not isinstance(records, list):
                return []
            trades: list[TradeEvent] = []
            for r in records:
                trade = self._parse_activity_record(r, wallet)
                if trade:
                    trades.append(trade)
            return trades
        except Exception:
            return []

    def _parse_activity_record(self, record: dict, wallet: WalletTarget) -> TradeEvent | None:
        try:
            trade_id = record.get("id") or record.get("transactionHash", "")
            condition_id = record.get("conditionId") or record.get("condition_id", "")
            title = record.get("title") or record.get("market", "Unknown Market")
            asset = record.get("asset") or record.get("outcome_id", "")
            outcome_name = record.get("outcome", record.get("outcomeIndex", "?"))

            side_raw = record.get("side") or record.get("type", "")
            if side_raw.upper() in ("BUY", "BOUGHT"):
                side = "BUY"
            elif side_raw.upper() in ("SELL", "SOLD"):
                side = "SELL"
            else:
                type_field = record.get("type", "").upper()
                if "BUY" in type_field or "BOUGHT" in type_field:
                    side = "BUY"
                elif "SELL" in type_field or "SOLD" in type_field:
                    side = "SELL"
                else:
                    return None

            price = float(record.get("price", 0))
            size = float(record.get("size", 0))
            usdc_amount = float(record.get("usdcSize", 0)) or (price * size)

            ts_raw = record.get("timestamp") or record.get("createdAt") or record.get("created_at", "")
            if isinstance(ts_raw, (int, float)):
                timestamp = datetime.fromtimestamp(ts_raw, tz=timezone.utc)
            elif isinstance(ts_raw, str) and ts_raw:
                ts_raw = ts_raw.replace("Z", "+00:00")
                timestamp = datetime.fromisoformat(ts_raw)
                if timestamp.tzinfo is None:
                    timestamp = timestamp.replace(tzinfo=timezone.utc)
            else:
                timestamp = datetime.now(timezone.utc)

            return TradeEvent(
                wallet=wallet, trade_id=str(trade_id), market_id=condition_id,
                market_title=title, token_id=asset, outcome=str(outcome_name),
                side=side, price=price, size=size, usdc_amount=usdc_amount,
                timestamp=timestamp, raw=record,
            )
        except (ValueError, KeyError, TypeError):
            return None

    def _rate_limit(self) -> None:
        time.sleep(0.2)
