"""
strategy/mean_reversion_strategy.py

Mean-reversion strategy: buy a dip within a confirmed uptrend, sell once
the dip has recovered back to normal. This is deliberately a different
shape from mac_strategy and momentum_trend_strategy (both trend-following:
ride a move, cut losers fast, let rare big winners carry the average).
Trend-following structurally caps win rate below 50% even when profitable,
since it wins big rarely and loses small often. Mean reversion inverts
that trade-off - many small, high-probability wins, at the cost of an
occasional larger loss when a "dip" turns out to be a real breakdown - in
pursuit of a materially higher win rate.

Parameter-swept against the cached momentum universe (see
tests/test_mean_reversion_strategy.py and the sweep behind these
defaults): the win-rate-maximizing shape turned out to be a *shallow* dip
(oversold_threshold=48, far milder than the classic RSI<30 signal) paired
with a fast, small profit target (2%) rather than waiting for RSI to fully
recover - taking a quick, high-probability win rather than holding out for
a bigger one materially raised the win rate. A wide stop-loss (8%) turned
out to matter too: tightening it (tested down to 2%) got triggered by
routine volatility before a real dip had room to bounce, which hurt both
win rate and return - counterintuitive, but the sweep was consistent about
it across all four momentum-universe lookback windows tested.

  - Entry: price above a long trend SMA (default 200-period, the same
    macro-trend filter validated for the other two strategies - only buy
    dips inside an uptrend, not a falling knife) AND RSI(rsi_period) has
    just crossed below oversold_threshold.
  - Exit: whichever comes first - price reaches `take_profit_pct` above
    entry, or RSI recovers above `exit_rsi_threshold` (a fallback for the
    case where price grinds sideways without cleanly tagging the target).
    Since the Strategy interface only passes `has_position` (not an entry
    price or entry date), the entry price is reconstructed by scanning
    backward for the most recent bar where this strategy's own entry
    condition fired - safe because entries are gated on a fresh crossed_below
    event, so at most one qualifying bar exists in any unbroken holding
    period, and re-entry is blocked by `has_position` while already long.
    The scan is capped at `max_entry_lookback_bars` for cost and as a
    defensive backstop; if no entry bar is found in that window (shouldn't
    happen in practice), the position falls back to RSI-recovery-only exit.
  - Every entry still carries a hard stop-loss `max_loss_pct` below the
    entry price (attached by the broker, not re-evaluated here), for the
    case where the dip doesn't bounce.
"""

import pandas as pd
from alpaca.data.timeframe import TimeFrame

from strategy.base import Action, Signal, Strategy
from strategy.indicators import crossed_above, crossed_below, rsi, sma


class MeanReversionStrategy(Strategy):
    name = "mean_reversion"

    def __init__(
        self,
        trend_filter_period: int = 200,
        rsi_period: int = 14,
        oversold_threshold: float = 48.0,
        exit_rsi_threshold: float = 70.0,
        take_profit_pct: float | None = 0.02,
        max_loss_pct: float = 0.08,
        max_entry_lookback_bars: int = 252,
        allocation_pct: float = 1.00,
        **_ignored,
    ):
        self.trend_filter_period = trend_filter_period
        self.rsi_period = rsi_period
        self.oversold_threshold = oversold_threshold
        self.exit_rsi_threshold = exit_rsi_threshold
        self.take_profit_pct = take_profit_pct
        self.max_loss_pct = max_loss_pct
        self.max_entry_lookback_bars = max_entry_lookback_bars
        self.allocation_pct = allocation_pct

    def required_bars(self) -> int:
        return max(self.trend_filter_period, self.rsi_period) + 2

    def timeframe(self) -> TimeFrame:
        return TimeFrame.Day

    def _entry_price(self, closes: pd.Series, sma_trend: pd.Series, rsi_series: pd.Series) -> float | None:
        """
        Reconstruct this position's entry price by scanning backward for the
        most recent bar where the entry condition (trend filter + fresh
        oversold cross) fired. See module docstring for why this is safe.
        """
        floor = max(self.required_bars(), len(closes) - self.max_entry_lookback_bars)
        for j in range(len(closes) - 1, floor, -1):
            price_j = float(closes.iloc[j])
            trend_j = sma_trend.iloc[j]
            rsi_now, rsi_prev = rsi_series.iloc[j], rsi_series.iloc[j - 1]
            if pd.isna(trend_j) or pd.isna(rsi_now) or pd.isna(rsi_prev):
                continue
            if not (price_j > trend_j):
                continue
            if crossed_below(rsi_prev, self.oversold_threshold, rsi_now, self.oversold_threshold):
                return price_j
        return None

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
        rsi_series = rsi(closes, self.rsi_period)
        rsi_now, rsi_prev = rsi_series.iloc[-1], rsi_series.iloc[-2]
        sma_trend = sma(closes, self.trend_filter_period)

        if not has_position:
            sma_trend_now = sma_trend.iloc[-1]
            if not (price > sma_trend_now):
                return Signal(
                    action=Action.HOLD, symbol=symbol,
                    reason=f"price {price:.2f} below trend SMA{self.trend_filter_period}={sma_trend_now:.2f}",
                    price=price,
                )

            if crossed_below(rsi_prev, self.oversold_threshold, rsi_now, self.oversold_threshold):
                return Signal(
                    action=Action.BUY, symbol=symbol,
                    reason=f"oversold dip: RSI{self.rsi_period}={rsi_now:.1f} crossed below {self.oversold_threshold:.0f}",
                    price=price,
                    stop_loss_price=round(price * (1 - self.max_loss_pct), 2),
                    allocation_pct=self.allocation_pct,
                )

            return Signal(
                action=Action.HOLD, symbol=symbol,
                reason=f"no dip: RSI{self.rsi_period}={rsi_now:.1f} (need < {self.oversold_threshold:.0f})",
                price=price,
            )

        if self.take_profit_pct is not None:
            entry_price = self._entry_price(closes, sma_trend, rsi_series)
            if entry_price is not None and price >= entry_price * (1 + self.take_profit_pct):
                return Signal(
                    action=Action.SELL, symbol=symbol,
                    reason=f"profit target: price {price:.2f} >= {self.take_profit_pct * 100:.0f}% above entry {entry_price:.2f}",
                    price=price,
                )

        if crossed_above(rsi_prev, self.exit_rsi_threshold, rsi_now, self.exit_rsi_threshold):
            return Signal(
                action=Action.SELL, symbol=symbol,
                reason=f"dip recovered: RSI{self.rsi_period}={rsi_now:.1f} crossed above {self.exit_rsi_threshold:.0f}",
                price=price,
            )

        return Signal(
            action=Action.HOLD, symbol=symbol,
            reason=f"holding: RSI{self.rsi_period}={rsi_now:.1f} (exit at {self.exit_rsi_threshold:.0f} or +{(self.take_profit_pct or 0) * 100:.0f}%)",
            price=price,
        )
