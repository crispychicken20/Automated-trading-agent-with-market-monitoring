from datetime import datetime, timezone

import pandas as pd
import pytest

from tradebot.broker import OrderResult, Position
from tradebot.config import (
    Config, EngineConfig, IBKRConfig, MarketConfig, RiskConfig, StrategyConfig,
)
from tradebot.journal import Journal

# Tuesday 2026-09-29, 11:00 ET (market open)
MARKET_HOURS = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc)
# Same day, 15:55 ET (inside the 10-minute flatten window)
NEAR_CLOSE = datetime(2026, 9, 29, 19, 55, tzinfo=timezone.utc)
# Saturday
WEEKEND = datetime(2026, 10, 3, 15, 0, tzinfo=timezone.utc)


def make_bars(closes, volumes=None):
    volumes = volumes or [100] * len(closes)
    return pd.DataFrame({
        "date": pd.date_range("2026-09-29 09:30", periods=len(closes), freq="5min"),
        "open": closes, "high": closes, "low": closes, "close": closes, "volume": volumes,
    })


def crossover_bars():
    """A dip then a steady recovery; the fast EMA crosses the slow one on the last,
    high-volume bar (close 99.4, RSI ~64)."""
    closes = [100.0] * 20 + [100 - 0.2 * i for i in range(1, 16)] + [97 + 0.3 * i for i in range(1, 9)]
    volumes = [100] * (len(closes) - 1) + [400]
    return make_bars(closes, volumes)


def flat_bars(price=100.0, n=41):
    return make_bars([price] * n)


@pytest.fixture
def cfg(tmp_path):
    return Config(
        ibkr=IBKRConfig(),
        stocks=MarketConfig(symbols=["AAA"]),
        crypto=MarketConfig(symbols=["BTC"], exchange="PAXOS", qty_decimals=4),
        strategy=StrategyConfig(),
        risk=RiskConfig(),
        engine=EngineConfig(
            kill_switch_file=str(tmp_path / "KILL"),
            journal_path=str(tmp_path / "journal.sqlite"),
            reports_dir=str(tmp_path / "reports"),
        ),
    )


@pytest.fixture
def journal(cfg):
    return Journal(cfg.engine.journal_path)


class FakeBroker:
    def __init__(self, bars=None, equity=100_000.0):
        self.bars_map = bars or {}
        self.pos: dict[str, Position] = {}
        self.equity = equity
        self.orders = []

    def connect(self): pass
    def disconnect(self): pass
    def sleep(self, seconds): pass

    def bars(self, symbol, market):
        return self.bars_map.get(symbol, make_bars([]))

    def positions(self):
        return dict(self.pos)

    def net_liquidation(self):
        return self.equity

    def place_limit(self, symbol, market, side, qty, limit_price):
        self.orders.append((symbol, market, side, qty, limit_price))
        if side == "BUY":
            self.pos[symbol] = Position(symbol, market, qty, limit_price)
        else:
            self.pos.pop(symbol, None)
        return OrderResult("Filled", qty, limit_price, len(self.orders))


@pytest.fixture
def fake_broker():
    return FakeBroker()
