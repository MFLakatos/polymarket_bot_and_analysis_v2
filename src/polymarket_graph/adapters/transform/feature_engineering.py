"""Feature engineering: convert per-trade events into a wallet feature DataFrame."""
from __future__ import annotations
from collections import defaultdict
from dataclasses import asdict
from typing import Iterable, Mapping
import pandas as pd
from polymarket_graph.domain.entities import Market, Trade
from polymarket_graph.domain.rules import compute_wallet_features

WALLET_FEATURE_COLUMNS: tuple[str, ...] = (
    "wallet_id", "total_trades", "total_volume", "avg_trade_size",
    "pnl", "win_rate", "early_participation_score", "market_diversity",
    "avg_holding_time_hours", "first_seen", "last_seen",
)

def build_wallet_feature_frame(
    trades: Iterable[Trade],
    markets: Iterable[Market] | Mapping[str, Market],
    *,
    min_trades_per_wallet: int = 5,
    early_window_minutes: int = 60,
) -> pd.DataFrame:
    market_map: dict[str, Market]
    if isinstance(markets, Mapping):
        market_map = dict(markets)
    else:
        market_map = {m.id: m for m in markets}
    by_wallet: dict[str, list[Trade]] = defaultdict(list)
    for t in trades:
        by_wallet[t.wallet_id].append(t)
    rows = []
    for wallet_id, wallet_trades in by_wallet.items():
        if len(wallet_trades) < min_trades_per_wallet:
            continue
        feats = compute_wallet_features(wallet_id, wallet_trades, market_map, early_window_minutes=early_window_minutes)
        rows.append(asdict(feats))
    if not rows:
        return pd.DataFrame(columns=list(WALLET_FEATURE_COLUMNS))
    return pd.DataFrame(rows)[list(WALLET_FEATURE_COLUMNS)]
