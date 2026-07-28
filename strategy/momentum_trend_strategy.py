"""
strategy/momentum_trend_strategy.py

Momentum/trend-following strategy, built directly from the winner/loser
analysis run against mac_strategy's trade history (see momentum_universe.py
and MAC_strat.md for the sibling strategy this was compared against):

  - Entries are gated on the two filters that measurably separated winners
    from losers there: price above a long trend SMA (default 200-period),
    and entry-day realized volatility under a cap (default 4%, 20-period).
    Unlike mac_strategy, momentum itself - not an incidental MA crossover -
    is the entry trigger: a BUY fires the first bar the trailing
    `momentum_period`-bar return crosses above `min_momentum_pct`. 60+ day
    momentum windows outperformed a 30-day window when ranking the trading
    universe, so momentum_period defaults to 60.
  - Exits are a rolling trailing stop (price falls `trailing_stop_pct`
    below its own `trailing_period`-bar high) rather than a symmetric
    crossover-based exit: backtesting showed winners here tend to run for
    months, and that trying to cut losers earlier than the mechanical stop
    (via any same-signal early-exit rule) didn't improve results - so this
    exit is built to give a real trend room to run, only bailing out once
    the trend has visibly rolled over from its own recent peak.
  - Every entry still carries a hard stop-loss `max_loss_pct` below the
    entry price (attached by the broker, not re-evaluated here), as a
    safety net under the trailing stop.
"""

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from strategy.base import Action, Signal, Strategy
from strategy.indicators import crossed_above, momentum_pct, realized_vol_pct, sma, trailing_stop_price


class MomentumTrendStrategy(Strategy):
    name = "momentum_trend"

    def __init__(
        self,
        trend_filter_period: int = 200,
        max_entry_vol_pct: float = 4.0,
        entry_vol_period: int = 20,
        momentum_period: int = 60,
        min_momentum_pct: float = 15.0,
        trailing_period: int = 20,
        trailing_stop_pct: float = 0.12,
        max_loss_pct: float = 0.08,
        allocation_pct: float = 1.00,
        **_ignored,
    ):
        self.trend_filter_period = trend_filter_period
        self.max_entry_vol_pct = max_entry_vol_pct
        self.entry_vol_period = entry_vol_period
        self.momentum_period = momentum_period
        self.min_momentum_pct = min_momentum_pct
        self.trailing_period = trailing_period
        self.trailing_stop_pct = trailing_stop_pct
        self.max_loss_pct = max_loss_pct
        self.allocation_pct = allocation_pct

    def required_bars(self) -> int:
        # +2: one extra prior bar to detect the momentum threshold cross,
        # plus the trailing-stop lookback already excludes today via shift(1).
        return max(self.trend_filter_period, self.momentum_period, self.entry_vol_period, self.trailing_period) + 2

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
        price = float(closes.iloc[-1])

        if not has_position:
            sma_trend_now = sma(closes, self.trend_filter_period).iloc[-1]
            if not (price > sma_trend_now):
                return Signal(
                    action=Action.HOLD, symbol=symbol,
                    reason=f"price {price:.2f} below trend SMA{self.trend_filter_period}={sma_trend_now:.2f}",
                    price=price,
                )

            vol_now = realized_vol_pct(closes, self.entry_vol_period).iloc[-1]
            if vol_now > self.max_entry_vol_pct:
                return Signal(
                    action=Action.HOLD, symbol=symbol,
                    reason=f"{self.entry_vol_period}d volatility {vol_now:.1f}% > cap {self.max_entry_vol_pct:.1f}%",
                    price=price,
                )

            mom = momentum_pct(closes, self.momentum_period)
            mom_now, mom_prev = mom.iloc[-1], mom.iloc[-2]
            if crossed_above(mom_prev, self.min_momentum_pct, mom_now, self.min_momentum_pct):
                return Signal(
                    action=Action.BUY, symbol=symbol,
                    reason=f"momentum breakout: {self.momentum_period}d return {mom_now:.1f}% crossed above {self.min_momentum_pct:.1f}%",
                    price=price,
                    stop_loss_price=round(price * (1 - self.max_loss_pct), 2),
                    allocation_pct=self.allocation_pct,
                )

            return Signal(
                action=Action.HOLD, symbol=symbol,
                reason=f"no momentum trigger: {self.momentum_period}d return {mom_now:.1f}% (need {self.min_momentum_pct:.1f}%)",
                price=price,
            )

        stop_now = trailing_stop_price(closes, self.trailing_period, self.trailing_stop_pct).iloc[-1]
        if price < stop_now:
            return Signal(
                action=Action.SELL, symbol=symbol,
                reason=f"trailing stop: price {price:.2f} fell below {stop_now:.2f} "
                       f"({self.trailing_stop_pct * 100:.0f}% off the {self.trailing_period}d high)",
                price=price,
            )

        return Signal(
            action=Action.HOLD, symbol=symbol,
            reason=f"holding: price {price:.2f} above trailing stop {stop_now:.2f}",
            price=price,
        )
