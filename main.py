"""
main.py
Entry point for the Simple Trading Bot (STB).

Usage:
    python main.py --once --dry-run   # test the strategy right now, no orders
    python main.py --once             # one real evaluation pass (paper by default)
    python main.py                    # continuous loop, market-hours aware
"""

import argparse
import logging
import sys
from pathlib import Path

from dotenv import load_dotenv

# Load only this project's .env - dotenv's default search walks up parent
# directories, which risks silently picking up credentials from an
# unrelated .env elsewhere on the machine.
load_dotenv(Path(__file__).resolve().parent / ".env")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Simple Trading Bot")
    parser.add_argument("--config", default="config.json", help="Path to config JSON (default: config.json)")
    parser.add_argument("--once", action="store_true", help="Run a single evaluation pass across all symbols, then exit")
    parser.add_argument("--dry-run", action="store_true", help="Log signals but never submit orders")
    return parser.parse_args()


def main() -> None:
    args = _parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s  %(levelname)-8s  %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )
    log = logging.getLogger(__name__)

    from config_loader import load_config
    from engine import TradingEngine

    config = load_config(args.config)

    # add the file handler now that we know the configured log path
    file_handler = logging.FileHandler(config.log_file)
    file_handler.setFormatter(logging.Formatter("%(asctime)s  %(levelname)-8s  %(message)s", "%Y-%m-%d %H:%M:%S"))
    logging.getLogger().addHandler(file_handler)

    log.info("=" * 50)
    log.info("  Simple Trading Bot (STB) starting")
    log.info("=" * 50)
    if config.universe is not None:
        log.info(
            "Strategy: %s | Universe: top %d momentum, <$%.2f, %dd lookback | Paper trading: %s",
            config.strategy.name, config.universe.top_n, config.universe.max_price,
            config.universe.lookback_days, config.paper_trading,
        )
    else:
        log.info("Strategy: %s | Symbols: %s | Paper trading: %s", config.strategy.name, config.symbols, config.paper_trading)
    if args.dry_run:
        log.info("DRY RUN - no orders will be submitted")

    engine = TradingEngine(config)

    if args.once:
        engine.run_once(dry_run=args.dry_run)
    else:
        engine.run_forever(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
