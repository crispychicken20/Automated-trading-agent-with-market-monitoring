import pytest

from tradebot.config import IBKRConfig, validate
from tradebot.risk import AccountState, RiskManager


def state(**kw):
    base = dict(open_positions=0, trades_today=0, daily_pnl=0.0, stock_day_trades_5d=0)
    return AccountState(**{**base, **kw})


def test_allows_a_normal_entry(cfg):
    assert RiskManager(cfg.risk).can_open(state(), "stocks") == (True, "ok")


@pytest.mark.parametrize("kw,market,text", [
    (dict(daily_pnl=-300), "crypto", "daily loss"),
    (dict(open_positions=4), "crypto", "max open positions"),
    (dict(trades_today=19), "crypto", "max trades"),
    (dict(stock_day_trades_5d=3), "stocks", "pattern-day-trader"),
])
def test_blocks_entries_over_limits(cfg, kw, market, text):
    ok, why = RiskManager(cfg.risk).can_open(state(**kw), market)
    assert not ok and text in why


def test_pdt_guard_does_not_apply_to_crypto(cfg):
    assert RiskManager(cfg.risk).can_open(state(stock_day_trades_5d=3), "crypto")[0]


def test_position_size(cfg):
    rm = RiskManager(cfg.risk)
    assert rm.position_size(333.0) == 3
    assert rm.position_size(2000.0) == 0
    assert rm.position_size(65432.1, qty_decimals=4) == 0.0152


def test_limit_price_is_marketable(cfg):
    rm = RiskManager(cfg.risk)
    assert rm.limit_price("BUY", 100.0) == 100.10
    assert rm.limit_price("SELL", 100.0) == 99.90


def test_live_port_refused_without_opt_in(cfg):
    cfg.ibkr = IBKRConfig(port=7496)
    with pytest.raises(ValueError, match="LIVE"):
        validate(cfg)
    cfg.ibkr = IBKRConfig(port=7496, allow_live=True)
    validate(cfg)


def test_repo_config_is_valid_and_paper():
    from tradebot.config import load_config

    cfg = load_config("config.yaml")
    assert not cfg.ibkr.allow_live and cfg.ibkr.port in (7497, 4002)
