"""Daily markdown report, written for the supervising agent (and you) to read."""

from __future__ import annotations

from collections import Counter
from datetime import date
from pathlib import Path

from .journal import Journal


def build_report(journal: Journal, day: date) -> str:
    orders = journal.orders_on(day)
    events = journal.events_on(day)
    start = journal.start_of_day_equity(day)
    last = journal.db.execute(
        "SELECT net_liquidation FROM equity WHERE day = ? ORDER BY ts DESC LIMIT 1",
        (day.isoformat(),),
    ).fetchone()

    lines = [f"# Trading report {day.isoformat()}", ""]
    if start is not None and last is not None:
        pnl = last[0] - start
        lines += [
            f"- Start-of-day equity: ${start:,.2f}",
            f"- Latest equity: ${last[0]:,.2f}",
            f"- Day P&L: ${pnl:+,.2f}",
        ]
    else:
        lines.append("- No equity snapshots today (engine not running?)")

    kinds = Counter(e["kind"] for e in events)
    filled = [o for o in orders if o["status"] == "Filled"]
    lines += [
        f"- Orders: {len(orders)} placed, {len(filled)} filled",
        f"- Blocked signals: {kinds.get('blocked', 0)}",
        f"- Errors: {kinds.get('error', 0)}",
        f"- Halts: {kinds.get('halt', 0)}",
        "",
        "## Orders",
        "",
    ]
    if orders:
        lines += [
            "| Time (UTC) | Market | Symbol | Side | Qty | Limit | Status | Fill | Reason |",
            "|---|---|---|---|---|---|---|---|---|",
        ]
        for o in orders:
            fill = f"{o['fill_price']:.2f}" if o["fill_price"] else "-"
            lines.append(
                f"| {o['ts'][11:19]} | {o['market']} | {o['symbol']} | {o['side']} | "
                f"{o['qty']:g} | {o['limit_price']:.2f} | {o['status']} | {fill} | {o['reason']} |"
            )
    else:
        lines.append("No orders today.")

    notable = [e for e in events if e["kind"] in ("error", "halt", "blocked", "start", "stop")]
    lines += ["", "## Notable events", ""]
    if notable:
        for e in notable[-50:]:
            sym = f" {e['symbol']}" if e["symbol"] else ""
            lines.append(f"- {e['ts'][11:19]} **{e['kind']}**{sym}: {e['message']}")
    else:
        lines.append("None.")
    return "\n".join(lines) + "\n"


def write_report(journal: Journal, day: date, reports_dir: str | Path) -> Path:
    path = Path(reports_dir) / f"{day.isoformat()}.md"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(build_report(journal, day))
    return path
