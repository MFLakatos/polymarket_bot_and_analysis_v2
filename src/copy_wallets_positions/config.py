"""Configuration for the copy trading bot."""
from __future__ import annotations

from pathlib import Path
from typing import Optional

import yaml
from pydantic import BaseModel, Field


class WalletTarget(BaseModel):
    address: str = Field(description="Wallet address (0x...)")
    label: str = Field(default="", description="Friendly name for terminal display")
    weight: float = Field(default=1.0, description="Allocation weight (1.0 = full share)")


class RiskConfig(BaseModel):
    max_trade_amount_usdc: float = Field(default=1.0, description="Max USDC per single trade")
    min_trade_amount_usdc: float = Field(default=0.1, description="Min USDC per trade (skip if below)")
    max_daily_drawdown_pct: float = Field(default=10.0, description="Max daily loss as % of starting balance")
    max_open_positions: int = Field(default=20, description="Max simultaneous open positions")
    max_exposure_per_market_pct: float = Field(default=25.0, description="Max % of balance in a single market")
    cooldown_after_loss_seconds: int = Field(default=60, description="Pause after a losing trade (seconds)")
    skip_if_price_moved_pct: float = Field(default=5.0, description="Skip if price moved >X% since whale traded")
    sizing_mode: str = Field(default="fixed_usdc", description="'fixed_usdc' or 'proportional_shares'")
    share_fraction: float = Field(default=0.5, description="When proportional_shares: floor(whale_shares * fraction)")


class ExecutionConfig(BaseModel):
    order_type: str = Field(default="market", description="'market' (FOK) or 'limit' (GTC)")
    slippage_tolerance_pct: float = Field(default=2.0, description="Max slippage for limit orders (%)")
    tick_size: str = Field(default="0.01", description="Tick size for the market")
    retry_attempts: int = Field(default=3, description="Retries on failed order placement")
    retry_delay_seconds: float = Field(default=2.0, description="Delay between retries (seconds)")


class MonitorConfig(BaseModel):
    poll_interval_seconds: float = Field(default=15, description="How often to check for new trades (seconds)")
    lookback_on_start_minutes: int = Field(default=5, description="On startup, look back this many minutes")
    data_api_base_url: str = Field(default="https://data-api.polymarket.com")
    gamma_api_base_url: str = Field(default="https://gamma-api.polymarket.com")


class ClobConfig(BaseModel):
    host: str = Field(default="https://clob.polymarket.com")
    chain_id: int = Field(default=137, description="Polygon mainnet=137, Amoy testnet=80002")
    private_key: str = Field(default="", description="Wallet private key (use env var)")
    funder: str = Field(default="", description="Funder address for email/Magic wallets")
    signature_type: int = Field(default=0, description="0=EOA, 1=email/Magic, 2=browser proxy")
    api_key: Optional[str] = Field(default=None)
    api_secret: Optional[str] = Field(default=None)
    api_passphrase: Optional[str] = Field(default=None)


class NotificationsConfig(BaseModel):
    show_balance_every_n_trades: int = Field(default=5, description="Print balance every N trades")
    show_pnl_update_minutes: int = Field(default=30, description="Print P&L summary every N minutes")


class CopyTradingConfig(BaseModel):
    wallets: list[WalletTarget] = Field(default_factory=list)
    risk: RiskConfig = Field(default_factory=RiskConfig)
    execution: ExecutionConfig = Field(default_factory=ExecutionConfig)
    monitor: MonitorConfig = Field(default_factory=MonitorConfig)
    clob: ClobConfig = Field(default_factory=ClobConfig)
    notifications: NotificationsConfig = Field(default_factory=NotificationsConfig)

    @classmethod
    def from_yaml(cls, path: str | Path) -> "CopyTradingConfig":
        """Load config from YAML, with env var overrides for secrets."""
        import os
        p = Path(path)
        if not p.exists():
            raise FileNotFoundError(f"Config not found: {p}")
        with open(p) as f:
            data = yaml.safe_load(f) or {}

        clob_data = data.get("clob", {})
        clob_data.setdefault("private_key", os.environ.get("POLYMARKET_PRIVATE_KEY", ""))
        clob_data.setdefault("funder", os.environ.get("POLYMARKET_FUNDER_ADDRESS", ""))
        clob_data.setdefault("api_key", os.environ.get("POLYMARKET_API_KEY") or None)
        clob_data.setdefault("api_secret", os.environ.get("POLYMARKET_API_SECRET") or None)
        clob_data.setdefault("api_passphrase", os.environ.get("POLYMARKET_API_PASSPHRASE") or None)
        data["clob"] = clob_data
        return cls.model_validate(data)
