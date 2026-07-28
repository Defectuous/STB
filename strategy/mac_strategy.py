"""
strategy/mac_strategy.py

Moving Average Crossover strategy, per MAC_strat.md:

  - SMA_fast (default 20-period) and SMA_slow (default 50-period) simple
    moving averages of the closing price.
  - Golden cross (fast crosses from at/below slow to above slow) -> BUY,
    only when flat.
  - Death cross (fast crosses from at/above slow to below slow) -> SELL,
    only when holding a position.
  - Every entry carries a hard stop-loss `max_loss_pct` below the entry
    price (attached by the broker, not re-evaluated here).

Two optional entry gates, both disabled by default (None) to keep behavior
unchanged for existing single-symbol/backtest use:

  - trend_filter_period: require price above this SMA (e.g. 200) at entry.
  - max_entry_vol_pct: cap the entry-day realized volatility (rolling
    entry_vol_period-bar stdev of daily returns, as a percentage).

Backtesting this strategy against a basket of volatile sub-$20 momentum
names showed every stop-loss exit is, by construction, a loss - and those
losses cluster in the first few days after entry. Every real winner instead
survived long enough to exit on a death cross. Entries made during a
macro downtrend (price below its 200-day SMA) or during unusually high
realized volatility (where the 5% stop sits within ~1 day's normal noise)
accounted for nearly all of the fast stop-outs. These two gates exist to
filter those entries out before they're taken, trading trade frequency for
win rate.
"""

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from strategy.base import Action, Signal, Strategy
from strategy.indicators import crossed_above, crossed_below, realized_vol_pct, sma


class MovingAverageCrossoverStrategy(Strategy):
    name = "mac_strategy"

    def __init__(
        self,
        fast_period: int = 20,
        slow_period: int = 50,
        max_loss_pct: float = 0.05,
        allocation_pct: float = 1.00,
        trend_filter_period: int | None = None,
        max_entry_vol_pct: float | None = None,
        entry_vol_period: int = 20,
        **_ignored,
    ):
        if fast_period >= slow_period:
            raise ValueError("fast_period must be less than slow_period")
        self.fast_period = fast_period
        self.slow_period = slow_period
        self.max_loss_pct = max_loss_pct
        self.allocation_pct = allocation_pct
        self.trend_filter_period = trend_filter_period
        self.max_entry_vol_pct = max_entry_vol_pct
        self.entry_vol_period = entry_vol_period

    def required_bars(self) -> int:
        # Need slow_period (and, if set, trend_filter_period) bars to seed
        # the SMAs, plus one extra prior bar so we can compare bar t-1 vs
        # bar t and detect the cross event.
        base = max(self.slow_period, self.trend_filter_period or 0)
        return base + 1

    def timeframe(self) -> TimeFrame:
        return TimeFrame.Day

    def generate_signal(self, symbol: str, bars: pd.DataFrame, has_position: bool) -> Signal:
        if len(bars) < self.required_bars():
            return Signal(
                action=Action.HOLD,
                symbol=symbol,
                reason=f"insufficient bars ({len(bars)}/{self.required_bars()})",
                price=float(bars["close"].iloc[-1]) if len(bars) else 0.0,
            )

        closes = bars["close"]
        sma_fast = sma(closes, self.fast_period)
        sma_slow = sma(closes, self.slow_period)

        fast_now, fast_prev = sma_fast.iloc[-1], sma_fast.iloc[-2]
        slow_now, slow_prev = sma_slow.iloc[-1], sma_slow.iloc[-2]
        price = float(closes.iloc[-1])

        golden_cross = crossed_above(fast_prev, slow_prev, fast_now, slow_now)
        death_cross = crossed_below(fast_prev, slow_prev, fast_now, slow_now)

        if golden_cross and not has_position:
            if self.trend_filter_period is not None:
                sma_trend_now = sma(closes, self.trend_filter_period).iloc[-1]
                if not (price > sma_trend_now):
                    return Signal(
                        action=Action.HOLD,
                        symbol=symbol,
                        reason=f"golden cross blocked: price {price:.2f} below SMA{self.trend_filter_period}={sma_trend_now:.2f}",
                        price=price,
                    )

            if self.max_entry_vol_pct is not None:
                vol_now = realized_vol_pct(closes, self.entry_vol_period).iloc[-1]
                if vol_now > self.max_entry_vol_pct:
                    return Signal(
                        action=Action.HOLD,
                        symbol=symbol,
                        reason=f"golden cross blocked: {self.entry_vol_period}d volatility {vol_now:.1f}% > cap {self.max_entry_vol_pct:.1f}%",
                        price=price,
                    )

            return Signal(
                action=Action.BUY,
                symbol=symbol,
                reason=f"golden cross: SMA{self.fast_period}={fast_now:.2f} crossed above SMA{self.slow_period}={slow_now:.2f}",
                price=price,
                stop_loss_price=round(price * (1 - self.max_loss_pct), 2),
                allocation_pct=self.allocation_pct,
            )

        if death_cross and has_position:
            return Signal(
                action=Action.SELL,
                symbol=symbol,
                reason=f"death cross: SMA{self.fast_period}={fast_now:.2f} crossed below SMA{self.slow_period}={slow_now:.2f}",
                price=price,
            )

        return Signal(
            action=Action.HOLD,
            symbol=symbol,
            reason=f"no cross: SMA{self.fast_period}={fast_now:.2f} SMA{self.slow_period}={slow_now:.2f}",
            price=price,
        )
