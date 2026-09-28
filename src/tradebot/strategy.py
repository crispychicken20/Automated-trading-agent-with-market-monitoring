"""Signal logic. Pure functions over OHLCV bars so live trading and backtests share it."""

from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from .config import RiskConfig, StrategyConfig


@dataclass
class Signal:
    action: str  # "buy", "sell" or "hold"
    reason: str


def add_indicators(bars: pd.DataFrame, cfg: StrategyConfig) -> pd.DataFrame:
    """Return a copy of `bars` (columns: open, high, low, close, volume) with indicators."""
    df = bars.copy()
    df["ema_fast"] = df["close"].ewm(span=cfg.fast_ema, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=cfg.slow_ema, adjust=False).mean()
    # Average volume of the bars *before* the current one.
    df["vol_avg"] = df["volume"].shift(1).rolling(cfg.volume_lookback).mean()
    delta = df["close"].diff()
    gain = delta.clip(lower=0).ewm(alpha=1 / cfg.rsi_period, adjust=False).mean()
    loss = (-delta.clip(upper=0)).ewm(alpha=1 / cfg.rsi_period, adjust=False).mean()
    rs = gain / loss.replace(0, float("nan"))
    df["rsi"] = (100 - 100 / (1 + rs)).fillna(100)
    return df


def entry_signal(df: pd.DataFrame, cfg: StrategyConfig) -> Signal:
    """Decide on the last bar of an indicator frame whether to open a long position."""
    if len(df) < max(cfg.slow_ema, cfg.volume_lookback) + 2:
        return Signal("hold", "not enough bars")
    prev, cur = df.iloc[-2], df.iloc[-1]
    crossed_up = prev["ema_fast"] <= prev["ema_slow"] and cur["ema_fast"] > cur["ema_slow"]
    if not crossed_up:
        return Signal("hold", "no bullish crossover")
    if pd.isna(cur["vol_avg"]) or cur["volume"] < cfg.volume_multiplier * cur["vol_avg"]:
        return Signal("hold", "crossover without volume confirmation")
    if cur["rsi"] > cfg.rsi_max_entry:
        return Signal("hold", f"crossover but RSI {cur['rsi']:.0f} overbought")
    return Signal(
        "buy",
        f"EMA{cfg.fast_ema} crossed above EMA{cfg.slow_ema}, "
        f"volume {cur['volume'] / cur['vol_avg']:.1f}x avg, RSI {cur['rsi']:.0f}",
    )


def exit_signal(
    df: pd.DataFrame, entry_price: float, strat: StrategyConfig, risk: RiskConfig
) -> Signal:
    """Decide on the last bar whether to close an open long position."""
    price = float(df["close"].iloc[-1])
    change_pct = (price / entry_price - 1) * 100
    if change_pct <= -risk.stop_loss_pct:
        return Signal("sell", f"stop-loss hit ({change_pct:.2f}%)")
    if change_pct >= risk.take_profit_pct:
        return Signal("sell", f"take-profit hit ({change_pct:.2f}%)")
    if len(df) >= 2:
        cur = df.iloc[-1]
        if cur["ema_fast"] < cur["ema_slow"]:
            return Signal("sell", f"trend reversed: EMA{strat.fast_ema} below EMA{strat.slow_ema}")
    return Signal("hold", f"position open ({change_pct:+.2f}%)")
