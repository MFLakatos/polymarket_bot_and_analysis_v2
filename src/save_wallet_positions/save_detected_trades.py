"""Save all detected wallet trades to CSV with timestamped filename."""
import csv
import os
from datetime import datetime, timezone
from typing import Optional

from copy_wallets_positions.monitor import TradeEvent


def get_detected_trades_path(wallet_address: str, base_dir: Optional[str] = None) -> str:
    if base_dir is None:
        base_dir = os.path.dirname(__file__)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"detected_trades_{wallet_address[:8]}_{timestamp}.csv"
    return os.path.join(base_dir, filename)


def save_detected_trade_to_csv(trade: TradeEvent, status: str, reason: str, csv_path: str) -> None:
    """Append detected trade info to CSV. Creates file with header if it doesn't exist."""
    file_exists = os.path.isfile(csv_path)
    now_utc = datetime.now(timezone.utc).isoformat()
    os.makedirs(os.path.dirname(csv_path), exist_ok=True)

    # Calculate up/down price for BTC 5m markets
    up_price = down_price = ""
    try:
        if trade.size and trade.usdc_amount and float(trade.size) != 0:
            price_val = float(trade.usdc_amount) / float(trade.size)
            if str(trade.outcome).lower() == "down":
                down_price = price_val
                up_price = 1 - price_val
            elif str(trade.outcome).lower() == "up":
                up_price = price_val
                down_price = 1 - price_val
    except Exception:
        pass

    with open(csv_path, mode="a", newline="") as f:
        writer = csv.writer(f)
        if not file_exists:
            writer.writerow([
                "side", "outcome", "size", "usdc_amount", "price",
                "up_price", "down_price", "time_open", "time_detected",
                "status", "reason", "market_title", "wallet_address", "token_id",
            ])
        writer.writerow([
            trade.side, trade.outcome, trade.size, trade.usdc_amount, trade.price,
            up_price, down_price, trade.timestamp.isoformat(), now_utc,
            status, reason, trade.market_title, trade.wallet.address, trade.token_id,
        ])
