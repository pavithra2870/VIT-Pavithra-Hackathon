"""Build the SQLite database from the processed artifacts and portfolio data under /data.

    python -m src.backend.db.build
"""
from __future__ import annotations

import os
import sqlite3

import pandas as pd

from src.config import DB_PATH, PORTFOLIOS, PROCESSED

TABLES = {
    "risk_signals": (PROCESSED / "risk_signals.csv", "event_id"),
    "signal_entities": (PROCESSED / "signal_entities.csv", "event_id"),
    "signal_documents": (PROCESSED / "signal_documents.csv", "event_id"),
    "documents": (PROCESSED / "documents.csv", "doc_id"),
    "entity_mentions": (PROCESSED / "entity_mentions.csv", "doc_id"),
    "market_context": (PROCESSED / "market_context.csv", "ticker"),
    "index_context": (PROCESSED / "index_context.csv", "date"),
    "daily_risk": (PROCESSED / "daily_risk.csv", "signal_date"),
    "portfolios": (PORTFOLIOS / "portfolios.csv", "portfolio_id"),
    "holdings": (PORTFOLIOS / "holdings.csv", "portfolio_id"),
    "mock_index": (PORTFOLIOS / "mock_index.csv", "ticker"),
}
EXTRA_INDEXES = [("risk_signals", "impact_score"), ("risk_signals", "signal_date"), ("risk_signals", "event_type"), ("signal_entities", "ticker"), ("signal_documents", "doc_id"), ("holdings", "ticker")]


def build_database() -> None:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = DB_PATH.with_suffix(".db.tmp")  # build aside, then swap atomically so a running API never sees a half-built DB
    if tmp.exists():
        tmp.unlink()
    conn = sqlite3.connect(tmp)
    for name, (path, idx_col) in TABLES.items():
        if not path.exists():
            raise FileNotFoundError(f"{path} missing - run `python -m src.risk_engine.pipeline` and `python -m src.data_prep.generate_portfolios` first")
        df = pd.read_csv(path)
        df.to_sql(name, conn, index=False, if_exists="replace")
        conn.execute(f'CREATE INDEX IF NOT EXISTS ix_{name}_{idx_col} ON {name}("{idx_col}")')
    for t, c in EXTRA_INDEXES:
        conn.execute(f'CREATE INDEX IF NOT EXISTS ix_{t}_{c} ON {t}("{c}")')
    conn.commit()
    conn.close()
    os.replace(tmp, DB_PATH)
    print(f"database built: {DB_PATH}")


if __name__ == "__main__":
    build_database()
