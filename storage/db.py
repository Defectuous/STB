"""
storage/db.py

SQLite persistence for OHLCV bars. The live/paper engine writes every bar
it fetches here as a side effect, so a local historical dataset accumulates
over time. backtest.py reads from (and backfills into) the same store.

Schema: one row per (symbol, timeframe, timestamp).
"""

import sqlite3
from pathlib import Path

import pandas as pd

_SCHEMA = """
CREATE TABLE IF NOT EXISTS bars (
    symbol    TEXT    NOT NULL,
    timeframe TEXT    NOT NULL,
    ts        TEXT    NOT NULL,
    open      REAL    NOT NULL,
    high      REAL    NOT NULL,
    low       REAL    NOT NULL,
    close     REAL    NOT NULL,
    volume    REAL    NOT NULL,
    PRIMARY KEY (symbol, timeframe, ts)
);
CREATE INDEX IF NOT EXISTS idx_bars_symbol_tf_ts ON bars (symbol, timeframe, ts);

CREATE TABLE IF NOT EXISTS momentum_snapshots (
    snapshot_date TEXT    NOT NULL,
    lookback_days INTEGER NOT NULL,
    rank          INTEGER NOT NULL,
    symbol        TEXT    NOT NULL,
    momentum_pct  REAL    NOT NULL,
    close         REAL    NOT NULL,
    avg_volume    REAL    NOT NULL,
    PRIMARY KEY (snapshot_date, lookback_days, symbol)
);
CREATE INDEX IF NOT EXISTS idx_momentum_snapshots_lookback_date ON momentum_snapshots (lookback_days, snapshot_date);
"""


def get_connection(db_path: str) -> sqlite3.Connection:
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(db_path)
    conn.executescript(_SCHEMA)
    return conn


def save_bars(conn: sqlite3.Connection, symbol: str, timeframe: str, bars: pd.DataFrame) -> int:
    """
    Upsert a DataFrame of bars (must have a DatetimeIndex named 'timestamp'
    or a 'timestamp' column, plus open/high/low/close/volume columns).
    Returns the number of rows written.
    """
    if bars.empty:
        return 0

    df = bars.reset_index()
    ts_col = "timestamp" if "timestamp" in df.columns else df.columns[0]

    rows = [
        (
            symbol,
            timeframe,
            pd.Timestamp(row[ts_col]).isoformat(),
            float(row["open"]),
            float(row["high"]),
            float(row["low"]),
            float(row["close"]),
            float(row["volume"]),
        )
        for _, row in df.iterrows()
    ]

    conn.executemany(
        """
        INSERT INTO bars (symbol, timeframe, ts, open, high, low, close, volume)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(symbol, timeframe, ts) DO UPDATE SET
            open=excluded.open, high=excluded.high, low=excluded.low,
            close=excluded.close, volume=excluded.volume
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def load_bars(
    conn: sqlite3.Connection,
    symbol: str,
    timeframe: str,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Return stored bars for symbol/timeframe as a DataFrame, oldest -> newest."""
    query = "SELECT ts, open, high, low, close, volume FROM bars WHERE symbol = ? AND timeframe = ?"
    params: list = [symbol, timeframe]
    if start:
        query += " AND ts >= ?"
        params.append(start)
    if end:
        query += " AND ts <= ?"
        params.append(end)
    query += " ORDER BY ts ASC"

    df = pd.read_sql_query(query, conn, params=params, parse_dates=["ts"])
    df = df.rename(columns={"ts": "timestamp"}).set_index("timestamp")
    return df


def latest_timestamp(conn: sqlite3.Connection, symbol: str, timeframe: str) -> pd.Timestamp | None:
    row = conn.execute(
        "SELECT MAX(ts) FROM bars WHERE symbol = ? AND timeframe = ?", (symbol, timeframe)
    ).fetchone()
    return pd.Timestamp(row[0]) if row and row[0] else None


def known_symbols(conn: sqlite3.Connection) -> list[str]:
    rows = conn.execute("SELECT DISTINCT symbol FROM bars ORDER BY symbol").fetchall()
    return [r[0] for r in rows]


def save_momentum_snapshot(conn: sqlite3.Connection, snapshot_date: str, lookback_days: int, df: pd.DataFrame) -> int:
    """
    Upsert a momentum_universe.select_momentum_universe() result (its
    RESULT_COLUMNS: Symbol, Momentum_Pct, Close, Avg_Volume, Window_Bars),
    keyed by (snapshot_date, lookback_days, symbol) with rank taken from row
    order. Returns the number of rows written.
    """
    if df.empty:
        return 0

    rows = [
        (snapshot_date, lookback_days, rank, row["Symbol"], float(row["Momentum_Pct"]), float(row["Close"]), float(row["Avg_Volume"]))
        for rank, (_, row) in enumerate(df.iterrows())
    ]

    conn.executemany(
        """
        INSERT INTO momentum_snapshots (snapshot_date, lookback_days, rank, symbol, momentum_pct, close, avg_volume)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(snapshot_date, lookback_days, symbol) DO UPDATE SET
            rank=excluded.rank, momentum_pct=excluded.momentum_pct, close=excluded.close, avg_volume=excluded.avg_volume
        """,
        rows,
    )
    conn.commit()
    return len(rows)


def latest_momentum_snapshot_date(conn: sqlite3.Connection, lookback_days: int) -> str | None:
    row = conn.execute(
        "SELECT MAX(snapshot_date) FROM momentum_snapshots WHERE lookback_days = ?", (lookback_days,)
    ).fetchone()
    return row[0] if row and row[0] else None


def load_momentum_snapshot(conn: sqlite3.Connection, lookback_days: int, snapshot_date: str | None = None) -> pd.DataFrame:
    """
    Return a cached momentum universe snapshot as a DataFrame shaped like
    momentum_universe.RESULT_COLUMNS, ordered by rank. Defaults to the most
    recently saved snapshot for this lookback_days if snapshot_date is None.
    Returns an empty DataFrame if no snapshot is cached.
    """
    if snapshot_date is None:
        snapshot_date = latest_momentum_snapshot_date(conn, lookback_days)
        if snapshot_date is None:
            return pd.DataFrame(columns=["Symbol", "Momentum_Pct", "Close", "Avg_Volume", "Window_Bars"])

    rows = conn.execute(
        """
        SELECT symbol, momentum_pct, close, avg_volume FROM momentum_snapshots
        WHERE lookback_days = ? AND snapshot_date = ?
        ORDER BY rank ASC
        """,
        (lookback_days, snapshot_date),
    ).fetchall()

    return pd.DataFrame(
        [{"Symbol": r[0], "Momentum_Pct": r[1], "Close": r[2], "Avg_Volume": r[3], "Window_Bars": None} for r in rows],
        columns=["Symbol", "Momentum_Pct", "Close", "Avg_Volume", "Window_Bars"],
    )
