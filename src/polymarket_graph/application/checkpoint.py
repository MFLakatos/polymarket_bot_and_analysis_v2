"""Checkpoint store for incremental ingestion state."""
from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


@dataclass
class CheckpointState:
    last_gamma_api_ts: Optional[str] = None
    last_clob_api_ts: Optional[str] = None
    total_trades_ingested: int = 0
    extras: dict = field(default_factory=dict)


class CheckpointStore:
    """Persist and load ingestion state as JSON."""

    def __init__(self, path: str) -> None:
        self._path = Path(path)

    def load(self) -> CheckpointState:
        if not self._path.exists():
            return CheckpointState()
        try:
            raw = json.loads(self._path.read_text())
            return CheckpointState(**{k: v for k, v in raw.items() if k in CheckpointState.__dataclass_fields__})
        except Exception:
            return CheckpointState()

    def save(self, state: CheckpointState) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(json.dumps(asdict(state), indent=2))

    @staticmethod
    def from_iso(ts_str: Optional[str]) -> Optional[datetime]:
        if not ts_str:
            return None
        try:
            return datetime.fromisoformat(ts_str)
        except Exception:
            return None

    @staticmethod
    def to_iso(dt: Optional[datetime]) -> Optional[str]:
        if dt is None:
            return None
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.isoformat()
