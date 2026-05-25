"""Root CLI entrypoint — allows `python cli.py <cmd>` without poetry install.

Proxies to polymarket_graph.cli:cli.
"""
from __future__ import annotations

import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

try:
    from dotenv import load_dotenv
    load_dotenv(ROOT / ".env")
except Exception:
    pass

# Prefer config/polymarket.yaml
os.environ.setdefault("POLYMARKET_CONFIG_PATH", str(ROOT / "config" / "polymarket.yaml"))
# Legacy fallback
os.environ.setdefault("CONFIG_PATH", str(ROOT / "config" / "polymarket.yaml"))

from polymarket_graph.cli import cli  # noqa: E402

if __name__ == "__main__":
    cli()
