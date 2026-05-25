"""Core domain entities for the Polymarket graph.

Pydantic models provide validation at the system boundary (API responses)
while remaining safe to instantiate by hand inside the domain layer.
"""
from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Optional

from pydantic import BaseModel, ConfigDict, Field, field_validator


class TradeSide(str, Enum):
    BUY = "BUY"
    SELL = "SELL"


class Outcome(BaseModel):
    """A possible outcome of a Polymarket market (e.g. YES / NO)."""

    model_config = ConfigDict(frozen=True)

    id: str
    market_id: str
    name: str
    token_id: Optional[str] = None
    is_winner: Optional[bool] = None  # None until market resolves


class Market(BaseModel):
    """A Polymarket prediction market."""

    model_config = ConfigDict(frozen=True)

    id: str
    question: str
    slug: Optional[str] = None
    category: Optional[str] = None
    created_at: Optional[datetime] = None
    closed_at: Optional[datetime] = None
    resolved: bool = False
    outcomes: tuple[Outcome, ...] = Field(default_factory=tuple)

    @property
    def winning_outcome_id(self) -> Optional[str]:
        for o in self.outcomes:
            if o.is_winner:
                return o.id
        return None


class Wallet(BaseModel):
    """An on-chain wallet that participates in Polymarket trades."""

    model_config = ConfigDict(frozen=True)

    id: str  # normalized lowercase 0x address
    first_seen: Optional[datetime] = None
    last_seen: Optional[datetime] = None

    @field_validator("id")
    @classmethod
    def _normalize(cls, v: str) -> str:
        v = v.strip().lower()
        if not v:
            raise ValueError("wallet id cannot be empty")
        return v


class Trade(BaseModel):
    """A single trade event executed by a wallet on a market outcome."""

    model_config = ConfigDict(frozen=True)

    trade_id: str
    wallet_id: str
    market_id: str
    outcome_id: Optional[str] = None
    side: TradeSide
    price: float = Field(ge=0.0, le=1.0)
    size: float = Field(gt=0.0)
    timestamp: datetime
    tx_hash: Optional[str] = None

    @field_validator("wallet_id")
    @classmethod
    def _norm_wallet(cls, v: str) -> str:
        return v.strip().lower()

    @property
    def notional(self) -> float:
        """USD notional value of the trade (price × size)."""
        return self.price * self.size

    @property
    def dedup_key(self) -> tuple[str, str, str, float]:
        """Deterministic key used to deduplicate trades across feeds."""
        return (self.trade_id, self.wallet_id, self.market_id, self.timestamp.timestamp())
