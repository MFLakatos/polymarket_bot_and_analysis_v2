"""Terminal display — pretty-prints status, trades, and P&L."""
from __future__ import annotations

from datetime import datetime, timezone

from copy_wallets_positions.config import CopyTradingConfig
from copy_wallets_positions.executor import OrderResult
from copy_wallets_positions.monitor import TradeEvent
from copy_wallets_positions.risk import DailyStats, Position, RiskManager


class Colors:
    RESET = "\033[0m";  BOLD = "\033[1m";   DIM = "\033[2m"
    GREEN = "\033[92m"; RED = "\033[91m";   YELLOW = "\033[93m"
    BLUE = "\033[94m";  CYAN = "\033[96m";  MAGENTA = "\033[95m"
    WHITE = "\033[97m"; BG_GREEN = "\033[42m"; BG_RED = "\033[41m"


def print_banner() -> None:
    print(f"""
{Colors.CYAN}{Colors.BOLD}╔══════════════════════════════════════════════════════════════╗
║            POLYMARKET COPY TRADING BOT v1.0                  ║
╚══════════════════════════════════════════════════════════════╝{Colors.RESET}
""")


def print_config_summary(config: CopyTradingConfig) -> None:
    print(f"{Colors.BOLD}📋 Configuration:{Colors.RESET}")
    if config.risk.sizing_mode == "proportional_shares":
        print(f"   Sizing mode:         PROPORTIONAL SHARES (×{config.risk.share_fraction})")
    else:
        print(f"   Sizing mode:         FIXED USDC (${config.risk.max_trade_amount_usdc:.2f})")
    print(f"   Max trade amount:    ${config.risk.max_trade_amount_usdc:.2f} USDC")
    print(f"   Max daily drawdown:  {config.risk.max_daily_drawdown_pct:.1f}%")
    print(f"   Max open positions:  {config.risk.max_open_positions}")
    print(f"   Max market exposure: {config.risk.max_exposure_per_market_pct:.1f}%")
    print(f"   Order type:          {config.execution.order_type.upper()}")
    print(f"   Poll interval:       {config.monitor.poll_interval_seconds}s")
    print(f"   Price skip threshold:{config.risk.skip_if_price_moved_pct:.1f}%")
    print()


def print_wallets(config: CopyTradingConfig) -> None:
    print(f"{Colors.BOLD}👀 Monitoring {len(config.wallets)} wallet(s):{Colors.RESET}")
    for w in config.wallets:
        label = f" ({w.label})" if w.label else ""
        weight = f" [weight: {w.weight}x]" if w.weight != 1.0 else ""
        print(f"   {Colors.CYAN}{w.address}{Colors.RESET}{label}{weight}")
    print()


def print_balance(balance: float) -> None:
    if balance >= 0:
        print(f"{Colors.BOLD}💰 Account Balance: {Colors.GREEN}${balance:.2f} USDC{Colors.RESET}")
    else:
        print(f"{Colors.BOLD}💰 Account Balance: {Colors.YELLOW}Unable to fetch{Colors.RESET}")
    print()


def print_open_positions(positions: dict[str, Position]) -> None:
    if not positions:
        print(f"{Colors.DIM}   No open positions{Colors.RESET}")
        print()
        return
    print(f"{Colors.BOLD}📊 Open Positions ({len(positions)}):{Colors.RESET}")
    print(f"   {'Market':<35} {'Side':<5} {'Entry':<8} {'Size':<8} {'USDC':<10} {'Age'}")
    print(f"   {'─' * 35} {'─' * 5} {'─' * 8} {'─' * 8} {'─' * 10} {'─' * 8}")
    for pos in positions.values():
        age = f"{pos.age_minutes:.0f}m"
        title = pos.market_title[:33] + ".." if len(pos.market_title) > 35 else pos.market_title
        side_color = Colors.GREEN if pos.side == "BUY" else Colors.RED
        print(f"   {title:<35} {side_color}{pos.side:<5}{Colors.RESET} "
              f"${pos.entry_price:<7.4f} {pos.size:<8.2f} ${pos.usdc_invested:<9.2f} {age}")
    print()


