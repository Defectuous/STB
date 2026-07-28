"""
tests/test_db.py

Covers the momentum-snapshot cache added to storage/db.py so
backtest.py's --momentum-lookback-days can replay a previously-fetched
universe/bar set fully offline via --no-fetch.
"""

import sys
from pathlib import Path

import pandas as pd
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from storage import db

RESULT_COLUMNS = ["Symbol", "Momentum_Pct", "Close", "Avg_Volume", "Window_Bars"]


@pytest.fixture
def conn(tmp_path):
    return db.get_connection(str(tmp_path / "test.sqlite3"))


def _make_df(rows: list[tuple[str, float, float, float]]) -> pd.DataFrame:
    return pd.DataFrame(
        [{"Symbol": s, "Momentum_Pct": m, "Close": c, "Avg_Volume": v, "Window_Bars": 21} for s, m, c, v in rows],
        columns=RESULT_COLUMNS,
    )


def test_load_momentum_snapshot_empty_when_nothing_cached(conn):
    df = db.load_momentum_snapshot(conn, lookback_days=30)
    assert df.empty
    assert list(df.columns) == RESULT_COLUMNS


def test_save_and_load_momentum_snapshot_round_trip_preserves_rank_order(conn):
    original = _make_df([("HIGH", 30.0, 12.0, 2_000_000), ("MID", 10.0, 8.0, 1_500_000), ("LOW", 1.0, 5.0, 1_100_000)])
    n = db.save_momentum_snapshot(conn, "2026-07-26", 30, original)
    assert n == 3

    loaded = db.load_momentum_snapshot(conn, lookback_days=30)
    assert list(loaded["Symbol"]) == ["HIGH", "MID", "LOW"]
    assert loaded.iloc[0]["Momentum_Pct"] == 30.0


def test_latest_momentum_snapshot_date_picks_most_recent(conn):
    df = _make_df([("A", 5.0, 10.0, 1_000_000)])
    db.save_momentum_snapshot(conn, "2026-07-01", 30, df)
    db.save_momentum_snapshot(conn, "2026-07-26", 30, df)
    db.save_momentum_snapshot(conn, "2026-07-15", 30, df)

    assert db.latest_momentum_snapshot_date(conn, 30) == "2026-07-26"


def test_snapshots_are_isolated_by_lookback_days(conn):
    df30 = _make_df([("A", 5.0, 10.0, 1_000_000)])
    df60 = _make_df([("B", 8.0, 15.0, 1_000_000)])
    db.save_momentum_snapshot(conn, "2026-07-26", 30, df30)
    db.save_momentum_snapshot(conn, "2026-07-26", 60, df60)

    assert db.load_momentum_snapshot(conn, 30)["Symbol"].tolist() == ["A"]
    assert db.load_momentum_snapshot(conn, 60)["Symbol"].tolist() == ["B"]


def test_save_momentum_snapshot_upserts_on_conflict(conn):
    v1 = _make_df([("A", 5.0, 10.0, 1_000_000)])
    v2 = _make_df([("A", 9.0, 11.0, 1_200_000)])
    db.save_momentum_snapshot(conn, "2026-07-26", 30, v1)
    db.save_momentum_snapshot(conn, "2026-07-26", 30, v2)

    loaded = db.load_momentum_snapshot(conn, 30)
    assert len(loaded) == 1
    assert loaded.iloc[0]["Momentum_Pct"] == 9.0


def test_save_momentum_snapshot_empty_df_returns_zero(conn):
    empty = pd.DataFrame(columns=RESULT_COLUMNS)
    assert db.save_momentum_snapshot(conn, "2026-07-26", 30, empty) == 0
