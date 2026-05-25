"""Save executed wallet positions to CSV with timestamped filename."""
import csv
import os
from datetime import datetime
from typing import Optional

from copy_wallets_positions.monitor import TradeEvent
from copy_wallets_positions.executor import OrderResult


def get_save_path(wallet_address: str, market_title: str, base_dir: Optional[str] = None) -> str:
    if base_dir is None:
        base_dir = os.path.dirname(__file__)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_title = "_".join(market_title.strip().split())[:30]
    filename = f"orders_{wallet_address[:8]}_{safe_title}_{timestamp}.csv"
    return os.path.join(base_dir, filename)


def save_order_to_csv(trade: TradeEvent, result: OrderResult, csv_path: str) -> None:
    """Append executed order info to CSV. Creates file with header if it doesn't exist."""
    file_exists = os.path.isfile(csv_path)
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)
    with open(csv_path, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "wallet_address", "market_title", "token_id", "side", "outcome",
                "filled_size", "filled_amount_usdc", "filled_price", "timestamp", "order_id"
            ])
        writer.writerow([
            trade.wallet.address, trade.market_title, trade.token_id, trade.side, trade.outcome,
            result.filled_size or 0, result.filled_amount_usdc or 0, result.filled_price or 0,
            result.timestamp.isoformat(), result.order_id or "",
        ])
