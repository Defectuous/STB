"""
main.py
Entry point for the Stock Trading Bot (STB).

Usage:
    python main.py
    python main.py --config path/to/config.json

The bot will:
  1. Load configuration from config.json (or the path you specify).
  2. Verify that required environment variables are set (.env file).
  3. Run the trading loop — evaluating each configured stock,
     computing RSI, consulting ChatGPT, and executing trades via Alpaca.
"""

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%Y-%m-%d %H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler("stb.log"),
    ],
)
log = logging.getLogger(__name__)


# ── CLI args ──────────────────────────────────────────────────────────────────
def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Stock Trading Bot — RSI + ChatGPT + Alpaca"
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Path to the JSON config file (default: config.json)",
    )
    return parser.parse_args()


# ── Config loader ─────────────────────────────────────────────────────────────
def _load_config(path: str) -> dict:
    config_path = Path(path)
    if not config_path.exists():
        log.error("Config file not found: %s", config_path.resolve())
        sys.exit(1)
    with open(config_path, "r") as f:
        return json.load(f)


# ── Env validation ────────────────────────────────────────────────────────────
def _check_env() -> None:
    required = ["ALPACA_API_KEY", "ALPACA_SECRET_KEY", "OPENAI_API_KEY"]
    missing = [k for k in required if not os.getenv(k)]
    if missing:
        log.error(
            "Missing required environment variables: %s\n"
            "Copy .env.example to .env and fill in your API keys.",
            ", ".join(missing),
        )
        sys.exit(1)


# ── Main ──────────────────────────────────────────────────────────────────────
def main() -> None:
    args = _parse_args()

    log.info("=" * 50)
    log.info("  Stock Trading Bot (STB) starting")
    log.info("=" * 50)

    _check_env()
    config = _load_config(args.config)

    log.info("Loaded config: %s", args.config)
    log.info("Stocks to evaluate: %s", config["stocks"])
    log.info("RSI period: %d  |  Lookback: %d days", config["rsi_period"], config["rsi_lookback_days"])
    log.info("Wallet spend limit: %.0f%%", config["wallet_percentage"] * 100)
    log.info("Paper trading: %s", config["paper_trading"])
    log.info("ChatGPT model: %s", config["chatgpt_model"])
    log.info("-" * 50)

    import trader
    import market_schedule

    loop_seconds = 60
    run_count = 0

    log.info("Scheduler active — bot will only trade during NYSE market hours.")

    while True:
        try:
            if not market_schedule.is_market_open():
                status = market_schedule.market_status_str()
                wait_secs = market_schedule.seconds_until_market_open()
                log.info("Market %s", status)
                log.info(
                    "Next open in %.0f s (%.2f h) — sleeping ...",
                    wait_secs, wait_secs / 3600,
                )
                time.sleep(max(wait_secs, 60))  # minimum 60 s to avoid tight spin
                continue

            run_count += 1
            log.info("--- Run #%d | Market %s ---", run_count, market_schedule.market_status_str())
            try:
                trader.run(config)
            except Exception as exc:
                log.error("Unexpected error during trading run: %s", exc, exc_info=True)

            log.info("Sleeping %d s until next run ...", loop_seconds)
            time.sleep(loop_seconds)

        except KeyboardInterrupt:
            log.info("Interrupted by user — shutting down.")
            break

    log.info("=" * 50)
    log.info("  STB stopped")
    log.info("=" * 50)


if __name__ == "__main__":
    main()
