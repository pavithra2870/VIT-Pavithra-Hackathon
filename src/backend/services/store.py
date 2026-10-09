"""Cached, read-only access to the SQLite tables used by the services (the DB is rebuilt offline from /data)."""
from __future__ import annotations

import json
from functools import lru_cache

import numpy as np
import pandas as pd

from src.backend.db.database import connect, query_df
from src.config import DB_PATH, MARKET, PROCESSED, engine_config


YF_DIR = MARKET / "yfinance"


def _stamp() -> tuple:
    """Changes whenever the database or any live market file changes."""
    out = []
    for p in [DB_PATH, YF_DIR / "quotes.csv", YF_DIR / "prices_daily.csv", YF_DIR / "market_caps.csv"]:
        try:
            out.append(p.stat().st_mtime_ns)
        except OSError:
            out.append(None)
    return tuple(out)


def _table(fn):
    """Cache a table in memory, but drop it automatically when the SQLite file changes (e.g. rebuilt by the refresh job,
    by `python -m src.backend.db.build`, or by another process) so a running server never serves stale data."""
    cache: dict = {}

    def wrapper():
        stamp = _stamp()
        if "v" not in cache or cache["stamp"] != stamp:
            cache["v"], cache["stamp"] = fn(), stamp
        return cache["v"]

    wrapper.cache_clear = lambda: cache.clear()
    wrapper.__name__ = fn.__name__
    return wrapper


def _clean(df: pd.DataFrame) -> pd.DataFrame:
    return df.astype(object).where(pd.notna(df), None)


@_table
def signals() -> pd.DataFrame:
    df = query_df("SELECT * FROM risk_signals")
    for c in ("affected_sectors", "inferred_sectors", "countries", "affected_tickers", "matched_terms", "source_types"):
        df[c] = df[c].fillna("")
    return df


@_table
def signal_entities() -> pd.DataFrame:
    return query_df("SELECT * FROM signal_entities")


@_table
def market_context() -> pd.DataFrame:
    return query_df("SELECT * FROM market_context")


@_table
def index_context() -> pd.DataFrame:
    return query_df("SELECT * FROM index_context")


@_table
def portfolios() -> pd.DataFrame:
    return query_df("SELECT * FROM portfolios")


@_table
def holdings() -> pd.DataFrame:
    """Holdings. Equity positions are repriced at live quotes (quantities are fixed synthetic positions); bonds, loans and
    derivatives stay at their original synthetic marks."""
    h = query_df("SELECT * FROM holdings")
    h["live_priced"] = False
    q = quotes()
    if q:
        m = (h["asset_class"] == "equity") & h["ticker"].isin(q.keys())
        px = h.loc[m, "ticker"].map(lambda t: q[t]["price"])
        h.loc[m, "price"] = px
        h.loc[m, "market_value"] = (h.loc[m, "quantity"] * px).round(2)
        h.loc[m, "notional"] = h.loc[m, "market_value"]
        h.loc[m, "live_priced"] = True
    return h


@_table
def quotes() -> dict[str, dict]:
    """Latest quotes (live, from yfinance) keyed by symbol; empty when the live layer is absent."""
    p = YF_DIR / "quotes.csv"
    if not p.exists():
        return {}
    try:
        return {r["ticker"]: r for r in pd.read_csv(p).to_dict("records")}
    except Exception:
        return {}


def quote_time() -> str | None:
    q = quotes()
    return max((r["quote_time_utc"] for r in q.values()), default=None) if q else None


def ten_year_yield() -> float | None:
    """Live US 10-year Treasury yield as a fraction (^TNX is quoted in percent)."""
    r = quotes().get("^TNX")
    return float(r["price"]) / 100.0 if r else None


@_table
def mock_index() -> pd.DataFrame:
    """Index constituents. Weights: live market-cap weights (single-stock cap 12%) when yfinance caps exist, else the committed snapshot."""
    idx = query_df("SELECT * FROM mock_index")
    idx["weight_basis"] = "snapshot (sqrt of 2025 traded value, 12% cap)"
    p = YF_DIR / "market_caps.csv"
    if p.exists():
        try:
            from src.modules.rebalancer.engine import capped_weights

            caps = pd.read_csv(p).set_index("ticker")["market_cap"].to_dict()
            if all(t in caps for t in idx["ticker"]):
                w = capped_weights({t: caps[t] for t in idx["ticker"]}, 0.12)
                idx["weight"] = idx["ticker"].map(w)
                idx["weight_basis"] = "live market capitalisation (single-stock cap 12%)"
        except Exception:
            pass
    return idx


@_table
def betas() -> dict[str, float]:
    c = market_context()
    return {r.ticker: float(r.beta) for r in c.itertuples() if pd.notna(r.beta)}


@_table
def pipeline_summary() -> dict:
    p = PROCESSED / "pipeline_summary.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@_table
def sentiment_eval() -> dict:
    p = PROCESSED / "sentiment_model_eval.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


@_table
def index_covariance() -> tuple[list[str], np.ndarray | None]:
    """Annualised covariance of the mock-index constituents from 2025 daily returns."""
    from src.risk_engine.ingestion.market import load_prices

    tick = list(mock_index()["ticker"])
    px = load_prices()[tick]
    rets = np.log(px).diff().dropna()
    return tick, (rets.cov().loc[tick, tick].to_numpy() * 252)


def doc_count() -> dict:
    with connect() as c:
        row = c.execute("SELECT COUNT(*), COUNT(DISTINCT source_name) FROM documents").fetchone()
        by_type = {r[0]: r[1] for r in c.execute("SELECT source_type, COUNT(*) FROM documents GROUP BY source_type")}
    return {"documents": row[0], "sources": row[1], "by_source_type": by_type}


def split(s: str | None) -> list[str]:
    return [x for x in (s or "").split(";") if x]


def high_impact_threshold() -> float:
    return engine_config()["scoring"]["high_impact_threshold"]


def reload() -> None:
    """Drop every cached table (called after the database is rebuilt by the refresh job)."""
    for fn in (signals, signal_entities, market_context, index_context, portfolios, holdings, mock_index, betas, pipeline_summary, sentiment_eval, index_covariance):
        fn.cache_clear()
