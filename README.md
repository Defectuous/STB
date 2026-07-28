# Simple Trading Bot (STB)

A modular Python trading bot built on [alpaca-py](https://github.com/alpacahq/alpaca-py). Strategies are pluggable — each one implements a small interface and gets registered by name in `config.json`. Ships with an initial moving-average-crossover strategy (`strategy/mac_strategy.py`, per `MAC_strat.md`).

## How it's organized

```
STB/
├── main.py              # CLI entry point — live/paper trading loop
├── backtest.py           # walk-forward backtest over stored historical bars
├── screener.py            # scans the tradable universe for SMA crossover setups
├── engine.py              # orchestrates broker + strategy + storage + scheduler + notifier
├── config_loader.py       # loads config.json, instantiates the configured strategy
├── state.py                # tracks last-acted-on bar per symbol (avoids duplicate actions)
├── broker/
│   └── alpaca_broker.py   # all Alpaca API calls live here (trading + market data + asset universe)
├── strategy/
│   ├── base.py             # Strategy interface (Action, Signal, Strategy ABC)
│   ├── indicators.py       # shared SMA/crossover math (used by mac_strategy.py and screener.py)
│   └── mac_strategy.py     # Moving Average Crossover strategy
├── scheduler/
│   └── nyse_calendar.py    # NYSE holiday/hours calendar, no API calls needed
├── notify/
│   └── discord.py          # optional Discord webhook alerts on BUY/SELL
├── storage/
│   └── db.py                # SQLite bar history (accumulates automatically; used for backtesting)
└── tests/
    ├── test_mac_strategy.py
    └── test_screener.py
```

Every module only depends on the interfaces above it — `engine.py` never imports `alpaca-py` directly, and strategies never import the broker. To add a new strategy: implement `Strategy` in a new file under `strategy/`, then add one line to `strategy/__init__.py`'s `STRATEGY_REGISTRY`.

## Setup

```bash
pip install -r requirements.txt
cp .env.example .env        # fill in ALPACA_API_KEY / ALPACA_SECRET_KEY
cp config.example.json config.json   # already done; edit symbols/params as needed
```

Get free paper-trading API keys at [alpaca.markets](https://alpaca.markets).

## Testing the strategy

**1. Unit tests** (fast, no network):

```bash
python -m pytest tests/ -v
```

**2. Backtest against historical data** (backfills SQLite from Alpaca automatically, then simulates):

```bash
python backtest.py --symbol SPY --start 2022-01-01
```

Prints every simulated trade plus strategy return vs. buy-and-hold. Data is cached in `stb_data.sqlite3` so re-runs and future backtests don't re-fetch what's already stored.

**3. Dry-run against live data** (computes today's signal, places no order):

```bash
python main.py --once --dry-run
```

**4. One real paper-trading pass:**

```bash
python main.py --once
```

**5. Continuous, market-hours-aware loop:**

```bash
python main.py
```

## Running as a systemd service (Linux / Raspberry Pi)

`deploy/stb@.service` is a systemd *template* unit — the instance name after the `@` is the Linux user the bot runs as, and `%h`/`%i` expand to that user's home directory, so the same unit file works for any user without editing it.

Assumes the repo is checked out at `~<user>/STB` with a virtualenv at `~<user>/STB/.venv` (see Setup above, run as that user):

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
cp .env.example .env && $EDITOR .env
```

Then install and start the service:

```bash
deploy/install.sh <username>   # e.g. deploy/install.sh defectuous
```

Or by hand:

```bash
sudo cp deploy/stb@.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now stb@<username>.service
```

Useful commands:

```bash
sudo systemctl status stb@<username>.service
journalctl -u stb@<username>.service -f     # follow logs (also written to stb.log in the repo dir)
sudo systemctl stop stb@<username>.service
sudo systemctl restart stb@<username>.service
```

The unit restarts the bot on crash (30s backoff) and starts on boot. It runs `main.py` (continuous, market-hours-aware loop) — flip `paper_trading` to `false` in `config.json` only once you've validated behavior in paper mode.

## Screener

`screener.py` scans Alpaca's tradable US-equity universe (active, marginable, NASDAQ/NYSE/AMEX only — OTC excluded) for stocks whose 20-period SMA is crossing or about to cross above their 50-period SMA, restricted to a $1-$20 price band, >1,000,000 30-day average volume, and price above the 200-period SMA (macro uptrend filter). It shares its crossover math with `strategy/mac_strategy.py` via `strategy/indicators.py`, so "what counts as a cross" is defined once.

```bash
python screener.py                          # full universe, default filters
python screener.py --limit 300               # quick pass over a subset, for testing
python screener.py --min-price 2 --max-price 15 --min-volume 2000000
```

Output is a console table with `Status` (`SIGNAL_TRIGGERED` sorted before `WATCHLIST`) and a `Max_Whole_Shares` column sized off a fixed capital base (`--capital`, default $300). Symbols with insufficient history, missing data, or that fail any filter are silently skipped rather than crashing the run. See `python screener.py --help` for every tunable (SMA periods, lookback window, batch size, exchanges).

## Configuration (`config.json`)

| Key | Default | Description |
|---|---|---|
| `paper_trading` | `true` | `true` = paper, `false` = live (flip only when ready) |
| `poll_interval_seconds` | `300` | How often `main.py`'s continuous loop re-checks while the market is open |
| `db_file` | `stb_data.sqlite3` | SQLite file where every fetched bar is stored, for backtesting |
| `state_file` | `state.json` | Per-symbol last-acted-on-bar tracking |
| `strategy.name` | `mac_strategy` | Must match a key in `strategy.STRATEGY_REGISTRY` |
| `strategy.params.symbols` | `["SPY"]` | Symbols to trade |
| `strategy.params.fast_period` | `20` | Fast SMA length |
| `strategy.params.slow_period` | `50` | Slow SMA length |
| `strategy.params.max_loss_pct` | `0.05` | Hard stop-loss distance from entry |
| `strategy.params.allocation_pct` | `1.00` | Fraction of buying power spent per BUY |

Secrets (`ALPACA_API_KEY`, `ALPACA_SECRET_KEY`, optional `DISCORD_WEBHOOK_URL`) live in `.env`, never in `config.json`.

## Strategy: Moving Average Crossover

Per `MAC_strat.md`: BUY on a golden cross (fast SMA crosses above slow SMA) when flat; SELL on a death cross (fast SMA crosses below slow SMA) when holding. Every BUY carries a resting stop-loss order `max_loss_pct` below entry, submitted alongside the buy via Alpaca's one-triggers-other (OTO) order class.

## Disclaimer

Educational/research use only. Automated trading carries significant financial risk. Use live trading at your own risk.
