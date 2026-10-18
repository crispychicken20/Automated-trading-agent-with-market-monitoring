"""Hard risk limits. Every order goes through here before it reaches the broker."""

from __future__ import annotations

import math
from dataclasses import dataclass

from .config import RiskConfig

MIN_POSITION_FRACTION = 0.1  # smallest new position, as a share of max_position_usd


@dataclass
class AccountState:
    open_positions: int
    trades_today: int
    daily_pnl: float
    stock_day_trades_5d: int
    committed_usd: float = 0.0  # cost basis of all open positions


class RiskManager:
    def __init__(self, cfg: RiskConfig):
        self.cfg = cfg

    def daily_loss_breached(self, daily_pnl: float) -> bool:
        return daily_pnl <= -self.cfg.daily_loss_limit_usd

    def remaining_budget(self, committed_usd: float) -> float:
        return max(0.0, self.cfg.max_total_budget_usd - committed_usd)

    def can_open(self, state: AccountState, market: str) -> tuple[bool, str]:
        # Don't open tiny leftover positions with the last scraps of the budget.
        min_position = MIN_POSITION_FRACTION * self.cfg.max_position_usd
        if self.remaining_budget(state.committed_usd) < min_position:
            return False, (
                f"total budget used up (${state.committed_usd:,.2f} of "
                f"${self.cfg.max_total_budget_usd:,.2f} committed)"
            )
        if self.daily_loss_breached(state.daily_pnl):
            return False, f"daily loss limit reached ({state.daily_pnl:.2f})"
        if state.open_positions >= self.cfg.max_open_positions:
            return False, f"max open positions ({self.cfg.max_open_positions}) reached"
        # Each trade needs an exit too, so leave room for it under the daily cap.
        if state.trades_today + 2 > self.cfg.max_trades_per_day:
            return False, f"max trades per day ({self.cfg.max_trades_per_day}) reached"
        # Stock positions are closed the same day, so every new one becomes a day trade.
        if market == "stocks" and state.stock_day_trades_5d >= self.cfg.max_stock_day_trades_5d:
            return False, (
                f"pattern-day-trader guard: {state.stock_day_trades_5d} stock day trades "
                "in the last 5 business days"
            )
        return True, "ok"

    def position_size(
        self, price: float, qty_decimals: int = 0, committed_usd: float = 0.0
    ) -> float:
        """Quantity to buy so the position is at most max_position_usd and the total
        committed stays within max_total_budget_usd."""
        if price <= 0:
            return 0
        dollars = min(self.cfg.max_position_usd, self.remaining_budget(committed_usd))
        step = 10 ** -qty_decimals
        qty = math.floor(dollars / price / step) * step
        return round(qty, qty_decimals)

    def limit_price(self, side: str, price: float) -> float:
        """A marketable limit price: slightly above for buys, slightly below for sells."""
        offset = price * self.cfg.slippage_bps / 10_000
        return round(price + offset if side == "BUY" else price - offset, 2)
