"""The trading loop: fetch bars, check signals and limits, place orders, log everything."""

from __future__ import annotations

import logging
import traceback
from datetime import datetime, time, timedelta
from pathlib import Path
from typing import Callable
from zoneinfo import ZoneInfo

from .broker import Broker, Position
from .config import Config
from .journal import Journal, now_utc
from .risk import AccountState, RiskManager
from .strategy import add_indicators, entry_signal, exit_signal

log = logging.getLogger("tradebot")

ET = ZoneInfo("America/New_York")
MARKET_OPEN = time(9, 30)
MARKET_CLOSE = time(16, 0)


def stock_market_open(now: datetime) -> bool:
    """Regular US trading hours. Exchange holidays are not modelled; IBKR simply returns
    no fresh bars on those days, so no crossover can trigger."""
    et = now.astimezone(ET)
    return et.weekday() < 5 and MARKET_OPEN <= et.time() < MARKET_CLOSE


def in_flatten_window(now: datetime, minutes: int) -> bool:
    et = now.astimezone(ET)
    cutoff = (datetime.combine(et.date(), MARKET_CLOSE) - timedelta(minutes=minutes)).time()
    return et.weekday() < 5 and cutoff <= et.time() < MARKET_CLOSE


class Engine:
    def __init__(
        self,
        cfg: Config,
        broker: Broker,
        journal: Journal,
        clock: Callable[[], datetime] = now_utc,
    ):
        self.cfg = cfg
        self.broker = broker
        self.journal = journal
        self.journal.clock = clock  # journal days must match the engine's trading day
        self.risk = RiskManager(cfg.risk)
        self.clock = clock
        self.halted_day = None

    # ---- main loop -------------------------------------------------------

    def run(self) -> None:
        self.broker.connect()
        self.journal.event("start", "engine started", config_port=self.cfg.ibkr.port)
        log.info("engine started; create a file named %s to stop", self.cfg.engine.kill_switch_file)
        try:
            while True:
                if self.kill_switch_on():
                    self.flatten_all("kill switch")
                    self.journal.event("stop", "kill switch file found; flattened and stopped")
                    log.warning("kill switch: flattened all positions, stopping")
                    return
                try:
                    self.tick()
                except Exception as exc:  # keep running; errors are journaled for the agent
                    self.journal.event("error", repr(exc), traceback=traceback.format_exc())
                    log.exception("tick failed")
                self.broker.sleep(self.cfg.engine.poll_seconds)
        except KeyboardInterrupt:
            self.journal.event("stop", "stopped by user (positions left open)")
            log.info("stopped by user")
        finally:
            self.broker.disconnect()

    def kill_switch_on(self) -> bool:
        return Path(self.cfg.engine.kill_switch_file).exists()

    # ---- one iteration ---------------------------------------------------

    def tick(self) -> None:
        now = self.clock()
        day = now.date()

        equity = self.broker.net_liquidation()
        self.journal.equity(equity, day)
        daily_pnl = equity - (self.journal.start_of_day_equity(day) or equity)
        positions = self.broker.positions()

        if self.risk.daily_loss_breached(daily_pnl):
            if self.halted_day != day:
                self.halted_day = day
                self.journal.event(
                    "halt", f"daily loss limit breached ({daily_pnl:.2f}); flattening", pnl=daily_pnl
                )
                log.warning("daily loss limit breached (%.2f), flattening", daily_pnl)
            self.flatten_all("daily loss limit", positions)
            return

        open_count = len(positions)
        for market in ("stocks", "crypto"):
            mcfg = self.cfg.market(market)
            if not mcfg.enabled:
                continue
            for symbol in mcfg.symbols:
                pos = positions.get(symbol)
                if pos and pos.market != market:
                    pos = None
                opened = self.handle_symbol(symbol, market, pos, now, day, daily_pnl, open_count)
                open_count += opened

    def handle_symbol(self, symbol, market, pos, now, day, daily_pnl, open_count) -> int:
        """Returns the change in open position count (1 if a position was opened, -1 if closed)."""
        mcfg = self.cfg.market(market)
        if market == "stocks":
            if not stock_market_open(now):
                return 0
            if in_flatten_window(now, mcfg.flatten_minutes_before_close):
                if pos:
                    return -1 if self.close(pos, "end-of-day flatten") else 0
                return 0

        bars = self.broker.bars(symbol, market)
        if bars.empty:
            return 0
        df = add_indicators(bars, self.cfg.strategy)
        price = float(df["close"].iloc[-1])

        if pos:
            sig = exit_signal(df, pos.avg_cost, self.cfg.strategy, self.cfg.risk)
            if sig.action == "sell":
                return -1 if self.close(pos, sig.reason, price) else 0
            return 0

        sig = entry_signal(df, self.cfg.strategy)
        if sig.action != "buy":
            return 0
        state = AccountState(
            open_positions=open_count,
            trades_today=self.journal.filled_trade_count(day),
            daily_pnl=daily_pnl,
            stock_day_trades_5d=self.journal.stock_day_trades(
                self.journal.business_days_ago(day, 4)
            ),
        )
        ok, why = self.risk.can_open(state, market)
        if not ok:
            self.journal.event("blocked", f"buy signal blocked: {why}", symbol, signal=sig.reason)
            return 0
        # Size on the limit price so the position never exceeds max_position_usd.
        qty = self.risk.position_size(self.risk.limit_price("BUY", price), mcfg.qty_decimals)
        if qty <= 0:
            self.journal.event("blocked", f"position size is zero at price {price}", symbol)
            return 0
        return 1 if self.order(symbol, market, "BUY", qty, price, sig.reason) else 0

    # ---- orders ----------------------------------------------------------

    def close(self, pos: Position, reason: str, price: float | None = None) -> bool:
        if price is None:
            bars = self.broker.bars(pos.symbol, pos.market)
            price = float(bars["close"].iloc[-1]) if not bars.empty else pos.avg_cost
        return self.order(pos.symbol, pos.market, "SELL", abs(pos.qty), price, reason)

    def order(self, symbol, market, side, qty, price, reason) -> bool:
        limit = self.risk.limit_price(side, price)
        log.info("%s %s %s @ %.2f (%s)", side, qty, symbol, limit, reason)
        result = self.broker.place_limit(symbol, market, side, qty, limit)
        self.journal.order(
            market, symbol, side, qty, limit, result.status, result.fill_price, reason,
            result.order_id,
        )
        return result.status == "Filled"

    def flatten_all(self, reason: str, positions: dict[str, Position] | None = None) -> None:
        positions = self.broker.positions() if positions is None else positions
        for pos in positions.values():
            if pos.market == "crypto" or stock_market_open(self.clock()):
                self.close(pos, reason)
            else:
                self.journal.event(
                    "blocked", "cannot flatten stock outside market hours", pos.symbol
                )
