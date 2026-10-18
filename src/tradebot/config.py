"""Load and validate config.yaml into typed settings."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import yaml

LIVE_PORTS = {7496, 4001}


@dataclass
class IBKRConfig:
    host: str = "127.0.0.1"
    port: int = 7497
    client_id: int = 17
    allow_live: bool = False


@dataclass
class MarketConfig:
    enabled: bool = True
    symbols: list[str] = field(default_factory=list)
    exchange: str = "SMART"
    currency: str = "USD"
    bar_size: str = "5 mins"
    history: str = "2 D"
    flatten_minutes_before_close: int = 10
    qty_decimals: int = 0


@dataclass
class StrategyConfig:
    fast_ema: int = 9
    slow_ema: int = 21
    volume_lookback: int = 20
    volume_multiplier: float = 1.5
    rsi_period: int = 14
    rsi_max_entry: float = 70


@dataclass
class RiskConfig:
    max_total_budget_usd: float = 4000
    max_position_usd: float = 1000
    max_open_positions: int = 4
    stop_loss_pct: float = 1.5
    take_profit_pct: float = 3.0
    daily_loss_limit_usd: float = 300
    max_trades_per_day: int = 20
    max_stock_day_trades_5d: int = 3
    slippage_bps: float = 10


@dataclass
class EngineConfig:
    poll_seconds: int = 60
    kill_switch_file: str = "KILL"
    journal_path: str = "data/journal.sqlite"
    reports_dir: str = "reports"


@dataclass
class Config:
    ibkr: IBKRConfig
    stocks: MarketConfig
    crypto: MarketConfig
    strategy: StrategyConfig
    risk: RiskConfig
    engine: EngineConfig

    def market(self, name: str) -> MarketConfig:
        return {"stocks": self.stocks, "crypto": self.crypto}[name]


def load_config(path: str | Path = "config.yaml") -> Config:
    raw = yaml.safe_load(Path(path).read_text()) or {}
    markets = raw.get("markets", {})
    cfg = Config(
        ibkr=IBKRConfig(**raw.get("ibkr", {})),
        stocks=MarketConfig(**markets.get("stocks", {})),
        crypto=MarketConfig(**markets.get("crypto", {})),
        strategy=StrategyConfig(**raw.get("strategy", {})),
        risk=RiskConfig(**raw.get("risk", {})),
        engine=EngineConfig(**raw.get("engine", {})),
    )
    validate(cfg)
    return cfg


def validate(cfg: Config) -> None:
    errors = []
    if cfg.ibkr.port in LIVE_PORTS and not cfg.ibkr.allow_live:
        errors.append(
            f"port {cfg.ibkr.port} is a LIVE trading port; set ibkr.allow_live: true to use it"
        )
    s, r = cfg.strategy, cfg.risk
    if s.fast_ema >= s.slow_ema:
        errors.append("strategy.fast_ema must be smaller than strategy.slow_ema")
    for name in (
        "max_total_budget_usd", "max_position_usd", "stop_loss_pct", "take_profit_pct",
        "daily_loss_limit_usd",
    ):
        if getattr(r, name) <= 0:
            errors.append(f"risk.{name} must be positive")
    if r.max_position_usd > r.max_total_budget_usd:
        errors.append("risk.max_position_usd cannot be larger than risk.max_total_budget_usd")
    if r.max_open_positions < 1:
        errors.append("risk.max_open_positions must be at least 1")
    if errors:
        raise ValueError("Invalid config:\n  - " + "\n  - ".join(errors))
