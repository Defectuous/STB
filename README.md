# STB — Stock Trading Bot

An automated stock trading bot that combines **RSI technical analysis** with **ChatGPT-powered decision making**, executing trades through the **Alpaca Markets API**. The bot only runs during NYSE market hours, including full awareness of holidays and early-close days.

---

## How It Works

```
Every 60 seconds (while market is open):
  For each configured stock:
    1. Fetch historical closing prices  (Alpaca)
    2. Calculate RSI                    (14-period Wilder smoothing)
    3. Ask ChatGPT for a decision       (BUY / SELL / DO NOTHING)
    4. Execute the trade                (Alpaca)
    5. Log the result                   (trade_log.json)
```

**Daily safeguards:**
- Maximum **1 BUY** per day
- Maximum **1 SELL** per day — once a sell executes, all further trading stops for the day
- State resets automatically at midnight

---

## Features

- **RSI Calculation** — Wilder's smoothing method, configurable period and lookback window
- **ChatGPT Advisor** — Sends RSI + ticker to GPT-4o and receives a strict BUY / SELL / DO NOTHING response
- **Alpaca Integration** — Supports market orders, limit orders, stop-loss orders, and take-profit orders
- **NYSE Market Scheduler** — Dynamically computes holidays and early-close days for any year; sleeps precisely until next market open
- **Paper Trading Mode** — Toggle live vs. paper trading without touching any code
- **Persistent Trade State** — Daily buy/sell counts tracked in a local JSON file, auto-reset each day
- **Full Audit Log** — Every decision and trade appended to `trade_log.json`

---

## NYSE Market Schedule

The bot handles all NYSE calendar rules automatically:

| Rule | Detail |
|---|---|
| Regular hours | 9:30 AM – 4:00 PM ET, Monday–Friday |
| Holidays | New Year's Day, MLK Jr. Day, Presidents' Day, Good Friday, Memorial Day, Juneteenth, Independence Day, Labor Day, Thanksgiving, Christmas |
| Early closes (1:00 PM ET) | Day before Thanksgiving, Christmas Eve, day before July 4 (when applicable) |
| Weekend observance | Saturday holidays → Friday, Sunday holidays → Monday |

When the market is closed the bot logs the reason and how long until next open, then sleeps exactly that long.

---

## Requirements

- Python 3.11+
- [Alpaca Markets](https://alpaca.markets) account (free paper trading available)
- [OpenAI](https://platform.openai.com) API key

---

## Installation

```bash
git clone https://github.com/Defectuous/STB.git
cd STB
python -m venv .venv
source .venv/bin/activate      # Windows: .venv\Scripts\activate
pip install -r requirements.txt
```

---

## Configuration

### 1. API Keys

Copy `.env.example` to `.env` and fill in your credentials:

```bash
cp .env.example .env
```

```env
ALPACA_API_KEY=your_alpaca_api_key_here
ALPACA_SECRET_KEY=your_alpaca_secret_key_here
OPENAI_API_KEY=your_openai_api_key_here
```

> **Never commit `.env` to version control.** It is already listed in `.gitignore`.

### 2. Bot Settings (`config.json`)

| Key | Default | Description |
|---|---|---|
| `stocks` | `["AAPL","MSFT",...]` | List of tickers to evaluate each run |
| `rsi_period` | `14` | RSI look-back period |
| `rsi_lookback_days` | `60` | Days of historical data to fetch |
| `wallet_percentage` | `0.75` | Fraction of available cash to spend per BUY |
| `chatgpt_model` | `"gpt-4o"` | OpenAI model to use |
| `paper_trading` | `true` | `true` = paper, `false` = live |
| `log_file` | `"trade_log.json"` | Path to the trade audit log |
| `use_limit_orders` | `true` | Use limit orders instead of market orders for buys |
| `limit_order_offset_pct` | `0.005` | Limit price = current price × (1 + offset) |
| `use_stop_loss` | `true` | Attach a stop-loss to every buy order |
| `stop_loss_pct` | `0.05` | Stop-loss distance below entry price (5%) |
| `use_take_profit` | `true` | Attach a take-profit to every buy order |
| `take_profit_pct` | `0.50` | Take-profit distance above entry price (50%) |

---

## Usage

```bash
python main.py
```

Optional — specify a custom config file:

```bash
python main.py --config path/to/config.json
```

The bot logs to both **stdout** and `stb.log`.

---

## Project Structure

```
STB/
├── main.py               # Entry point — CLI args, config loading, trading loop
├── trader.py             # Core trading logic — RSI → ChatGPT → Alpaca
├── alpaca_client.py      # Alpaca API wrapper (account, positions, orders, history)
├── chatgpt_advisor.py    # OpenAI wrapper — returns BUY / SELL / DO NOTHING
├── rsi_calculator.py     # RSI calculation (Wilder's smoothing)
├── market_schedule.py    # NYSE market hours, holidays, early-close detection
├── trade_state.py        # Daily trade state persistence (auto-resets at midnight)
├── config.json           # Bot configuration
├── requirements.txt      # Python dependencies
├── .env.example          # API key template
└── .gitignore
```

---

## Disclaimer

This bot is provided for educational and research purposes only. Automated trading carries significant financial risk. Past performance does not guarantee future results. **Use live trading at your own risk.**
