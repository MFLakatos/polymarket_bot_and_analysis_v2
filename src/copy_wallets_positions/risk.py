"""Risk management — drawdown tracking, position limits, sizing."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone, timedelta
from typing import Optional

from copy_wallets_positions.config import CopyTradingConfig
from copy_wallets_positions.monitor import TradeEvent


@dataclass
class DailyStats:
    date: str
    starting_balance: float = 0.0
    realized_pnl: float = 0.0
    trades_executed: int = 0
    trades_skipped: int = 0
    max_drawdown_hit: bool = False

    @property
    def drawdown_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return (-self.realized_pnl / self.starting_balance) * 100 if self.realized_pnl < 0 else 0.0

    @property
    def pnl_pct(self) -> float:
        if self.starting_balance <= 0:
            return 0.0
        return (self.realized_pnl / self.starting_balance) * 100


@dataclass
class Position:
    token_id: str
    market_id: str
    market_title: str
    outcome: str
    side: str
    entry_price: float
    size: float
    usdc_invested: float
    opened_at: datetime
    source_wallet: str

    @property
    def age_minutes(self) -> float:
        return (datetime.now(timezone.utc) - self.opened_at).total_seconds() / 60


class RiskManager:
    """Enforces risk limits and computes position sizing."""

    def __init__(self, config: CopyTradingConfig, initial_balance: float):
        self.config = config
        self._initial_balance = initial_balance
        self._current_balance = initial_balance
        self._positions: dict[str, Position] = {}
        self._daily_stats = self._new_daily_stats(initial_balance)
        self._last_loss_time: Optional[datetime] = None

    @property
    def positions(self) -> dict[str, Position]:
        return self._positions

    @property
    def daily_stats(self) -> DailyStats:
        return self._daily_stats

    @property
    def current_balance(self) -> float:
        return self._current_balance

    def update_balance(self, balance: float) -> None:
        self._current_balance = balance

    def can_trade(self) -> tuple[bool, str]:
        self._maybe_reset_daily_stats()
        if self._daily_stats.max_drawdown_hit:
            return False, f"Daily drawdown limit hit ({self.config.risk.max_daily_drawdown_pct}%)"
        if self._daily_stats.drawdown_pct >= self.config.risk.max_daily_drawdown_pct:
            self._daily_stats.max_drawdown_hit = True
            return False, f"Daily drawdown {self._daily_stats.drawdown_pct:.1f}% exceeded limit"
        if len(self._positions) >= self.config.risk.max_open_positions:
            return False, f"Max open positions reached ({self.config.risk.max_open_positions})"
        if self._last_loss_time:
            elapsed = (datetime.now(timezone.utc) - self._last_loss_time).total_seconds()
            if elapsed < self.config.risk.cooldown_after_loss_seconds:
                remaining = int(self.config.risk.cooldown_after_loss_seconds - elapsed)
                return False, f"Cooldown after loss ({remaining}s remaining)"
        return True, ""

    def should_copy_trade(self, trade: TradeEvent, current_price: float | None) -> tuple[bool, str]:
        can, reason = self.can_trade()
        if not can:
            return False, reason
        if current_price is not None and trade.price > 0:
            price_move = abs(current_price - trade.price) / trade.price * 100
            if price_move > self.config.risk.skip_if_price_moved_pct:
                return False, f"Price moved {price_move:.1f}% since target traded (limit: {self.config.risk.skip_if_price_moved_pct}%)"
        if self._current_balance > 0:
            market_exposure = sum(
                p.usdc_invested for p in self._positions.values() if p.market_id == trade.market_id
            )
            max_exposure = self._current_balance * self.config.risk.max_exposure_per_market_pct / 100
            if market_exposure >= max_exposure:
                return False, f"Max market exposure reached ({self.config.risk.max_exposure_per_market_pct}%)"
        return True, "OK"

    def compute_trade_size(self, trade: TradeEvent) -> float:
        amount = self.config.risk.max_trade_amount_usdc
        if self._current_balance > 0:
            amount = min(amount, self._current_balance * 0.95)
        return max(0.0, amount) if amount >= self.config.risk.min_trade_amount_usdc else 0.0

    def compute_trade_shares(self, trade: TradeEvent) -> float:
        whale_shares = trade.size
        shares = int(whale_shares * self.config.risk.share_fraction)
        if shares <= 0:
            return 0.0
        estimated_usdc = shares * trade.price if trade.price > 0 else shares
        if estimated_usdc < self.config.risk.min_trade_amount_usdc:
            return 0.0
        max_usdc = self.config.risk.max_trade_amount_usdc
        if estimated_usdc > max_usdc and trade.price > 0:
            shares = int(max_usdc / trade.price)
        return float(shares)

    def record_trade(self, trade_event: TradeEvent, filled_price: float, filled_size: float, usdc_amount: float) -> None:
        self._maybe_reset_daily_stats()
        self._daily_stats.trades_executed += 1
        if trade_event.side == "BUY":
            existing = self._positions.get(trade_event.token_id)
            if existing:
                total_size = existing.size + filled_size
                existing.entry_price = (existing.entry_price * existing.size + filled_price * filled_size) / total_size
                existing.size = total_size
                existing.usdc_invested += usdc_amount
            else:
                self._positions[trade_event.token_id] = Position(
                    token_id=trade_event.token_id, market_id=trade_event.market_id,
                    market_title=trade_event.market_title, outcome=trade_event.outcome,
                    side="BUY", entry_price=filled_price, size=filled_size,
                    usdc_invested=usdc_amount, opened_at=datetime.now(timezone.utc),
                    source_wallet=trade_event.wallet.address,
                )
        else:
            existing = self._positions.get(trade_event.token_id)
            if existing:
                pnl = (filled_price - existing.entry_price) * filled_size
                self._daily_stats.realized_pnl += pnl
                self._current_balance += pnl
                if filled_size >= existing.size:
                    del self._positions[trade_event.token_id]
                else:
                    existing.size -= filled_size
                    existing.usdc_invested -= usdc_amount
                if pnl < 0:
                    self._last_loss_time = datetime.now(timezone.utc)

    def record_skip(self) -> None:
        self._daily_stats.trades_skipped += 1

    def _maybe_reset_daily_stats(self) -> None:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        if self._daily_stats.date != today:
            self._daily_stats = self._new_daily_stats(self._current_balance)

    def _new_daily_stats(self, balance: float) -> DailyStats:
        return DailyStats(date=datetime.now(timezone.utc).strftime("%Y-%m-%d"), starting_balance=balance)
