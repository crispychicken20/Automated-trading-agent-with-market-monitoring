from conftest import MARKET_HOURS, NEAR_CLOSE, WEEKEND, FakeBroker, crossover_bars, flat_bars

from tradebot.broker import Position
from tradebot.engine import Engine
from tradebot.report import build_report


def engine(cfg, journal, broker, now):
    return Engine(cfg, broker, journal, clock=lambda: now)


def test_buys_on_signal_during_market_hours(cfg, journal):
    broker = FakeBroker({"AAA": crossover_bars(), "BTC": crossover_bars()})
    engine(cfg, journal, broker, MARKET_HOURS).tick()
    assert [(o[0], o[2]) for o in broker.orders] == [("AAA", "BUY"), ("BTC", "BUY")]
    assert broker.orders[0][3] == 10  # whole shares
    assert broker.orders[1][3] == 10.0502  # $1000 at 99.50, 4 decimals


def test_stocks_skipped_on_weekend_but_crypto_trades(cfg, journal):
    broker = FakeBroker({"AAA": crossover_bars(), "BTC": crossover_bars()})
    engine(cfg, journal, broker, WEEKEND).tick()
    assert [o[0] for o in broker.orders] == ["BTC"]


def test_stop_loss_closes_position(cfg, journal):
    broker = FakeBroker({"AAA": flat_bars(95.0)})
    broker.pos["AAA"] = Position("AAA", "stocks", 10, 100.0)
    engine(cfg, journal, broker, MARKET_HOURS).tick()
    assert broker.orders[0][:4] == ("AAA", "stocks", "SELL", 10)
    assert "stop-loss" in journal.orders_on(MARKET_HOURS.date())[0]["reason"]


def test_stocks_flattened_before_close_and_no_new_entries(cfg, journal):
    broker = FakeBroker({"AAA": flat_bars(100.0), "ZZZ": crossover_bars()})
    cfg.stocks.symbols = ["AAA", "ZZZ"]
    broker.pos["AAA"] = Position("AAA", "stocks", 5, 100.0)
    engine(cfg, journal, broker, NEAR_CLOSE).tick()
    assert [(o[0], o[2]) for o in broker.orders] == [("AAA", "SELL")]


def test_daily_loss_limit_flattens_and_halts(cfg, journal):
    broker = FakeBroker({"BTC": crossover_bars()})
    broker.pos["BTC"] = Position("BTC", "crypto", 0.01, 100.0)
    eng = engine(cfg, journal, broker, MARKET_HOURS)
    eng.tick()  # records start-of-day equity 100000
    broker.orders.clear()
    broker.pos["BTC"] = Position("BTC", "crypto", 0.01, 100.0)
    broker.equity = 99_600
    eng.tick()
    assert [(o[0], o[2]) for o in broker.orders] == [("BTC", "SELL")]
    eng.tick()  # still halted: no new buy despite the signal
    assert len(broker.orders) == 1
    assert "Halts: 1" in build_report(journal, MARKET_HOURS.date())


def test_max_open_positions_blocks_entry(cfg, journal):
    cfg.risk.max_open_positions = 1
    broker = FakeBroker({"AAA": crossover_bars(), "BTC": crossover_bars()})
    engine(cfg, journal, broker, MARKET_HOURS).tick()
    assert [o[0] for o in broker.orders] == ["AAA"]
    assert "Blocked signals: 1" in build_report(journal, MARKET_HOURS.date())


def test_kill_switch_flattens_and_stops(cfg, journal, tmp_path):
    broker = FakeBroker()
    broker.pos["BTC"] = Position("BTC", "crypto", 0.01, 100.0)
    broker.bars_map["BTC"] = flat_bars()
    (tmp_path / "KILL").touch()
    engine(cfg, journal, broker, MARKET_HOURS).run()
    assert [(o[0], o[2]) for o in broker.orders] == [("BTC", "SELL")]


def test_errors_are_journaled_and_loop_continues(cfg, journal, tmp_path):
    class Flaky(FakeBroker):
        calls = 0

        def net_liquidation(self):
            raise ConnectionError("lost connection")

        def sleep(self, seconds):
            (tmp_path / "KILL").touch()

    engine(cfg, journal, Flaky(), MARKET_HOURS).run()
    assert "Errors: 1" in build_report(journal, MARKET_HOURS.date())
