"""
alpaca_client.py
Handles all communication with the Alpaca Markets API:
  - Fetching historical bar data for RSI calculation
  - Retrieving account cash balance
  - Retrieving open positions
  - Placing market / limit buy orders
  - Placing market / stop-loss sell orders
"""

import os
from datetime import datetime, timedelta, timezone

from dotenv import load_dotenv
from alpaca.trading.client import TradingClient
from alpaca.trading.requests import MarketOrderRequest, LimitOrderRequest, StopOrderRequest
from alpaca.trading.enums import OrderSide, TimeInForce
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest, StockLatestTradeRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.data.enums import DataFeed

load_dotenv()


def _get_trading_client(paper: bool) -> TradingClient:
    return TradingClient(
        api_key=os.getenv("ALPACA_API_KEY"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
        paper=paper,
    )


def _get_data_client() -> StockHistoricalDataClient:
    return StockHistoricalDataClient(
        api_key=os.getenv("ALPACA_API_KEY"),
        secret_key=os.getenv("ALPACA_SECRET_KEY"),
    )


def get_account(paper: bool = True) -> dict:
    """Return a simplified dict with cash and portfolio value."""
    client = _get_trading_client(paper)
    account = client.get_account()
    return {
        "cash": float(account.cash),
        "portfolio_value": float(account.portfolio_value),
        "buying_power": float(account.buying_power),
    }


def get_positions(paper: bool = True) -> dict:
    """Return a dict of {symbol: qty} for all open positions."""
    client = _get_trading_client(paper)
    positions = client.get_all_positions()
    return {p.symbol: float(p.qty) for p in positions}


def get_historical_closes(symbol: str, lookback_days: int = 60) -> list[float]:
    """
    Fetch daily closing prices for *symbol* going back *lookback_days* calendar days.
    Returns a list of floats ordered oldest → newest.
    """
    data_client = _get_data_client()
    end = datetime.now(timezone.utc)
    start = end - timedelta(days=lookback_days)

    request = StockBarsRequest(
        symbol_or_symbols=symbol,
        timeframe=TimeFrame.Day,
        start=start,
        end=end,
        limit=lookback_days,
        feed=DataFeed.IEX,
    )
    bars = data_client.get_stock_bars(request)
    df = bars.df

    if df.empty:
        raise ValueError(f"No bar data returned for {symbol}")

    # bars.df may be multi-indexed (symbol, timestamp) — flatten if needed
    if isinstance(df.index, type(df.index)) and df.index.nlevels > 1:
        df = df.xs(symbol, level=0)

    closes = df["close"].tolist()
    return closes


def get_latest_price(symbol: str) -> float:
    """
    Fetch the latest trade price for *symbol*.
    Used to calculate limit prices and stop-loss levels.
    """
    data_client = _get_data_client()
    request = StockLatestTradeRequest(symbol_or_symbols=symbol, feed=DataFeed.IEX)
    result = data_client.get_stock_latest_trade(request)
    return float(result[symbol].price)


def place_buy_order(symbol: str, notional: float, paper: bool = True) -> dict:
    """
    Place a fractional market buy order for *notional* dollars of *symbol*.
    Returns the order object as a dict.
    """
    client = _get_trading_client(paper)
    order_data = MarketOrderRequest(
        symbol=symbol,
        notional=round(notional, 2),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(order_data)
    return order.model_dump()


def place_limit_buy_order(symbol: str, qty: float, limit_price: float, paper: bool = True) -> dict:
    """
    Place a limit buy order for *qty* shares at *limit_price*.
    Uses GTC (Good Till Cancelled) so it can fill if price dips to the limit.
    Returns the order object as a dict.
    """
    client = _get_trading_client(paper)
    order_data = LimitOrderRequest(
        symbol=symbol,
        qty=round(qty, 6),
        limit_price=round(limit_price, 2),
        side=OrderSide.BUY,
        time_in_force=TimeInForce.GTC,
    )
    order = client.submit_order(order_data)
    return order.model_dump()


def place_sell_order(symbol: str, qty: float, paper: bool = True) -> dict:
    """
    Place a fractional market sell order for *qty* shares of *symbol*.
    Returns the order object as a dict.
    """
    client = _get_trading_client(paper)
    order_data = MarketOrderRequest(
        symbol=symbol,
        qty=qty,
        side=OrderSide.SELL,
        time_in_force=TimeInForce.DAY,
    )
    order = client.submit_order(order_data)
    return order.model_dump()


def place_stop_loss_order(symbol: str, qty: float, stop_price: float, paper: bool = True) -> dict:
    """
    Place a stop (stop-loss) sell order for *qty* shares.
    Order triggers a market sell when price falls to *stop_price*.
    Returns the order object as a dict.
    """
    client = _get_trading_client(paper)
    order_data = StopOrderRequest(
        symbol=symbol,
        qty=round(qty, 6),
        stop_price=round(stop_price, 2),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
    )
    order = client.submit_order(order_data)
    return order.model_dump()


def place_take_profit_order(symbol: str, qty: float, limit_price: float, paper: bool = True) -> dict:
    """
    Place a limit sell (take-profit) order for *qty* shares at *limit_price*.
    Order fills when price rises to or above *limit_price*.
    Uses GTC so it remains active until filled or cancelled.
    Returns the order object as a dict.
    """
    client = _get_trading_client(paper)
    order_data = LimitOrderRequest(
        symbol=symbol,
        qty=round(qty, 6),
        limit_price=round(limit_price, 2),
        side=OrderSide.SELL,
        time_in_force=TimeInForce.GTC,
    )
    order = client.submit_order(order_data)
    return order.model_dump()
