# Automated Trading Agent with Market Monitoring

TESTING THE UTILITY OF ANTHROPICS Opus 5.5: Creating an application that will analize  markets utilizing synthetic data.

A rules-based day-trading bot for **Interactive Brokers** that trades **US stocks and crypto**,
supervised by an AI agent (Claude) that checks on it on a schedule.

> **Paper trading by default.** The bot refuses to connect to IBKR's live ports unless you
> explicitly set `ibkr.allow_live: true`. Nothing here is financial advice, and no strategy is
> guaranteed to make money. Backtest and paper trade for weeks before risking real funds.

## How it works

The work is split into two parts:

| Part | What it does | Runs |
|------|--------------|------|
| **Trading engine** (`tradebot run`) | Pulls bars from IBKR, computes signals, enforces risk limits, places orders, logs everything | Continuously, every `poll_seconds` |
| **Supervising agent** (Claude) | Reads the journal/report, summarizes performance, flags problems, proposes parameter changes within approved bounds | On a schedule (e.g. hourly or end of day) |

Trade decisions are made by deterministic code, **not** by the LLM. The agent watches and advises;
its limits are in [`CLAUDE.md`](CLAUDE.md).

```
IBKR TWS / Gateway (paper)
        │  bars, positions, fills
        ▼
┌────────────────── tradebot run ───────────────────┐
│ strategy.py  → entry/exit signals                 │
│ risk.py      → sizing, limits, kill switch        │
│ engine.py    → loop, market hours, orders         │
│ journal.py   → SQLite log (data/journal.sqlite)   │
└───────────────────────────────────────────────────┘
        │
        ▼
tradebot report → reports/YYYY-MM-DD.md → read by the agent
```

### Strategy (default)

- **Entry (long only):** the fast EMA (9) crosses above the slow EMA (21), bar volume is at least
  1.5× the recent average, and RSI is below 70.
- **Exit:** stop-loss (−1.5%), take-profit (+3%), or the fast EMA dropping back below the slow EMA.
- **Stocks** trade only during regular US hours (9:30–16:00 ET) and are flattened shortly before
  the close, so nothing is held overnight. **Crypto** (BTC, ETH via Paxos) trades 24/7.

All numbers are in `config.yaml`.

### Risk controls (enforced in code)

- Max dollars per position and max number of open positions
- **Daily loss limit**: when breached, the bot stops opening positions and closes everything
- Max trades per day
- Pattern-day-trader guard for stocks (max day trades in 5 business days)
- **Kill switch**: create a file named `KILL` in the project folder and the bot flattens all
  positions and exits
- Orders are marketable **limit** orders (price ± `slippage_bps`), never unbounded market orders

Stop-loss and take-profit are checked by the bot each poll. They are **not** resting orders at
IBKR, so they only work while the bot is running.

## Setup

1. **IBKR paper account.** Log in to TWS or IB Gateway with your *paper* credentials.
2. **Enable the API.** In TWS: *File → Global Configuration → API → Settings*. Check
   *Enable ActiveX and Socket Clients* and note the port (TWS paper `7497`, Gateway paper `4002`).
   Add `127.0.0.1` to trusted IPs.
3. **Market data.** Stocks need a US market data subscription on the account (paper accounts share
   the live account's subscriptions). Crypto requires crypto trading permission (Paxos).
4. **Install** (Python 3.10+):

   ```bash
   python3 -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

5. **Configure.** Edit `config.yaml` with your watchlist, position size and limits.

## Usage

```bash
tradebot check                  # connect to IBKR, print account value, positions and config
tradebot backtest data/SPY.csv  # backtest the strategy on a CSV of bars
tradebot backtest --ibkr SPY --market stocks --duration "30 D"   # or pull history from IBKR
tradebot run                    # start the trading loop (Ctrl+C to stop)
tradebot report                 # write today's summary to reports/YYYY-MM-DD.md
touch KILL                      # emergency stop: flatten everything and exit
```

A backtest CSV needs the columns `date,open,high,low,close,volume`.

## Having an agent supervise it

With Claude Code, run the bot in one terminal and schedule the agent in another, for example:

```
/loop 60m Run `tradebot report`, read today's report and summarize it for me. Flag anything that breaks the rules in CLAUDE.md.
```

The agent's permissions (what it may and may not change) are in `CLAUDE.md`.

## Project layout

```
config.yaml            all parameters and hard limits
src/tradebot/
  config.py            config loading and validation (live-port guard)
  strategy.py          indicators and entry/exit signals
  risk.py              sizing, daily loss limit, trade caps, kill switch
  broker.py            IBKR connection (ib_async): contracts, bars, positions, orders
  engine.py            the main trading loop
  journal.py           SQLite journal
  report.py            daily markdown report for the agent
  backtest.py          historical simulation using the same strategy code
  cli.py               `tradebot` command
tests/                 unit tests (no IBKR connection needed)
```

## Tests

```bash
pytest
```
