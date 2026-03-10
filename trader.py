"""
trader.py
Core trading logic:
  - Evaluates each stock from the config file
  - Builds RSI
  - Asks ChatGPT for a decision
  - Enforces daily trading rules
  - Executes orders via Alpaca
  - Logs all activity
"""

import json
import logging
from datetime import datetime, timezone

import alpaca_client as alpaca
from rsi_calculator import calculate_rsi
from chatgpt_advisor import get_trading_advice
import trade_state
import discord_notify

log = logging.getLogger(__name__)


def _log_trade(entry: dict, log_file: str) -> None:
    """Append a trade/decision record to the JSON log file."""
    history = []
    try:
        with open(log_file, "r") as f:
            history = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        pass

    history.append(entry)
    with open(log_file, "w") as f:
        json.dump(history, f, indent=2, default=str)


def run(config: dict) -> None:
    """
    Main trading loop.  Called once per run with the loaded config dict.
    """
    stocks: list[str] = config["stocks"]
    rsi_period: int = config["rsi_period"]
    lookback_days: int = config["rsi_lookback_days"]
    wallet_pct: float = config["wallet_percentage"]
    model: str = config["chatgpt_model"]
    paper: bool = config["paper_trading"]
    log_file: str = config["log_file"]

    # ── Pre-flight checks ────────────────────────────────────────────────────
    if trade_state.has_sold_today():
        log.info("A SELL was already executed today. No further trading allowed.")
        return

    account = alpaca.get_account(paper=paper)
    cash_available = account["cash"]
    log.info(
        "Account: cash=$%.2f  buying_power=$%.2f  portfolio=$%.2f",
        cash_available,
        account["buying_power"],
        account["portfolio_value"],
    )

    positions = alpaca.get_positions(paper=paper)
    log.info("Open positions: %s", positions if positions else "none")

    # ── Evaluate each stock ───────────────────────────────────────────────────
    for symbol in stocks:
        # Check again in case a SELL happened mid-loop
        if trade_state.has_sold_today():
            log.info("SELL executed during this run — stopping further evaluation.")
            break

        log.info("--- Evaluating %s ---", symbol)

        # 1. Fetch historical closes
        try:
            closes = alpaca.get_historical_closes(symbol, lookback_days=lookback_days)
        except Exception as exc:
            log.error("Failed to get historical data for %s: %s", symbol, exc)
            continue

        # 2. Calculate RSI
        try:
            rsi = calculate_rsi(closes, period=rsi_period)
        except ValueError as exc:
            log.error("RSI calculation failed for %s: %s", symbol, exc)
            continue

        log.info("%s  RSI=%.2f", symbol, rsi)

        # 3. Ask ChatGPT
        try:
            decision = get_trading_advice(symbol, rsi, model=model)
        except Exception as exc:
            log.error("ChatGPT error for %s: %s", symbol, exc)
            continue

        log.info("%s  Decision=%s", symbol, decision)

        # 4. Act on decision
        timestamp = datetime.now(timezone.utc).isoformat()

        if decision == "BUY":
            _handle_buy(
                symbol=symbol,
                rsi=rsi,
                cash=cash_available,
                wallet_pct=wallet_pct,
                paper=paper,
                log_file=log_file,
                timestamp=timestamp,
                use_limit_orders=config.get("use_limit_orders", False),
                limit_order_offset_pct=config.get("limit_order_offset_pct", 0.005),
                use_stop_loss=config.get("use_stop_loss", False),
                stop_loss_pct=config.get("stop_loss_pct", 0.05),
                use_take_profit=config.get("use_take_profit", False),
                take_profit_pct=config.get("take_profit_pct", 0.50),
            )

        elif decision == "SELL":
            _handle_sell(
                symbol=symbol,
                rsi=rsi,
                positions=positions,
                paper=paper,
                log_file=log_file,
                timestamp=timestamp,
            )

        else:  # DO NOTHING
            log.info("%s  Action=DO NOTHING — no trade placed.", symbol)
            _log_trade(
                {
                    "timestamp": timestamp,
                    "symbol": symbol,
                    "rsi": rsi,
                    "decision": "DO NOTHING",
                    "action": "skipped",
                },
                log_file,
            )


# ── Helpers ───────────────────────────────────────────────────────────────────

