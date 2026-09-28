from conftest import crossover_bars, flat_bars, make_bars

from tradebot.strategy import add_indicators, entry_signal, exit_signal


def test_bullish_crossover_with_volume_is_a_buy(cfg):
    sig = entry_signal(add_indicators(crossover_bars(), cfg.strategy), cfg.strategy)
    assert sig.action == "buy", sig.reason


def test_crossover_without_volume_is_held(cfg):
    bars = crossover_bars()
    bars.loc[bars.index[-1], "volume"] = 100
    sig = entry_signal(add_indicators(bars, cfg.strategy), cfg.strategy)
    assert sig.action == "hold"
    assert "volume" in sig.reason


def test_flat_market_is_held(cfg):
    assert entry_signal(add_indicators(flat_bars(), cfg.strategy), cfg.strategy).action == "hold"


def test_too_few_bars_is_held(cfg):
    df = add_indicators(make_bars([100.0] * 5), cfg.strategy)
    assert entry_signal(df, cfg.strategy).reason == "not enough bars"


def test_stop_loss_and_take_profit(cfg):
    df = add_indicators(flat_bars(100.0), cfg.strategy)
    assert "stop-loss" in exit_signal(df, 102.0, cfg.strategy, cfg.risk).reason
    assert "take-profit" in exit_signal(df, 96.0, cfg.strategy, cfg.risk).reason
    assert exit_signal(df, 100.0, cfg.strategy, cfg.risk).action == "hold"


def test_trend_reversal_exits(cfg):
    closes = [100 + 0.1 * i for i in range(40)] + [98.0]
    df = add_indicators(make_bars(closes), cfg.strategy)
    sig = exit_signal(df, 98.5, cfg.strategy, cfg.risk)  # -0.5%: not a stop-loss
    assert sig.action == "sell" and "reversed" in sig.reason
