"""`tradebot` command line."""

from __future__ import annotations

import argparse
import logging
import sys

from .config import load_config
from .journal import Journal, now_utc


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="tradebot", description=__doc__)
    parser.add_argument("--config", default="config.yaml")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("check", help="connect to IBKR and print account, positions and config")
    sub.add_parser("run", help="start the trading loop")
    rep = sub.add_parser("report", help="write the daily report")
    rep.add_argument("--day", help="YYYY-MM-DD (default: today, UTC)")

    bt = sub.add_parser("backtest", help="backtest the strategy")
    bt.add_argument("csv", nargs="?", help="CSV with date,open,high,low,close,volume")
    bt.add_argument("--ibkr", metavar="SYMBOL", help="pull history from IBKR instead of a CSV")
    bt.add_argument("--market", choices=["stocks", "crypto"], default="stocks")
    bt.add_argument("--duration", default="30 D", help='IBKR duration, e.g. "30 D"')
    bt.add_argument("--fee", type=float, default=1.0, help="fee per order in dollars")

    args = parser.parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = load_config(args.config)

    if args.command == "check":
        return cmd_check(cfg)
    if args.command == "run":
        from .broker import IBKRBroker
        from .engine import Engine

        Engine(cfg, IBKRBroker(cfg), Journal(cfg.engine.journal_path)).run()
        return 0
    if args.command == "report":
        return cmd_report(cfg, args.day)
    if args.command == "backtest":
        return cmd_backtest(cfg, args)
    return 1


def cmd_check(cfg) -> int:
    from .broker import IBKRBroker

    broker = IBKRBroker(cfg)
    mode = "LIVE" if cfg.ibkr.allow_live else "paper"
    print(f"Connecting to IBKR at {cfg.ibkr.host}:{cfg.ibkr.port} ({mode}) ...")
    try:
        broker.connect()
    except OSError as exc:
        print(f"Could not connect: {exc}\nIs TWS or IB Gateway running with the API enabled "
              f"on port {cfg.ibkr.port}? See README > Setup.", file=sys.stderr)
        return 1
    try:
        print(f"Net liquidation: ${broker.net_liquidation():,.2f}")
        positions = broker.positions()
        print(f"Positions: {len(positions) or 'none'}")
        for p in positions.values():
            print(f"  {p.symbol:6} {p.market:6} qty={p.qty:g} avg_cost={p.avg_cost:.2f}")
        for market in ("stocks", "crypto"):
            m = cfg.market(market)
            if m.enabled:
                for symbol in m.symbols:
                    broker.contract(symbol, market)
                print(f"{market}: {', '.join(m.symbols)} (contracts OK)")
    finally:
        broker.disconnect()
    return 0


def cmd_report(cfg, day_str: str | None) -> int:
    from datetime import date

    from .report import write_report

    day = date.fromisoformat(day_str) if day_str else now_utc().date()
    path = write_report(Journal(cfg.engine.journal_path), day, cfg.engine.reports_dir)
    print(path.read_text())
    print(f"(saved to {path})")
    return 0


def cmd_backtest(cfg, args) -> int:
    from .backtest import load_csv, run_backtest

    if args.ibkr:
        from .broker import IBKRBroker

        broker = IBKRBroker(cfg)
        broker.connect()
        try:
            bars = broker.bars(args.ibkr, args.market, duration=args.duration)
        finally:
            broker.disconnect()
    elif args.csv:
        bars = load_csv(args.csv)
    else:
        print("give a CSV path or --ibkr SYMBOL", file=sys.stderr)
        return 2

    m = cfg.market(args.market)
    result = run_backtest(
        bars, cfg.strategy, cfg.risk, qty_decimals=m.qty_decimals,
        fee_per_order=args.fee, flatten_daily=args.market == "stocks",
    )
    for t in result.trades:
        print(f"{t.entry_time} -> {t.exit_time}  {t.entry:.2f} -> {t.exit:.2f}  "
              f"qty {t.qty:g}  P&L {t.pnl:+.2f}  ({t.reason})")
    print(result.summary())
    return 0


if __name__ == "__main__":
    sys.exit(main())
