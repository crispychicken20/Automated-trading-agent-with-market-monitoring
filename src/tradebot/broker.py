"""Interactive Brokers connection via ib_async.

The engine only talks to the small `Broker` interface below, so tests can swap in a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

import pandas as pd

from .config import Config

BAR_COLUMNS = ["date", "open", "high", "low", "close", "volume"]


@dataclass
class Position:
    symbol: str
    market: str  # "stocks" or "crypto"
    qty: float
    avg_cost: float


@dataclass
class OrderResult:
    status: str  # "Filled", "Cancelled", ...
    filled_qty: float
    fill_price: float | None
    order_id: int | None


class Broker(Protocol):
    def connect(self) -> None: ...
    def disconnect(self) -> None: ...
    def bars(self, symbol: str, market: str) -> pd.DataFrame: ...
    def positions(self) -> dict[str, Position]: ...
    def net_liquidation(self) -> float: ...
    def place_limit(
        self, symbol: str, market: str, side: str, qty: float, limit_price: float
    ) -> OrderResult: ...
    def sleep(self, seconds: float) -> None: ...


class IBKRBroker:
    """Talks to a running TWS or IB Gateway."""

    SEC_TYPES = {"STK": "stocks", "CRYPTO": "crypto"}

    def __init__(self, cfg: Config, order_timeout: float = 30):
        from ib_async import IB

        self.cfg = cfg
        self.ib = IB()
        self.order_timeout = order_timeout
        self._contracts: dict[tuple[str, str], object] = {}

    def connect(self) -> None:
        c = self.cfg.ibkr
        self.ib.connect(c.host, c.port, clientId=c.client_id, timeout=15)

    def disconnect(self) -> None:
        if self.ib.isConnected():
            self.ib.disconnect()

    def sleep(self, seconds: float) -> None:
        # ib.sleep keeps the IB event loop running while we wait.
        self.ib.sleep(seconds)

    def contract(self, symbol: str, market: str):
        key = (symbol, market)
        if key not in self._contracts:
            from ib_async import Crypto, Stock

            m = self.cfg.market(market)
            cls = Stock if market == "stocks" else Crypto
            contract = cls(symbol, m.exchange, m.currency)
            qualified = self.ib.qualifyContracts(contract)
            if not qualified:
                raise RuntimeError(f"IBKR could not find contract {symbol} ({market})")
            self._contracts[key] = qualified[0]
        return self._contracts[key]

    def bars(self, symbol: str, market: str, duration: str | None = None) -> pd.DataFrame:
        from ib_async import util

        m = self.cfg.market(market)
        raw = self.ib.reqHistoricalData(
            self.contract(symbol, market),
            endDateTime="",
            durationStr=duration or m.history,
            barSizeSetting=m.bar_size,
            whatToShow="TRADES" if market == "stocks" else "AGGTRADES",
            useRTH=market == "stocks",
        )
        df = util.df(raw)
        if df is None or df.empty:
            return pd.DataFrame(columns=BAR_COLUMNS)
        return df[BAR_COLUMNS].reset_index(drop=True)

    def positions(self) -> dict[str, Position]:
        result = {}
        for p in self.ib.positions():
            market = self.SEC_TYPES.get(p.contract.secType)
            if market and p.position != 0:
                result[p.contract.symbol] = Position(
                    p.contract.symbol, market, float(p.position), float(p.avgCost)
                )
        return result

    def net_liquidation(self) -> float:
        values = {
            v.currency: float(v.value)
            for v in self.ib.accountValues()
            if v.tag == "NetLiquidation" and v.currency in ("USD", "BASE")
        }
        if not values:
            raise RuntimeError("NetLiquidation not available from IBKR yet")
        return values.get("USD", values.get("BASE"))

    def place_limit(
        self, symbol: str, market: str, side: str, qty: float, limit_price: float
    ) -> OrderResult:
        from ib_async import LimitOrder

        # IBKR crypto supports IOC but not DAY; stocks use DAY and we cancel if unfilled.
        tif = "IOC" if market == "crypto" else "DAY"
        order = LimitOrder(side, qty, limit_price, tif=tif)
        trade = self.ib.placeOrder(self.contract(symbol, market), order)
        waited = 0.0
        while not trade.isDone() and waited < self.order_timeout:
            self.ib.sleep(1)
            waited += 1
        if not trade.isDone():
            self.ib.cancelOrder(order)
            self.ib.sleep(2)
        status = trade.orderStatus
        return OrderResult(
            status=status.status,
            filled_qty=float(status.filled),
            fill_price=float(status.avgFillPrice) if status.filled else None,
            order_id=order.orderId,
        )
