"""SQLite journal of every decision, order and equity snapshot.

The supervising agent reads this (via `tradebot report`) to see what happened.
"""

from __future__ import annotations

import json
import sqlite3
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    ts TEXT NOT NULL, kind TEXT NOT NULL, symbol TEXT, message TEXT, data TEXT
);
CREATE TABLE IF NOT EXISTS orders (
    ts TEXT NOT NULL, market TEXT NOT NULL, symbol TEXT NOT NULL, side TEXT NOT NULL,
    qty REAL NOT NULL, limit_price REAL NOT NULL, status TEXT, fill_price REAL,
    reason TEXT, order_id INTEGER
);
CREATE TABLE IF NOT EXISTS equity (
    ts TEXT NOT NULL, day TEXT NOT NULL, net_liquidation REAL NOT NULL
);
"""


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


class Journal:
    def __init__(self, path: str | Path, clock=now_utc):
        self.clock = clock
        Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(str(path))
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def event(self, kind: str, message: str, symbol: str | None = None, **data) -> None:
        self.db.execute(
            "INSERT INTO events VALUES (?, ?, ?, ?, ?)",
            (self.clock().isoformat(), kind, symbol, message, json.dumps(data, default=str)),
        )
        self.db.commit()

    def order(self, market, symbol, side, qty, limit_price, status, fill_price, reason, order_id):
        self.db.execute(
            "INSERT INTO orders VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (self.clock().isoformat(), market, symbol, side, qty, limit_price, status,
             fill_price, reason, order_id),
        )
        self.db.commit()

    def equity(self, net_liquidation: float, day: date) -> None:
        self.db.execute(
            "INSERT INTO equity VALUES (?, ?, ?)",
            (self.clock().isoformat(), day.isoformat(), net_liquidation),
        )
        self.db.commit()

    def start_of_day_equity(self, day: date) -> float | None:
        row = self.db.execute(
            "SELECT net_liquidation FROM equity WHERE day = ? ORDER BY ts LIMIT 1",
            (day.isoformat(),),
        ).fetchone()
        return row[0] if row else None

    def orders_on(self, day: date) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM orders WHERE substr(ts, 1, 10) = ? ORDER BY ts", (day.isoformat(),)
        ).fetchall()

    def events_on(self, day: date) -> list[sqlite3.Row]:
        return self.db.execute(
            "SELECT * FROM events WHERE substr(ts, 1, 10) = ? ORDER BY ts", (day.isoformat(),)
        ).fetchall()

    def filled_trade_count(self, day: date) -> int:
        return sum(1 for o in self.orders_on(day) if o["status"] == "Filled")

    def stock_day_trades(self, since: date) -> int:
        """Count stock round trips (buy and sell of the same symbol on the same day)."""
        rows = self.db.execute(
            "SELECT substr(ts, 1, 10) AS d, symbol, side FROM orders "
            "WHERE market = 'stocks' AND status = 'Filled' AND substr(ts, 1, 10) >= ?",
            (since.isoformat(),),
        ).fetchall()
        sides: dict[tuple[str, str], set[str]] = {}
        for r in rows:
            sides.setdefault((r["d"], r["symbol"]), set()).add(r["side"])
        return sum(1 for s in sides.values() if {"BUY", "SELL"} <= s)

    def business_days_ago(self, today: date, n: int) -> date:
        d = today
        while n > 0:
            d -= timedelta(days=1)
            if d.weekday() < 5:
                n -= 1
        return d
