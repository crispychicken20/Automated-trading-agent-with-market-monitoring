"""Replay historical bars through the same strategy and risk code used live."""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from .config import RiskConfig, StrategyConfig
from .risk import RiskManager
from .strategy import add_indicators, entry_signal, exit_signal


@dataclass
class Trade:
    entry_time: str
    exit_time: str
    entry: float
    exit: float
    qty: float
    pnl: float
    reason: str


@dataclass
class BacktestResult:
    trades: list[Trade] = field(default_factory=list)

    @property
    def total_pnl(self) -> float:
        return sum(t.pnl for t in self.trades)

    @property
    def win_rate(self) -> float:
        return sum(t.pnl > 0 for t in self.trades) / len(self.trades) if self.trades else 0.0

    @property
    def max_drawdown(self) -> float:
        peak = equity = worst = 0.0
        for t in self.trades:
            equity += t.pnl
            peak = max(peak, equity)
            worst = min(worst, equity - peak)
        return worst

    def summary(self) -> str:
        return (
            f"trades: {len(self.trades)}  total P&L: ${self.total_pnl:,.2f}  "
            f"win rate: {self.win_rate:.0%}  max drawdown: ${self.max_drawdown:,.2f}"
        )


def run_backtest(
    bars: pd.DataFrame,
    strat: StrategyConfig,
    risk_cfg: RiskConfig,
    qty_decimals: int = 0,
    fee_per_order: float = 1.0,
    flatten_daily: bool = False,
) -> BacktestResult:
    """Long-only simulation. Fills at the bar close adjusted by slippage_bps.

    With flatten_daily (stocks), any open position is closed on the last bar of each day.
    """
    risk = RiskManager(risk_cfg)
    df = add_indicators(bars.reset_index(drop=True), strat)
    days = pd.to_datetime(df["date"]).dt.date if flatten_daily else None
    result = BacktestResult()
    pos = None  # (entry_time, entry_price, qty)

    for i in range(len(df)):
        window = df.iloc[: i + 1]
        price = float(df["close"].iloc[i])
        ts = str(df["date"].iloc[i])
        last_bar_of_day = flatten_daily and (i == len(df) - 1 or days.iloc[i + 1] != days.iloc[i])

        if pos:
            sig = exit_signal(window, pos[1], strat, risk_cfg)
            reason = sig.reason if sig.action == "sell" else None
            if reason is None and last_bar_of_day:
                reason = "end-of-day flatten"
            if reason:
                exit_px = risk.limit_price("SELL", price)
                pnl = (exit_px - pos[1]) * pos[2] - 2 * fee_per_order
                result.trades.append(Trade(pos[0], ts, pos[1], exit_px, pos[2], pnl, reason))
                pos = None
            continue

        if last_bar_of_day:
            continue
        if entry_signal(window, strat).action == "buy":
            entry_px = risk.limit_price("BUY", price)
            qty = risk.position_size(entry_px, qty_decimals)
            if qty > 0:
                pos = (ts, entry_px, qty)

    return result


def load_csv(path: str) -> pd.DataFrame:
    df = pd.read_csv(path)
    df.columns = [c.strip().lower() for c in df.columns]
    missing = {"date", "open", "high", "low", "close", "volume"} - set(df.columns)
    if missing:
        raise ValueError(f"{path} is missing columns: {sorted(missing)}")
    return df
