"""
broker/alpaca_broker.py

Thin wrapper around alpaca-py's TradingClient and StockHistoricalDataClient.
This is the only module that talks to the Alpaca API directly - engine.py,
strategy code, and backtest.py never import alpaca-py themselves.
"""

import logging
import os
from datetime import datetime, timezone

import pandas as pd
from alpaca.data.enums import DataFeed
from alpaca.data.historical import StockHistoricalDataClient
from alpaca.data.requests import StockBarsRequest
from alpaca.data.timeframe import TimeFrame
from alpaca.trading.client import TradingClient
from alpaca.trading.enums import AssetClass, AssetStatus, OrderClass, OrderSide, QueryOrderStatus, TimeInForce
from alpaca.trading.requests import (
    GetAssetsRequest,
    GetOrdersRequest,
    MarketOrderRequest,
    StopLossRequest,
)

log = logging.getLogger(__name__)


class AlpacaBroker:
    def __init__(self, paper: bool = True):
        api_key = os.getenv("ALPACA_API_KEY")
        secret_key = os.getenv("ALPACA_SECRET_KEY")
        if not api_key or not secret_key:
            raise RuntimeError(
                "ALPACA_API_KEY / ALPACA_SECRET_KEY not set - copy .env.example to .env and fill them in."
            )
        self.paper = paper
        self.trading_client = TradingClient(api_key=api_key, secret_key=secret_key, paper=paper)
        self.data_client = StockHistoricalDataClient(api_key=api_key, secret_key=secret_key)

    # ── Account / positions ──────────────────────────────────────────────

    def get_buying_power(self) -> float:
        return float(self.trading_client.get_account().buying_power)

    def get_position_qty(self, symbol: str) -> float | None:
        try:
            position = self.trading_client.get_open_position(symbol)
        except Exception:
            return None
        return float(position.qty)

    def get_held_symbols(self) -> list[str]:
        """Symbols with an open position, regardless of which strategy opened it."""
        return [p.symbol for p in self.trading_client.get_all_positions()]

    # ── Market data ──────────────────────────────────────────────────────

    def get_bars(self, symbol: str, timeframe: TimeFrame, limit: int) -> pd.DataFrame:
        """Fetch the most recent `limit` bars for symbol, oldest -> newest."""
        end = datetime.now(timezone.utc)
        # generous calendar-day lookback so `limit` trading bars are covered
        start = end - pd.Timedelta(days=limit * 3 + 10)

        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            limit=limit,
            feed=DataFeed.IEX,
        )
        return self._bars_to_df(symbol, request).tail(limit)

    def get_historical_bars(
        self, symbol: str, timeframe: TimeFrame, start: datetime, end: datetime
    ) -> pd.DataFrame:
        """Fetch every bar for symbol between start and end (for backfilling storage)."""
        request = StockBarsRequest(
            symbol_or_symbols=symbol,
            timeframe=timeframe,
            start=start,
            end=end,
            feed=DataFeed.IEX,
        )
        return self._bars_to_df(symbol, request)

    def _bars_to_df(self, symbol: str, request: StockBarsRequest) -> pd.DataFrame:
        bars = self.data_client.get_stock_bars(request)
        df = bars.df
        if df.empty:
            return df
        if df.index.nlevels > 1:
            df = df.xs(symbol, level=0)
        return df[["open", "high", "low", "close", "volume"]]

    def get_tradable_universe(
        self,
        exchanges: tuple[str, ...] = ("NASDAQ", "NYSE", "AMEX"),
        marginable_only: bool = True,
    ) -> list[str]:
        """
        Return tradable US-equity symbols on the given exchanges.

        Excludes OTC names by construction (only the listed exchanges are
        accepted) and, when marginable_only is True, excludes non-marginable
        names as a proxy for filtering out the most volatile micro-caps.
        """
        request = GetAssetsRequest(status=AssetStatus.ACTIVE, asset_class=AssetClass.US_EQUITY)
        assets = self.trading_client.get_all_assets(request)
        wanted_exchanges = set(exchanges)
        return sorted(
            asset.symbol
            for asset in assets
            if asset.tradable
            and asset.exchange.value in wanted_exchanges
            and (not marginable_only or asset.marginable)
        )

    def get_bars_batch(
        self, symbols: list[str], timeframe: TimeFrame, start: datetime, end: datetime, batch_size: int = 100
    ) -> dict[str, pd.DataFrame]:
        """
        Fetch bars for many symbols, chunked into requests of `batch_size`
        symbols each (Alpaca supports multi-symbol requests but large
        universes still need chunking to stay under rate/URI limits).

        Returns {symbol: DataFrame}. A batch that fails to fetch is logged
        and skipped rather than aborting the whole run.
        """
        results: dict[str, pd.DataFrame] = {}
        for i in range(0, len(symbols), batch_size):
            chunk = symbols[i : i + batch_size]
            try:
                request = StockBarsRequest(
                    symbol_or_symbols=chunk,
                    timeframe=timeframe,
                    start=start,
                    end=end,
                    feed=DataFeed.IEX,
                )
                bars = self.data_client.get_stock_bars(request)
                df = bars.df
            except Exception:
                log.exception(
                    "Failed to fetch bar batch %d-%d (%d symbols) - skipping", i, i + len(chunk), len(chunk)
                )
                continue

            if df.empty:
                continue

            for sym in chunk:
                try:
                    sym_df = df.xs(sym, level=0) if df.index.nlevels > 1 else df
                except KeyError:
                    continue
                results[sym] = sym_df[["open", "high", "low", "close", "volume"]]

        return results

    # ── Orders ───────────────────────────────────────────────────────────

    def submit_buy_with_stop(self, symbol: str, qty: float, stop_price: float) -> dict:
        """Market buy that carries a resting stop-loss child order."""
        order_data = MarketOrderRequest(
            symbol=symbol,
            qty=round(qty, 6),
            side=OrderSide.BUY,
            time_in_force=TimeInForce.DAY,
            order_class=OrderClass.OTO,
            stop_loss=StopLossRequest(stop_price=round(stop_price, 2)),
        )
        order = self.trading_client.submit_order(order_data)
        return order.model_dump()

    def close_position_safely(self, symbol: str) -> dict:
        """Cancel any open orders for symbol (e.g. the resting stop-loss), then liquidate."""
        open_orders = self.trading_client.get_orders(
            GetOrdersRequest(status=QueryOrderStatus.OPEN, symbols=[symbol])
        )
        for order in open_orders:
            self.trading_client.cancel_order_by_id(order.id)

        order = self.trading_client.close_position(symbol)
        return order.model_dump() if hasattr(order, "model_dump") else dict(order)
