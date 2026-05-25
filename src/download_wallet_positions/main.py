"""
Download all trades, positions, activity and P&L for a Polymarket wallet.

Saves to data/wallets/{address}/ as JSON + CSV files.
"""
from __future__ import annotations

import csv
import json
import os
import time
from datetime import datetime
from pathlib import Path
from typing import Optional

import requests

DATA_API = "https://data-api.polymarket.com"
PAGE_SIZE = 500
SLEEP_SEC = 0.25


def fetch_paginated(url: str, params: dict) -> list:
    """Fetch all pages using offset-based pagination."""
    results = []
    params = {**params, "limit": PAGE_SIZE, "offset": 0}
    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        page = data if isinstance(data, list) else data.get("data", data.get("results", []))
        if not page:
            break
        results.extend(page)
        if len(page) < PAGE_SIZE:
            break
        params["offset"] += PAGE_SIZE
        time.sleep(SLEEP_SEC)
    return results


def fetch_cursor_paginated(url: str, params: dict) -> list:
    """Fetch all pages using cursor-based pagination."""
    results = []
    params = {**params, "limit": PAGE_SIZE}
    while True:
        resp = requests.get(url, params=params, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, list):
            records = data
            next_cursor = None
        else:
            records = data.get("data", data.get("results", []))
            next_cursor = data.get("next_cursor")
        results.extend(records)
        if not next_cursor or next_cursor == "LTE=":
            break
        params["next_cursor"] = next_cursor
        time.sleep(SLEEP_SEC)
    return results


def get_trades(address: str) -> list:
    print("\n[1/4] Fetching trades (CLOB fills)...")
    return fetch_cursor_paginated(f"{DATA_API}/trades", {"user": address})


def get_positions(address: str) -> list:
    print("\n[2/4] Fetching positions...")
    return fetch_paginated(f"{DATA_API}/positions", {"user": address})


def get_activity(address: str) -> list:
    print("\n[3/4] Fetching activity...")
    return fetch_cursor_paginated(f"{DATA_API}/activity", {"user": address})


def get_pnl(address: str) -> dict:
    print("\n[4/4] Fetching P&L summary...")
    resp = requests.get(f"{DATA_API}/pnl", params={"user": address}, timeout=30)
    if resp.ok:
        return resp.json()
    print(f"  P&L endpoint returned {resp.status_code} — skipping.")
    return {}


def flatten(obj: dict, parent_key: str = "", sep: str = ".") -> dict:
    """Recursively flatten a nested dict for CSV export."""
    items = {}
    for k, v in obj.items():
        new_key = f"{parent_key}{sep}{k}" if parent_key else k
        if isinstance(v, dict):
            items.update(flatten(v, new_key, sep))
        elif isinstance(v, list):
            items[new_key] = json.dumps(v)
        else:
            items[new_key] = v
    return items


def save_csv(records: list, filename: str) -> None:
    if not records:
        print(f"  No records → {filename}")
        return
    flat = [flatten(r) for r in records]
    all_keys = list(dict.fromkeys(k for row in flat for k in row))
    with open(filename, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=all_keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(flat)
    print(f"  Saved {len(flat)} rows → {filename}")


def download(address: str, output_dir: Optional[str] = None) -> Path:
    """Download all data for a wallet and save to output_dir."""
    if output_dir is None:
        output_dir = str(Path("data/wallets") / address[:10])

    out = Path(output_dir)
    out.mkdir(parents=True, exist_ok=True)

    print(f"Downloading Polymarket data for: {address}")
    print("=" * 60)

    trades = get_trades(address)
    positions = get_positions(address)
    activity = get_activity(address)
    pnl = get_pnl(address)

    bundle = {
        "wallet": address,
        "downloaded_at": datetime.utcnow().isoformat() + "Z",
        "summary": {
            "total_trades": len(trades),
            "total_positions": len(positions),
            "total_activity": len(activity),
        },
        "pnl": pnl,
        "trades": trades,
        "positions": positions,
        "activity": activity,
    }

    json_path = out / "polymarket_data.json"
    with open(json_path, "w", encoding="utf-8") as f:
        json.dump(bundle, f, indent=2, ensure_ascii=False)
    print(f"\nFull bundle → {json_path}")

    print("\nWriting CSVs...")
    save_csv(trades, str(out / "trades.csv"))
    save_csv(positions, str(out / "positions.csv"))
    save_csv(activity, str(out / "activity.csv"))
    combined = (
        [{"_section": "trade", **r} for r in trades]
        + [{"_section": "activity", **r} for r in activity]
    )
    save_csv(combined, str(out / "combined.csv"))

    print(f"\n── Summary {'─' * 40}")
    print(f"  Trades (fills):   {len(trades)}")
    print(f"  Positions:        {len(positions)}")
    print(f"  Activity events:  {len(activity)}")
    if pnl:
        print(f"  P&L:              {json.dumps(pnl)}")
    print(f"\nAll files saved to: {out}")
    return out