def _handle_buy(
    symbol: str,
    rsi: float,
    cash: float,
    wallet_pct: float,
    paper: bool,
    log_file: str,
    timestamp: str,
    use_limit_orders: bool = False,
    limit_order_offset_pct: float = 0.005,
    use_stop_loss: bool = False,
    stop_loss_pct: float = 0.05,
    use_take_profit: bool = False,
    take_profit_pct: float = 0.50,
) -> None:
    if not trade_state.can_buy():
        log.info("%s  BUY skipped — already bought once today.", symbol)
        return

    spend = round(cash * wallet_pct, 2)
    if spend <= 0:
        log.warning("%s  BUY skipped — insufficient cash ($%.2f).", symbol, cash)
        return

    # Get current price (needed for limit and stop-loss calculations)
    try:
        current_price = alpaca.get_latest_price(symbol)
        log.info("%s  Current price: $%.4f", symbol, current_price)
    except Exception as exc:
        log.error("%s  Could not fetch latest price, falling back to market order: %s", symbol, exc)
        current_price = None
        use_limit_orders = False
        use_stop_loss = False

    try:
        if use_limit_orders and current_price:
            # Limit buy: set price slightly below current to get a better fill
            limit_price = round(current_price * (1 - limit_order_offset_pct), 2)
            qty = round(spend / current_price, 6)
            log.info("%s  Placing LIMIT BUY: %.6f shares @ $%.2f limit (%.1f%% below $%.4f).",
                     symbol, qty, limit_price, limit_order_offset_pct * 100, current_price)
            order = alpaca.place_limit_buy_order(symbol, qty=qty, limit_price=limit_price, paper=paper)
            order_type = "limit"
        else:
            log.info("%s  Placing MARKET BUY for $%.2f (%.0f%% of $%.2f cash).",
                     symbol, spend, wallet_pct * 100, cash)
            order = alpaca.place_buy_order(symbol, notional=spend, paper=paper)
            order_type = "market"

        trade_state.record_buy()
        log.info("%s  BUY order submitted: %s", symbol, order.get("id"))
        discord_notify.notify_buy(
            symbol=symbol,
            rsi=rsi,
            order_type=order_type,
            notional=spend,
            order_id=order.get("id", "unknown"),
            limit_price=limit_price if use_limit_orders and current_price else None,
            qty=qty if use_limit_orders and current_price else None,
            paper=paper,
        )

        trade_entry = {
            "timestamp": timestamp,
            "symbol": symbol,
            "rsi": rsi,
            "decision": "BUY",
            "action": "order_placed",
            "order_type": order_type,
            "notional": spend,
            "order_id": order.get("id"),
        }
        if use_limit_orders and current_price:
            trade_entry["limit_price"] = limit_price
            trade_entry["qty"] = qty

        _log_trade(trade_entry, log_file)

        # Place stop-loss after a successful buy
        if use_stop_loss and current_price:
            buy_price = limit_price if use_limit_orders else current_price
            stop_price = round(buy_price * (1 - stop_loss_pct), 2)
            est_qty = qty if use_limit_orders else round(spend / current_price, 6)
            log.info("%s  Placing STOP-LOSS: %.6f shares @ $%.2f stop (%.1f%% below $%.4f).",
                     symbol, est_qty, stop_price, stop_loss_pct * 100, buy_price)
            try:
                sl_order = alpaca.place_stop_loss_order(symbol, qty=est_qty, stop_price=stop_price, paper=paper)
                log.info("%s  Stop-loss order submitted: %s", symbol, sl_order.get("id"))
                _log_trade(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "rsi": rsi,
                        "decision": "BUY",
                        "action": "stop_loss_placed",
                        "stop_price": stop_price,
                        "qty": est_qty,
                        "order_id": sl_order.get("id"),
                    },
                    log_file,
                )
            except Exception as sl_exc:
                log.error("%s  Stop-loss order failed: %s", symbol, sl_exc)

        # Place take-profit limit sell after a successful buy
        if use_take_profit and current_price:
            buy_price = limit_price if use_limit_orders else current_price
            tp_price = round(buy_price * (1 + take_profit_pct), 2)
            est_qty = qty if use_limit_orders else round(spend / current_price, 6)
            log.info("%s  Placing TAKE-PROFIT: %.6f shares @ $%.2f limit (%.0f%% above $%.4f).",
                     symbol, est_qty, tp_price, take_profit_pct * 100, buy_price)
            try:
                tp_order = alpaca.place_take_profit_order(symbol, qty=est_qty, limit_price=tp_price, paper=paper)
                log.info("%s  Take-profit order submitted: %s", symbol, tp_order.get("id"))
                _log_trade(
                    {
                        "timestamp": timestamp,
                        "symbol": symbol,
                        "rsi": rsi,
                        "decision": "BUY",
                        "action": "take_profit_placed",
                        "take_profit_price": tp_price,
                        "qty": est_qty,
                        "order_id": tp_order.get("id"),
                    },
                    log_file,
                )
            except Exception as tp_exc:
                log.error("%s  Take-profit order failed: %s", symbol, tp_exc)

    except Exception as exc:
        log.error("%s  BUY order failed: %s", symbol, exc)
        _log_trade(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "rsi": rsi,
                "decision": "BUY",
                "action": "order_failed",
                "error": str(exc),
            },
            log_file,
        )


def _handle_sell(
    symbol: str,
    rsi: float,
    positions: dict,
    paper: bool,
    log_file: str,
    timestamp: str,
) -> None:
    if not trade_state.can_sell():
        log.info("%s  SELL skipped — already sold once today.", symbol)
        return

    qty = positions.get(symbol, 0.0)
    if qty <= 0:
        log.info("%s  SELL skipped — no open position.", symbol)
        _log_trade(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "rsi": rsi,
                "decision": "SELL",
                "action": "no_position",
            },
            log_file,
        )
        return

    log.info("%s  Placing SELL order for %.6f shares.", symbol, qty)
    try:
        order = alpaca.place_sell_order(symbol, qty=qty, paper=paper)
        trade_state.record_sell()
        log.info("%s  SELL order submitted: %s", symbol, order.get("id"))
        discord_notify.notify_sell(
            symbol=symbol,
            rsi=rsi,
            qty=qty,
            order_id=order.get("id", "unknown"),
            paper=paper,
        )
        _log_trade(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "rsi": rsi,
                "decision": "SELL",
                "action": "order_placed",
                "qty": qty,
                "order_id": order.get("id"),
            },
            log_file,
        )
    except Exception as exc:
        log.error("%s  SELL order failed: %s", symbol, exc)
        _log_trade(
            {
                "timestamp": timestamp,
                "symbol": symbol,
                "rsi": rsi,
                "decision": "SELL",
                "action": "order_failed",
                "error": str(exc),
            },
            log_file,
        )
