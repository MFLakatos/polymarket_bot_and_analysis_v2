"""Main entry point — orchestration loop for the copy trading bot."""
from __future__ import annotations

import signal
import sys
import time
import os
from datetime import datetime, timezone
from pathlib import Path

import click

from copy_wallets_positions.config import CopyTradingConfig
from copy_wallets_positions.display import (
    print_balance, print_banner, print_config_summary, print_copy_decision,
    print_daily_summary, print_error, print_new_trade_detected, print_open_positions,
    print_order_result, print_shutdown, print_status_line, print_wallets, print_warning,
)
from copy_wallets_positions.executor import OrderExecutor
from copy_wallets_positions.monitor import TradeEvent, WalletMonitor
from copy_wallets_positions.risk import RiskManager
from save_wallet_positions.save_wallet_positions import save_order_to_csv, get_save_path
from save_wallet_positions.save_detected_trades import save_detected_trade_to_csv, get_detected_trades_path


class CopyTradingBot:
    """Main bot — ties monitoring, execution, risk, and display together."""

    def __init__(self, config_path: str = "config/copy_trading.yaml"):
        self.config = CopyTradingConfig.from_yaml(config_path)
        self.monitor = WalletMonitor(self.config)
        self.executor = OrderExecutor(self.config)
        self.risk: RiskManager | None = None
        self._running = False
        self._poll_count = 0
        self._last_summary_time = datetime.now(timezone.utc)
        self._trades_since_last_balance = 0
        self._csv_paths: dict = {}
        self._detected_csv_paths: dict = {}
        self._save_dir = str(Path(__file__).parent.parent.parent / "data" / "copy_trading")
        os.makedirs(self._save_dir, exist_ok=True)

    def run(self) -> None:
        signal.signal(signal.SIGINT, self._handle_shutdown)
        signal.signal(signal.SIGTERM, self._handle_shutdown)

        print_banner()
        print_config_summary(self.config)
        print_wallets(self.config)

        if not self.config.wallets:
            print_error("No wallets configured. Add wallets to config/copy_trading.yaml")
            sys.exit(1)

        print("🔑 Initializing CLOB API connection...")
        try:
            self.executor.initialize()
            print("   ✓ CLOB API connected\n")
        except ImportError as e:
            print_error(str(e))
            print_warning("Run: poetry install --extras copy-trading")
            sys.exit(1)
        except Exception as e:
            print_error(f"CLOB initialization failed: {e}")
            sys.exit(1)

        balance = self.executor.get_balance()
        print_balance(balance)

        if balance <= 0:
            print_warning("Could not fetch balance. Continuing with configured limits only.")
            balance = self.config.risk.max_trade_amount_usdc * self.config.risk.max_open_positions

        self.risk = RiskManager(self.config, initial_balance=balance)

        print("📡 Fetching recent activity from target wallets...")
        recent_trades = self.monitor.initialize(
            lookback_minutes=self.config.monitor.lookback_on_start_minutes
        )
        if recent_trades:
            print(f"\n{len(recent_trades)} recent trades found (establishing baseline, not copying):")
            for t in recent_trades[:10]:
                label = t.wallet.label or t.wallet.address[:10]
                sym = "🟢" if t.side == "BUY" else "🔴"
                print(f"   {sym} [{label}] {t.side} {t.outcome} @{t.price:.3f} — {t.market_title[:50]}")
            if len(recent_trades) > 10:
                print(f"   ... and {len(recent_trades) - 10} more")
        else:
            print("   No recent activity found.")

        print_open_positions(self.risk.positions)
        print(f"\n{'═' * 70}")
        print(f"🚀 Bot is LIVE — polling every {self.config.monitor.poll_interval_seconds}s")
        print(f"   Press Ctrl+C to stop")
        print(f"{'═' * 70}\n")

        self._running = True
        self._main_loop()

    def _main_loop(self) -> None:
        while self._running:
            try:
                self._poll_count += 1
                new_trades = self.monitor.poll()
                if new_trades:
                    for trade in new_trades:
                        self._process_trade(trade)
                else:
                    if self._poll_count % 4 == 0 and self.risk:
                        print_status_line(self.risk, self._poll_count)
                self._maybe_print_summary()
                time.sleep(self.config.monitor.poll_interval_seconds)
            except KeyboardInterrupt:
                break
            except Exception as e:
                print_error(f"Poll error: {e}")
                time.sleep(self.config.monitor.poll_interval_seconds * 2)
        self._shutdown()

    def _process_trade(self, trade: TradeEvent) -> None:
        assert self.risk is not None

        wallet_addr = trade.wallet.address
        if wallet_addr not in self._detected_csv_paths:
            self._detected_csv_paths[wallet_addr] = get_detected_trades_path(wallet_addr, base_dir=self._save_dir)
        detected_csv_path = self._detected_csv_paths[wallet_addr]

        print_new_trade_detected(trade)
        current_price = self.monitor.get_current_price(trade.token_id)
        should_copy, reason = self.risk.should_copy_trade(trade, current_price)

        if not should_copy:
            save_detected_trade_to_csv(trade, "skipped", reason, detected_csv_path)
            print_copy_decision(False, reason)
            self.risk.record_skip()
            return

        if self.config.risk.sizing_mode == "proportional_shares":
            shares = self.risk.compute_trade_shares(trade)
            if shares <= 0:
                save_detected_trade_to_csv(trade, "skipped", "Computed shares too small", detected_csv_path)
                print_copy_decision(False, "Computed shares too small")
                self.risk.record_skip()
                return
            estimated_usdc = shares * trade.price if trade.price > 0 else shares
            print_copy_decision(True, reason, estimated_usdc, shares=shares)
            result = self.executor.execute_copy_trade_by_shares(trade, shares)
            print_order_result(result, trade)
            status = "copied" if result.success else "failed"
            save_detected_trade_to_csv(trade, status, result.error or "", detected_csv_path)
            if result.success:
                key = (trade.wallet.address, trade.market_title)
                if key not in self._csv_paths:
                    self._csv_paths[key] = get_save_path(trade.wallet.address, trade.market_title, base_dir=self._save_dir)
                save_order_to_csv(trade, result, self._csv_paths[key])
                self.risk.record_trade(trade, result.filled_price or trade.price,
                                       result.filled_size or shares, result.filled_amount_usdc or estimated_usdc)
                self._trades_since_last_balance += 1
        else:
            amount = self.risk.compute_trade_size(trade)
            if amount <= 0:
                save_detected_trade_to_csv(trade, "skipped", "Computed amount too small", detected_csv_path)
                print_copy_decision(False, "Computed amount too small")
                self.risk.record_skip()
                return
            print_copy_decision(True, reason, amount)
            result = self.executor.execute_copy_trade(trade, amount)
            print_order_result(result, trade)
            status = "copied" if result.success else "failed"
            save_detected_trade_to_csv(trade, status, result.error or "", detected_csv_path)
            if result.success:
                self.risk.record_trade(trade, result.filled_price or trade.price,
                                       result.filled_size or (amount / trade.price if trade.price > 0 else 0),
                                       result.filled_amount_usdc or amount)
                self._trades_since_last_balance += 1

        if self._trades_since_last_balance >= self.config.notifications.show_balance_every_n_trades:
            new_balance = self.executor.get_balance()
            if new_balance > 0:
                self.risk.update_balance(new_balance)
            self._trades_since_last_balance = 0
            print_balance(new_balance)

    def _maybe_print_summary(self) -> None:
        if self.risk is None:
            return
        now = datetime.now(timezone.utc)
        elapsed = (now - self._last_summary_time).total_seconds() / 60
        if elapsed >= self.config.notifications.show_pnl_update_minutes:
            print_daily_summary(self.risk.daily_stats, self.risk.positions)
            self._last_summary_time = now

    def _handle_shutdown(self, signum: int, frame: object) -> None:
        self._running = False

    def _shutdown(self) -> None:
        print_shutdown()
        if self.risk:
            print_daily_summary(self.risk.daily_stats, self.risk.positions)
            if self.risk.positions:
                print_open_positions(self.risk.positions)
                print_warning(f"You have {len(self.risk.positions)} open position(s). They will NOT be closed automatically.")


@click.command()
@click.option("--config", "config_path", default="config/copy_trading.yaml",
              help="Path to copy_trading.yaml config file.")
def main(config_path: str) -> None:
    """Polymarket Copy Trading Bot — mirrors trades from target wallets."""
    try:
        from dotenv import load_dotenv
        load_dotenv()
    except ImportError:
        pass
    bot = CopyTradingBot(config_path=config_path)
    bot.run()


if __name__ == "__main__":
    main()
