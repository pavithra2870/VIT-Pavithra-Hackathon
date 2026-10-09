"""Tactical Rebalancer service: runs the engine for a date and (optionally) sequentially for every replay day."""
from __future__ import annotations

import pandas as pd

from src.backend.services import store
from src.modules.rebalancer.engine import rebalance


def run(as_of: str | None, overrides: dict, include_history: bool = True) -> dict:
    sig, ent, ctx, idx = store.signals(), store.signal_entities(), store.market_context(), store.mock_index()
    tick, cov_full = store.index_covariance()
    order = list(idx["ticker"])
    pos = [tick.index(t) for t in order]
    cov = cov_full[np_ix(pos)] if cov_full is not None else None
    days = sorted(sig["signal_date"].unique())
    as_of = as_of or days[-1]
    result = rebalance(idx, sig, ent, ctx, pd.Timestamp(as_of), cov, overrides)

    history = []
    if include_history:
        weights = None
        for d in [d for d in days if d <= as_of]:
            r = rebalance(idx, sig, ent, ctx, pd.Timestamp(d), cov, overrides, start_weights=weights)
            weights = {s["ticker"]: s["proposed_weight"] for s in r["stocks"]}
            history.append({"date": d, "weights": weights, "turnover": r["metrics"]["turnover"], "vol_after": r["metrics"]["vol_after"]})
    result["history"] = history
    q = store.quotes()
    for s in result["stocks"]:       # latest live price + day move per stock (display only)
        r = q.get(s["ticker"])
        s["last_price"] = r["price"] if r else None
        s["day_change"] = r["change_pct"] if r and r.get("change_pct") == r.get("change_pct") else None
    result["market_data"] = {"live": bool(q), "source": "Yahoo Finance via yfinance (quotes can be ~15 min delayed)" if q else "committed 2025 snapshot", "as_of": store.quote_time()}
    result["index"] = {"name": "Mock 15-stock index", "n_stocks": len(idx), "weighting": idx["weight_basis"].iloc[0]}
    result["available_dates"] = days
    return result


def np_ix(pos):
    import numpy as np

    return np.ix_(pos, pos)
