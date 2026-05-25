"""Order execution via Polymarket CLOB API v2."""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional

from copy_wallets_positions.config import CopyTradingConfig
from copy_wallets_positions.monitor import TradeEvent


@dataclass
class OrderResult:
    success: bool
    order_id: Optional[str] = None
    filled_price: Optional[float] = None
    filled_size: Optional[float] = None
    filled_amount_usdc: Optional[float] = None
    error: Optional[str] = None
    timestamp: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @property
    def summary(self) -> str:
        if self.success:
            return f"FILLED order={self.order_id} @{self.filled_price:.4f} ${self.filled_amount_usdc:.2f}"
        return f"FAILED: {self.error}"


class OrderExecutor:
    """Places orders on Polymarket via CLOB API v2."""

    def __init__(self, config: CopyTradingConfig):
        self.config = config
        self._client: Any = None
        self._initialized = False

    def initialize(self) -> None:
        """Initialize CLOB client with credentials."""
        try:
            from py_clob_client_v2 import ApiCreds, ClobClient
        except ImportError:
            raise ImportError(
                "py_clob_client_v2 is required. Run: poetry install --extras copy-trading"
            )

        clob_cfg = self.config.clob
        if not clob_cfg.private_key:
            raise ValueError("POLYMARKET_PRIVATE_KEY env var or clob.private_key config required")

        self._client = ClobClient(
            host=clob_cfg.host,
            chain_id=clob_cfg.chain_id,
            key=clob_cfg.private_key,
            funder=clob_cfg.funder or None,
            signature_type=clob_cfg.signature_type,
        )

        if clob_cfg.api_key and clob_cfg.api_secret and clob_cfg.api_passphrase:
            creds = ApiCreds(
                api_key=clob_cfg.api_key,
                api_secret=clob_cfg.api_secret,
                api_passphrase=clob_cfg.api_passphrase,
            )
        else:
            creds = self._client.create_or_derive_api_key()
            if creds is None:
                raise RuntimeError(
                    "CLOB API key derivation failed.\n"
                    "Check POLYMARKET_PRIVATE_KEY is correct and POLYMARKET_FUNDER_ADDRESS is set."
                )

        self._client = ClobClient(
            host=clob_cfg.host,
            chain_id=clob_cfg.chain_id,
            key=clob_cfg.private_key,
            funder=clob_cfg.funder or None,
            signature_type=clob_cfg.signature_type,
            creds=creds,
        )
        self._initialized = True

    def get_balance(self) -> float:
        if not self._initialized:
            raise RuntimeError("Executor not initialized. Call initialize() first.")
        try:
            from py_clob_client_v2 import BalanceAllowanceParams
            params = BalanceAllowanceParams(asset_type="COLLATERAL")
            balance_info = self._client.get_balance_allowance(params)
            if isinstance(balance_info, dict):
                raw = (
                    balance_info.get("balance")
                    or balance_info.get("collateral_balance")
                    or balance_info.get("collateralBalance", 0)
                )
                return float(raw or 0) / 1_000_000
            return 0.0
        except Exception:
            return -1.0

    def get_open_orders(self) -> list[dict[str, Any]]:
        if not self._initialized:
            return []
        try:
            from py_clob_client_v2 import OpenOrderParams
            orders = self._client.get_orders(OpenOrderParams())
            return orders if isinstance(orders, list) else []
        except Exception:
            return []

    def execute_copy_trade(self, trade_event: TradeEvent, amount_usdc: float) -> OrderResult:
        if not self._initialized:
            return OrderResult(success=False, error="Executor not initialized")
        exec_cfg = self.config.execution
        for attempt in range(exec_cfg.retry_attempts):
            try:
                result = self._place_order(trade_event.token_id, trade_event.side, amount_usdc, trade_event.price)
                if result.success:
                    return result
                if attempt < exec_cfg.retry_attempts - 1:
                    time.sleep(exec_cfg.retry_delay_seconds)
            except Exception as e:
                if attempt < exec_cfg.retry_attempts - 1:
                    time.sleep(exec_cfg.retry_delay_seconds)
                else:
                    return OrderResult(success=False, error=str(e))
        return OrderResult(success=False, error="Max retries exceeded")

    def execute_copy_trade_by_shares(self, trade_event: TradeEvent, shares: float) -> OrderResult:
        if not self._initialized:
            return OrderResult(success=False, error="Executor not initialized")
        exec_cfg = self.config.execution
        for attempt in range(exec_cfg.retry_attempts):
            try:
                result = self._place_order_by_size(trade_event.token_id, trade_event.side, shares)
                if result.success:
                    return result
                if attempt < exec_cfg.retry_attempts - 1:
                    time.sleep(exec_cfg.retry_delay_seconds)
            except Exception as e:
                if attempt < exec_cfg.retry_attempts - 1:
                    time.sleep(exec_cfg.retry_delay_seconds)
                else:
                    return OrderResult(success=False, error=str(e))
        return OrderResult(success=False, error="Max retries exceeded")

    def close_position(self, token_id: str, size: float, current_side: str) -> OrderResult:
        close_side = "SELL" if current_side == "BUY" else "BUY"
        try:
            return self._place_order_by_size(token_id, close_side, size)
        except Exception as e:
            return OrderResult(success=False, error=f"Close failed: {e}")

    def _place_order(self, token_id: str, side: str, amount_usdc: float, reference_price: float) -> OrderResult:
        from py_clob_client_v2 import MarketOrderArgs, OrderArgs, OrderType, PartialCreateOrderOptions, Side
        exec_cfg = self.config.execution
        clob_side = Side.BUY if side == "BUY" else Side.SELL
        options = PartialCreateOrderOptions(tick_size=exec_cfg.tick_size)

        if exec_cfg.order_type == "market":
            order_args = MarketOrderArgs(
                token_id=token_id, amount=amount_usdc, side=clob_side, order_type=OrderType.FOK,
            )
            resp = self._client.create_and_post_market_order(
                order_args=order_args, options=options, order_type=OrderType.FOK,
            )
        else:
            slippage = exec_cfg.slippage_tolerance_pct / 100
            if side == "BUY":
                limit_price = min(reference_price * (1 + slippage), 0.99)
            else:
                limit_price = max(reference_price * (1 - slippage), 0.01)
            size = amount_usdc / limit_price if limit_price > 0 else 0
            order_args = OrderArgs(
                token_id=token_id, price=round(limit_price, 2), size=round(size, 2), side=clob_side,
            )
            resp = self._client.create_and_post_order(
                order_args=order_args, options=options, order_type=OrderType.GTC,
            )
        return self._parse_order_response(resp, amount_usdc)

    def _place_order_by_size(self, token_id: str, side: str, size: float) -> OrderResult:
        from py_clob_client_v2 import MarketOrderArgs, OrderType, PartialCreateOrderOptions, Side
        exec_cfg = self.config.execution
        clob_side = Side.BUY if side == "BUY" else Side.SELL
        options = PartialCreateOrderOptions(tick_size=exec_cfg.tick_size)
        order_args = MarketOrderArgs(
            token_id=token_id, amount=size, side=clob_side, order_type=OrderType.FAK,
        )
        resp = self._client.create_and_post_market_order(
            order_args=order_args, options=options, order_type=OrderType.FAK,
        )
        return self._parse_order_response(resp, 0)

    def _parse_order_response(self, resp: Any, expected_amount: float) -> OrderResult:
        if resp is None:
            return OrderResult(success=False, error="No response from CLOB")
        if isinstance(resp, dict):
            if resp.get("success") or resp.get("orderID") or resp.get("status") == "matched":
                return OrderResult(
                    success=True,
                    order_id=resp.get("orderID") or resp.get("id", "unknown"),
                    filled_price=float(resp.get("averagePrice", 0) or 0),
                    filled_size=float(resp.get("filledSize", 0) or 0),
                    filled_amount_usdc=float(resp.get("filledAmount", expected_amount) or expected_amount),
                )
            error_msg = resp.get("errorMsg") or resp.get("error") or resp.get("message") or str(resp)
            return OrderResult(success=False, error=error_msg)
        return OrderResult(success=False, error=f"Unexpected response: {resp}")
