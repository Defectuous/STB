"""
engine.py

TradingEngine wires together the broker, the configured strategy, the local
bar store, the NYSE scheduler, and the Discord notifier. It is the only
module that orchestrates a full evaluate -> act cycle; strategies stay
broker-agnostic and the broker stays strategy-agnostic.
"""

import logging
import time
from datetime import date, datetime
from zoneinfo import ZoneInfo

from broker.alpaca_broker import AlpacaBroker
from notify.discord import notify_order_failure, notify_signal
from scheduler import nyse_calendar
from state import TradeState
from storage import db
from strategy.base import Action, Signal

log = logging.getLogger(__name__)


class TradingEngine:
    def __init__(self, config, broker: AlpacaBroker | None = None):
        self.config = config
        self.broker = broker or AlpacaBroker(paper=config.paper_trading)
        self.strategy = config.strategy
        self.state = TradeState(config.state_file)
        self.conn = db.get_connection(config.db_file)
        self.timeframe_str = str(self.strategy.timeframe())
        self._current_symbols = list(config.symbols)
        self._universe_date: date | None = None

    def _refresh_universe(self) -> None:
        """
        Recompute the dynamic momentum universe once per (Eastern) trading
        day, if config.universe is set. Symbols currently held are always
        kept in the evaluation set even if they've fallen out of the
        top-N momentum ranking, so an open position still gets a chance to
        exit on a death cross rather than being orphaned to the resting
        stop-loss alone.
        """
        universe_cfg = self.config.universe
        if universe_cfg is None or universe_cfg.mode != "momentum_top_n":
            return

        today = datetime.now(tz=ZoneInfo("America/New_York")).date()
        if self._universe_date == today:
            return

        from momentum_universe import select_momentum_universe

        df = select_momentum_universe(
            self.broker,
            exchanges=universe_cfg.exchanges,
            marginable_only=universe_cfg.marginable_only,
            lookback_days=universe_cfg.lookback_days,
            max_price=universe_cfg.max_price,
            min_price=universe_cfg.min_price,
            min_avg_volume=universe_cfg.min_avg_volume,
            top_n=universe_cfg.top_n,
        )
        momentum_symbols = df["Symbol"].tolist() if not df.empty else []
        held_symbols = self.broker.get_held_symbols()

        self._current_symbols = sorted(set(momentum_symbols) | set(held_symbols))
        self._universe_date = today
        log.info(
            "Universe refreshed for %s: %d momentum + %d held (%d unique)",
            today, len(momentum_symbols), len(held_symbols), len(self._current_symbols),
        )

    def evaluate_symbol(self, symbol: str, dry_run: bool = False) -> Signal:
        bars = self.broker.get_bars(
            symbol, self.strategy.timeframe(), self.strategy.required_bars()
        )
        if not bars.empty:
            db.save_bars(self.conn, symbol, self.timeframe_str, bars)

        has_position = self.broker.get_position_qty(symbol) is not None
        signal = self.strategy.generate_signal(symbol, bars, has_position)

        latest_bar_ts = bars.index[-1].isoformat() if not bars.empty else None
        if latest_bar_ts is not None and latest_bar_ts == self.state.last_bar_ts(symbol):
            log.debug("%s: already evaluated bar %s, skipping action", symbol, latest_bar_ts)
            return signal

        log.info("%s: %s (%s)", symbol, signal.action.value, signal.reason)

        if signal.action == Action.HOLD:
            if latest_bar_ts:
                self.state.record(symbol, latest_bar_ts, signal.action.value)
            return signal

        if dry_run:
            log.info("%s: dry-run - skipping order submission", symbol)
            if latest_bar_ts:
                self.state.record(symbol, latest_bar_ts, f"DRY_RUN_{signal.action.value}")
            return signal

        try:
            order_id, qty = self._act_on_signal(signal)
        except Exception as exc:
            log.exception(
                "%s: order submission failed for %s signal - marking bar handled to avoid retry storm",
                symbol, signal.action.value,
            )
            notify_order_failure(signal, exc, paper=self.config.paper_trading)
            if latest_bar_ts:
                self.state.record(symbol, latest_bar_ts, f"FAILED_{signal.action.value}")
            return signal

        notify_signal(signal, order_id=order_id, qty=qty, paper=self.config.paper_trading)

        if latest_bar_ts:
            self.state.record(symbol, latest_bar_ts, signal.action.value)

        return signal

    def _act_on_signal(self, signal: Signal) -> tuple[str, float]:
        if signal.action == Action.BUY:
            buying_power = self.broker.get_buying_power()

            if self.config.position_size_usd is not None:
                position_size = self.config.position_size_usd
                if buying_power < position_size:
                    log.info(
                        "%s: skipping buy - buying power $%.2f < position size $%.2f",
                        signal.symbol, buying_power, position_size,
                    )
                    return "", 0.0
                qty = round(position_size / signal.price, 6)
            else:
                allocation = signal.allocation_pct if signal.allocation_pct is not None else 1.0
                qty = round((buying_power * allocation) / signal.price, 6)

            order = self.broker.submit_buy_with_stop(
                signal.symbol, qty, signal.stop_loss_price
            )
            return str(order.get("id", "")), qty

        if signal.action == Action.SELL:
            qty = self.broker.get_position_qty(signal.symbol) or 0.0
            order = self.broker.close_position_safely(signal.symbol)
            return str(order.get("id", "")), qty

        return "", 0.0

    def run_once(self, dry_run: bool = False) -> dict[str, Signal]:
        self._refresh_universe()
        results: dict[str, Signal] = {}
        for symbol in self._current_symbols:
            try:
                results[symbol] = self.evaluate_symbol(symbol, dry_run=dry_run)
            except Exception:
                log.exception("%s: unexpected error evaluating symbol - skipping this cycle", symbol)
        return results

    def run_forever(self, dry_run: bool = False) -> None:
        log.info("Engine started - strategy=%s", self.strategy.name)
        while True:
            try:
                if not nyse_calendar.is_market_open():
                    status = nyse_calendar.market_status_str()
                    wait_secs = nyse_calendar.seconds_until_market_open()
                    log.info("Market %s - next open in %.0f s", status, wait_secs)
                    time.sleep(max(wait_secs, 60))
                    continue

                self.run_once(dry_run=dry_run)
                log.info("Sleeping %d s until next poll", self.config.poll_interval_seconds)
                time.sleep(self.config.poll_interval_seconds)

            except KeyboardInterrupt:
                log.info("Interrupted by user - shutting down.")
                break
            except Exception:
                log.exception("Unexpected error during trading loop - continuing after backoff")
                time.sleep(60)
