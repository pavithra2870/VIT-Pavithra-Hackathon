"""SQLite access layer. The database is a derived artifact: it is rebuilt from /data by `python -m src.backend.db.build`."""
from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from typing import Iterator

import pandas as pd

from src.config import DB_PATH


def connect() -> sqlite3.Connection:
    if not DB_PATH.exists():
        from src.backend.db.build import build_database  # lazy: first-run bootstrap from /data

        build_database()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


@contextmanager
def session() -> Iterator[sqlite3.Connection]:
    conn = connect()
    try:
        yield conn
    finally:
        conn.close()


def query_df(sql: str, params: tuple | list = ()) -> pd.DataFrame:
    with session() as conn:
        return pd.read_sql_query(sql, conn, params=params)