def print_new_trade_detected(trade: TradeEvent) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    wallet_label = trade.wallet.label or trade.wallet.address[:10] + "..."
    side_color = Colors.GREEN if trade.side == "BUY" else Colors.RED
    print(f"\n{Colors.BOLD}{'─' * 70}{Colors.RESET}")
    print(f"{Colors.YELLOW}⚡ [{now}] New trade detected from {Colors.CYAN}{wallet_label}{Colors.RESET}")
    print(f"   Market:  {trade.market_title}")
    print(f"   Action:  {side_color}{trade.side}{Colors.RESET} {trade.outcome}")
    print(f"   Price:   ${trade.price:.4f}")
    print(f"   Size:    {trade.size:.2f} shares (${trade.usdc_amount:.2f} USDC)")


def print_copy_decision(will_copy: bool, reason: str, amount: float = 0, shares: float = 0) -> None:
    if will_copy:
        if shares > 0:
            print(f"   {Colors.GREEN}✓ COPYING{Colors.RESET} — {shares:.0f} shares (~${amount:.2f} USDC)")
        else:
            print(f"   {Colors.GREEN}✓ COPYING{Colors.RESET} — Amount: ${amount:.2f} USDC")
    else:
        print(f"   {Colors.RED}✗ SKIPPED{Colors.RESET} — {reason}")


def print_order_result(result: OrderResult, trade: TradeEvent) -> None:
    if result.success:
        print(f"   {Colors.BG_GREEN}{Colors.WHITE} FILLED {Colors.RESET} "
              f"Order {result.order_id or 'ok'} — "
              f"@${result.filled_price:.4f} for ${result.filled_amount_usdc:.2f}")
    else:
        print(f"   {Colors.BG_RED}{Colors.WHITE} FAILED {Colors.RESET} {result.error}")


def print_daily_summary(stats: DailyStats, positions: dict[str, Position]) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    pnl_color = Colors.GREEN if stats.realized_pnl >= 0 else Colors.RED
    sign = "+" if stats.realized_pnl >= 0 else ""
    open_usdc = sum(p.usdc_invested for p in positions.values())
    print(f"\n{Colors.BOLD}📈 [{now}] Daily Summary ({stats.date}):{Colors.RESET}")
    print(f"   Realized P&L:     {pnl_color}{sign}${stats.realized_pnl:.2f} ({sign}{stats.pnl_pct:.1f}%){Colors.RESET}")
    print(f"   Trades executed:  {stats.trades_executed}")
    print(f"   Trades skipped:   {stats.trades_skipped}")
    print(f"   Open positions:   {len(positions)} (${open_usdc:.2f} invested)")
    print(f"   Daily drawdown:   {stats.drawdown_pct:.1f}% / {stats.starting_balance:.2f} start")
    print()


def print_status_line(risk: RiskManager, poll_count: int) -> None:
    now = datetime.now(timezone.utc).strftime("%H:%M:%S")
    stats = risk.daily_stats
    pnl_color = Colors.GREEN if stats.realized_pnl >= 0 else Colors.RED
    sign = "+" if stats.realized_pnl >= 0 else ""
    print(f"{Colors.DIM}[{now}] Poll #{poll_count} | "
          f"Positions: {len(risk.positions)} | "
          f"P&L: {pnl_color}{sign}${stats.realized_pnl:.2f}{Colors.RESET}{Colors.DIM} | "
          f"Balance: ${risk.current_balance:.2f}{Colors.RESET}")


def print_error(msg: str) -> None:
    print(f"\n{Colors.RED}{Colors.BOLD}✗ ERROR: {msg}{Colors.RESET}\n")


def print_warning(msg: str) -> None:
    print(f"{Colors.YELLOW}⚠ WARNING: {msg}{Colors.RESET}")


def print_shutdown() -> None:
    print(f"\n{Colors.YELLOW}{Colors.BOLD}Bot shutting down...{Colors.RESET}")
