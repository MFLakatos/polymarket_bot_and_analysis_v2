"""Domain business rules: PnL, win rate, wallet feature aggregation.

Pure functions — no I/O. Operate on Trade entities and market lookups.
"""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timedelta
from statistics import mean
from typing import Iterable, Mapping, Optional

from polymarket_graph.domain.entities import Market, Trade, TradeSide


@dataclass(frozen=True)
class WalletFeatures:
    """Behavioral feature vector for a single wallet."""

    wallet_id: str
    total_trades: int
    total_volume: float
    avg_trade_size: float
    pnl: float
    win_rate: float
    early_participation_score: float
    market_diversity: int
    avg_holding_time_hours: float
    first_seen: Optional[datetime]
    last_seen: Optional[datetime]


def compute_pnl(trades: Iterable[Trade], markets: Mapping[str, Market]) -> float:
    """Realized + unrealized PnL across a wallet's trades."""
    last_price: dict[tuple[str, Optional[str]], float] = {}
    settled_pnl = 0.0
    open_position: dict[tuple[str, Optional[str]], float] = defaultdict(float)
    avg_cost: dict[tuple[str, Optional[str]], float] = defaultdict(float)

    for t in sorted(trades, key=lambda x: x.timestamp):
        key = (t.market_id, t.outcome_id)
        last_price[key] = t.price
        signed = t.size if t.side == TradeSide.BUY else -t.size
        prev_qty = open_position[key]
        new_qty = prev_qty + signed

        if t.side == TradeSide.BUY:
            total_cost = avg_cost[key] * prev_qty + t.price * t.size
            avg_cost[key] = total_cost / new_qty if new_qty else 0.0
        else:
            settled_pnl += (t.price - avg_cost[key]) * t.size

        open_position[key] = new_qty

    unrealized = 0.0
    for key, qty in open_position.items():
        if qty == 0:
            continue
        market_id, outcome_id = key
        market = markets.get(market_id)
        if market and market.resolved and outcome_id is not None:
            settle_price = 1.0 if outcome_id == market.winning_outcome_id else 0.0
        else:
            settle_price = last_price.get(key, avg_cost[key])
        unrealized += (settle_price - avg_cost[key]) * qty

    return settled_pnl + unrealized


def compute_win_rate(trades: Iterable[Trade], markets: Mapping[str, Market]) -> float:
    """Win rate = profitable positions / total resolved positions."""
    by_key: dict[tuple[str, Optional[str]], list[Trade]] = defaultdict(list)
    for t in trades:
        by_key[(t.market_id, t.outcome_id)].append(t)

    wins = 0
    resolved_count = 0
    for (market_id, outcome_id), group in by_key.items():
        market = markets.get(market_id)
        if not market or not market.resolved or outcome_id is None:
            continue
        resolved_count += 1
        settle_price = 1.0 if outcome_id == market.winning_outcome_id else 0.0

        qty = 0.0
        avg_cost = 0.0
        realized = 0.0
        for t in sorted(group, key=lambda x: x.timestamp):
            if t.side == TradeSide.BUY:
                total_cost = avg_cost * qty + t.price * t.size
                qty += t.size
                avg_cost = total_cost / qty if qty else 0.0
            else:
                realized += (t.price - avg_cost) * t.size
                qty = max(0.0, qty - t.size)

        unrealized = (settle_price - avg_cost) * qty if qty > 0 else 0.0
        if realized + unrealized > 0:
            wins += 1

    return wins / resolved_count if resolved_count else 0.0


def compute_wallet_features(
    wallet_id: str,
    trades: list[Trade],
    markets: Mapping[str, Market],
    *,
    early_window_minutes: int = 60,
) -> WalletFeatures:
    """Compute the full feature vector for one wallet."""
    if not trades:
        return WalletFeatures(
            wallet_id=wallet_id, total_trades=0, total_volume=0.0,
            avg_trade_size=0.0, pnl=0.0, win_rate=0.0,
            early_participation_score=0.0, market_diversity=0,
            avg_holding_time_hours=0.0, first_seen=None, last_seen=None,
        )

    sorted_trades = sorted(trades, key=lambda t: t.timestamp)
    total_volume = sum(t.notional for t in trades)
    avg_trade_size = total_volume / len(trades)
    pnl = compute_pnl(trades, markets)
    win_rate = compute_win_rate(trades, markets)
    market_diversity = len({t.market_id for t in trades})
    early_score = _early_participation_score(sorted_trades, markets, timedelta(minutes=early_window_minutes))
    holding_hours = _avg_holding_time(sorted_trades)

    return WalletFeatures(
        wallet_id=wallet_id,
        total_trades=len(trades),
        total_volume=total_volume,
        avg_trade_size=avg_trade_size,
        pnl=pnl,
        win_rate=win_rate,
        early_participation_score=early_score,
        market_diversity=market_diversity,
        avg_holding_time_hours=holding_hours,
        first_seen=sorted_trades[0].timestamp,
        last_seen=sorted_trades[-1].timestamp,
    )


def _early_participation_score(
    trades: list[Trade],
    markets: Mapping[str, Market],
    window: timedelta,
) -> float:
    """Fraction of trades placed within `window` of the wallet's first trade per market."""
    by_market: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_market[t.market_id].append(t)

    early_count = 0
    total_count = 0
    for market_id, market_trades in by_market.items():
        if not market_trades:
            continue
        first_ts = min(t.timestamp for t in market_trades)
        cutoff = first_ts + window
        for t in market_trades:
            total_count += 1
            if t.timestamp <= cutoff:
                early_count += 1

    return early_count / total_count if total_count else 0.0


def _avg_holding_time(trades: list[Trade]) -> float:
    """Average hours between first BUY and last SELL per market position."""
    by_key: dict[tuple[str, Optional[str]], list[Trade]] = defaultdict(list)
    for t in trades:
        by_key[(t.market_id, t.outcome_id)].append(t)

    holding_times = []
    for group in by_key.values():
        buys = [t for t in group if t.side == TradeSide.BUY]
        sells = [t for t in group if t.side == TradeSide.SELL]
        if buys and sells:
            first_buy = min(t.timestamp for t in buys)
            last_sell = max(t.timestamp for t in sells)
            if last_sell > first_buy:
                holding_times.append((last_sell - first_buy).total_seconds() / 3600)

    return mean(holding_times) if holding_times else 0.0
