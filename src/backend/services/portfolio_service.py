"""Portfolio catalogue + holdings views (valuation and allocation only; shocks live in the stress module)."""
from __future__ import annotations

import numpy as np
import pandas as pd
from fastapi import HTTPException

from src.backend.services import store
from src.backend.services.stress_service import holdings_for


def _alloc(df: pd.DataFrame, col: str, total: float) -> list[dict]:
    g = df.groupby(col)["market_value"].sum().sort_values(ascending=False)
    return [{"key": k, "value": float(v), "weight": float(v / total)} for k, v in g.items()]


def _num(v):
    return None if pd.isna(v) else float(v)


def portfolio_summary(row, h: pd.DataFrame) -> dict:
    total = float(h["market_value"].sum())
    eq = h[h["asset_class"] == "equity"]
    bonds = h[h["asset_class"] == "bond"]
    dur = float(np.average(bonds["modified_duration"], weights=bonds["market_value"])) if len(bonds) else 0.0
    return {
        "portfolio_id": row.portfolio_id, "name": row.name, "strategy": row.strategy, "description": row.description, "valuation_date": row.valuation_date, "synthetic": True,
        "total_value": total, "n_holdings": int(len(h)),
        "by_asset_class": _alloc(h, "asset_class", total),
        "equity_beta": float(np.average([store.betas().get(t, 1.0) for t in eq["ticker"]], weights=eq["market_value"])) if len(eq) else None,
        "bond_duration": dur, "derivative_notional": float(h[h["asset_class"] == "derivative"]["notional"].sum()),
        "loan_notional": float(h[h["asset_class"] == "loan"]["notional"].sum()),
        "equities_live": bool(h["live_priced"].any()) if "live_priced" in h else False,
        "priced_at": store.quote_time() if ("live_priced" in h and h["live_priced"].any()) else None,
    }


def list_portfolios() -> list[dict]:
    h = store.holdings()
    return [portfolio_summary(p, h[h["portfolio_id"] == p.portfolio_id]) for p in store.portfolios().itertuples()]


def portfolio_detail(portfolio_id: str) -> dict:
    p = store.portfolios()
    p = p[p["portfolio_id"] == portfolio_id]
    if p.empty:
        raise HTTPException(404, f"Unknown portfolio {portfolio_id}")
    h = holdings_for(portfolio_id)
    total = float(h["market_value"].sum())
    pos = h[h["asset_class"] != "derivative"]
    top = h.assign(abs_mv=h["market_value"].abs()).sort_values("abs_mv", ascending=False).head(10)
    cols = ["holding_id", "name", "asset_class", "instrument_type", "ticker", "sector", "country", "rating", "market_value", "notional", "modified_duration", "pd", "lgd", "spread_bp", "rate_type", "coupon", "maturity_years"]
    holdings = [{k: (None if pd.isna(v) else v) for k, v in r.items()} for r in h[cols].to_dict("records")]
    for r in holdings:
        r["weight"] = r["market_value"] / total
    return {
        **portfolio_summary(next(p.itertuples()), h),
        "by_sector": _alloc(pos, "sector", float(pos["market_value"].sum())),
        "top_holdings": [{"holding_id": r.holding_id, "name": r.name, "asset_class": r.asset_class, "market_value": float(r.market_value), "weight": float(r.market_value / total)} for r in top.itertuples()],
        "holdings": holdings,
    }
