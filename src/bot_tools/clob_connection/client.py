"""
ClobConnection
==============
Unified executor for both simulation and live Polymarket CLOB trading.

In simulation mode all order methods return a fake OrderResult instantly.
In live mode they call py_clob_client_v2 (raises ImportError if not installed).

Usage::

    # Simulation
    conn = ClobConnection.simulation()
    result = conn.buy(token_id="0xabc...", shares=2, price=0.72)

    # Live
    conn = ClobConnection.live(
        private_key=os.environ["POLYMARKET_PRIVATE_KEY"],
        signature_type=0,
    )
    result = conn.buy(token_id="0xabc...", shares=2, price=0.72)

    # From config dict
    conn = ClobConnection.from_dict(cfg["clob"], mode="simulation")
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal, Optional


@dataclass
class OrderResult:
    """Result of a buy/sell attempt (simulation or live)."""
    success:          bool
    mode:             str        # "simulation" | "live"
    direction:        str        # "BUY" | "SELL"
    shares:           int
    est_share_price:  float      # price per share
    est_cost:         float      # shares × price
    order_id:         str = ""
    filled_price:     float = 0.0
    filled_shares:    float = 0.0
    error:            str = ""
    timestamp:        datetime = field(
        default_factory=lambda: datetime.now(timezone.utc)
    )

    @property
    def is_simulated(self) -> bool:
        return self.mode == "simulation"

    def __str__(self) -> str:
        if self.success:
            mode_tag = "(sim)" if self.is_simulated else ""
            return (f"OrderResult OK {mode_tag} "
                    f"{self.direction} {self.shares}× @${self.est_share_price:.4f} "
                    f"cost=${self.est_cost:.3f}  id={self.order_id or '-'}")
        return f"OrderResult FAILED: {self.error}"


class ClobConnection:
    """Unified simulation / live CLOB executor."""

    def __init__(
        self,
        mode:           Literal["simulation", "live"] = "simulation",
        private_key:    str = "",
        funder:         str = "",
        signature_type: int = 0,
        host:           str = "https://clob.polymarket.com",
        chain_id:       int = 137,
        tick_size:      str = "0.01",
    ) -> None:
        self.mode           = mode
        self.tick_size      = tick_size
        self._client: Any   = None

        if mode == "live":
            self._init_live(private_key, funder, signature_type, host, chain_id)

    # ── Factory helpers ───────────────────────────────────────────────────────

    @classmethod
    def simulation(cls) -> "ClobConnection":
        return cls(mode="simulation")

    @classmethod
    def live(
        cls,
        private_key:    str,
        signature_type: int = 0,
        funder:         str = "",
        host:           str = "https://clob.polymarket.com",
    ) -> "ClobConnection":
        return cls(
            mode="live",
            private_key=private_key,
            funder=funder,
            signature_type=signature_type,
            host=host,
        )

    @classmethod
    def from_dict(cls, cfg: dict, mode: str = "simulation") -> "ClobConnection":
        return cls(
            mode=mode,  # type: ignore[arg-type]
            private_key=os.environ.get("POLYMARKET_PRIVATE_KEY", cfg.get("private_key", "")),
            funder=os.environ.get("POLYMARKET_FUNDER_ADDRESS", cfg.get("funder", "")),
            signature_type=int(cfg.get("signature_type", 0)),
            host=cfg.get("host", "https://clob.polymarket.com"),
            chain_id=int(cfg.get("chain_id", 137)),
            tick_size=str(cfg.get("tick_size", "0.01")),
        )

    # ── Core order methods ────────────────────────────────────────────────────

    def buy(
        self,
        token_id:   str,
        shares:     int,
        price:      float,
        order_type: str = "market",
    ) -> OrderResult:
        """Place a BUY order. Returns OrderResult."""
        if self.mode == "simulation":
            return self._sim_result("BUY", shares, price)
        return self._live_order("BUY", token_id, shares, price, order_type)

    def sell(
        self,
        token_id:   str,
        shares:     int,
        price:      float,
        order_type: str = "market",
    ) -> OrderResult:
        """Place a SELL order. Returns OrderResult."""
        if self.mode == "simulation":
            return self._sim_result("SELL", shares, price)
        return self._live_order("SELL", token_id, shares, price, order_type)

    def get_balance(self) -> float:
        """USDC balance. Returns -1.0 if unavailable."""
        if self.mode == "simulation":
            return -1.0
        if self._client is None:
            return -1.0
        try:
            from py_clob_client_v2 import BalanceAllowanceParams
            info = self._client.get_balance_allowance(
                BalanceAllowanceParams(asset_type="COLLATERAL")
            )
            if isinstance(info, dict):
                raw = (info.get("balance")
                       or info.get("collateral_balance")
                       or info.get("collateralBalance", 0))
                return float(raw or 0) / 1_000_000
        except Exception:
            pass
        return -1.0

    def is_live(self) -> bool:
        return self.mode == "live"

    # ── Internal ──────────────────────────────────────────────────────────────

    def _sim_result(self, direction: str, shares: int, price: float) -> OrderResult:
        import random, string
        fake_id = "SIM-" + "".join(random.choices(string.hexdigits[:16], k=8)).upper()
        return OrderResult(
            success=True, mode="simulation",
            direction=direction, shares=shares,
            est_share_price=price, est_cost=round(shares * price, 4),
            order_id=fake_id,
            filled_price=price, filled_shares=float(shares),
        )

    def _live_order(
        self,
        direction: str,
        token_id:  str,
        shares:    int,
        price:     float,
        order_type: str,
    ) -> OrderResult:
        if self._client is None:
            return OrderResult(success=False, mode="live", direction=direction,
                               shares=shares, est_share_price=price,
                               est_cost=shares * price,
                               error="CLOB client not initialised")
        try:
            from py_clob_client_v2 import (
                MarketOrderArgs, OrderArgs, OrderType,
                PartialCreateOrderOptions, Side,
            )
            clob_side = Side.BUY if direction == "BUY" else Side.SELL
            options   = PartialCreateOrderOptions(tick_size=self.tick_size)

            if order_type == "market":
                order_args = MarketOrderArgs(
                    token_id=token_id, amount=float(shares * price),
                    side=clob_side, order_type=OrderType.FOK,
                )
                resp = self._client.create_and_post_market_order(
                    order_args=order_args, options=options, order_type=OrderType.FOK,
                )
            else:
                order_args = OrderArgs(  # type: ignore[assignment]
                    token_id=token_id, price=round(price, 2),
                    size=float(shares), side=clob_side,
                )
                resp = self._client.create_and_post_order(
                    order_args=order_args, options=options, order_type=OrderType.GTC,
                )
            return self._parse_resp(direction, shares, price, resp)
        except Exception as exc:
            return OrderResult(success=False, mode="live", direction=direction,
                               shares=shares, est_share_price=price,
                               est_cost=shares * price, error=str(exc))

    def _parse_resp(
        self,
        direction: str,
        shares: int,
        price: float,
        resp: Any,
    ) -> OrderResult:
        if resp and isinstance(resp, dict):
            if resp.get("success") or resp.get("orderID") or resp.get("status") == "matched":
                return OrderResult(
                    success=True, mode="live", direction=direction,
                    shares=shares, est_share_price=price,
                    est_cost=round(shares * price, 4),
                    order_id=resp.get("orderID") or resp.get("id", ""),
                    filled_price=float(resp.get("averagePrice") or price),
                    filled_shares=float(resp.get("filledSize") or shares),
                )
            err = resp.get("errorMsg") or resp.get("error") or resp.get("message") or str(resp)
            return OrderResult(success=False, mode="live", direction=direction,
                               shares=shares, est_share_price=price,
                               est_cost=shares * price, error=err)
        return OrderResult(success=False, mode="live", direction=direction,
                           shares=shares, est_share_price=price,
                           est_cost=shares * price,
                           error=f"Unexpected response: {resp}")

    def _init_live(
        self,
        private_key: str,
        funder: str,
        signature_type: int,
        host: str,
        chain_id: int,
    ) -> None:
        try:
            from py_clob_client_v2 import ClobClient
        except ImportError:
            raise ImportError(
                "py_clob_client_v2 is required for live trading.\n"
                "Run: poetry install --extras copy-trading"
            )
        if not private_key:
            raise ValueError("private_key is required for live mode")

        client = ClobClient(
            host=host, chain_id=chain_id,
            key=private_key,
            funder=funder or None,
            signature_type=signature_type,
        )
        creds = client.create_or_derive_api_key()
        if creds:
            client = ClobClient(
                host=host, chain_id=chain_id,
                key=private_key, funder=funder or None,
                signature_type=signature_type, creds=creds,
            )
        self._client = client
