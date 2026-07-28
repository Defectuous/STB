# Strategy Specification: MAC_strat
**System Type:** Trend-Following Momentum System  
**Execution Type:** Algorithmic / Automated Execution  
**Data Dependency:** Daily OHLCV Bars (End-of-Day)  

---

## 1. System Parameters & Configuration
The execution engine must initialize the strategy using these precise inputs. Do not allow dynamic optimization during runtime unless explicitly instructed.

| Parameter Name | Data Type | Default Value | Description |
| :--- | :--- | :--- | :--- |
| `ASSET_TICKER` | String | `SPY` | Target trading vehicle symbol |
| `BAR_INTERVAL` | String | `1D` | Candlestick time frame interval |
| `FAST_PERIOD` | Integer | `20` | Short-term Simple Moving Average length |
| `SLOW_PERIOD` | Integer | `50` | Medium-term Simple Moving Average length |
| `MAX_LOSS_PCT` | Float | `0.05` | 5% hard stop-loss threshold distance |
| `ALLOCATION_PCT` | Float | `1.00` | Capital allocation percentage per trade |

---

## 2. Mathematical Indicators
Calculate the indicators at the close of every `BAR_INTERVAL`.

*   **Fast Line ($SMA_{fast}$):** 20-period simple moving average of the closing price.
*   **Slow Line ($SMA_{slow}$):** 50-period simple moving average of the closing price.

$$\text{SMA}_n = \frac{1}{n} \sum_{i=0}^{n-1} \text{Close}_{t-i}$$

---

## 3. Algorithmic State Machine & Logic

The bot must evaluate state transitions sequentially on the definitive close of each daily candle ($t$). The index ($t-1$) denotes the previous closed candle.