import pandas as pd
from conftest import crossover_bars, make_bars

from tradebot.backtest import run_backtest


def test_backtest_takes_profit(cfg):
    up = [99.4 + 0.5 * i for i in range(1, 15)]
    bars = pd.concat([crossover_bars(), make_bars(up)], ignore_index=True)
    bars["date"] = pd.date_range("2026-09-29 09:30", periods=len(bars), freq="1min")
    result = run_backtest(bars, cfg.strategy, cfg.risk, fee_per_order=0)
    assert len(result.trades) == 1
    assert "take-profit" in result.trades[0].reason
    assert result.total_pnl > 0 and result.win_rate == 1.0


def test_backtest_flattens_at_end_of_day(cfg):
    bars = crossover_bars()
    bars = pd.concat([bars, make_bars([99.6])], ignore_index=True)
    bars["date"] = list(pd.date_range("2026-09-29 09:30", periods=len(bars) - 1, freq="1min")) + [
        pd.Timestamp("2026-09-30 09:30")
    ]
    result = run_backtest(bars, cfg.strategy, cfg.risk, flatten_daily=True)
    # Entry on the crossover bar is skipped because it is the day's last bar.
    assert result.trades == []
