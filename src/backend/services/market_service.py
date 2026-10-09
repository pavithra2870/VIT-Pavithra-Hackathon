"""Market-wide Risk Intelligence summary (dashboard top section)."""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.backend.services import signals_service, store

TOP_N = 25
INFERRED_WEIGHT = 0.1


def _band(x: float) -> str:
    return "Low" if x < 3 else "Moderate" if x < 5 else "Elevated" if x < 6.5 else "High"


def _day_risk(g: pd.DataFrame) -> float:
    top = g.sort_values("impact_score", ascending=False).head(TOP_N)
    return float((top["impact_score"] * (0.4 + 0.6 * top["adverse_weight"])).mean()) if len(top) else 0.0


def _sector_pressure(sig: pd.DataFrame, window: list[str]) -> list[dict]:
    """Impact x confidence x adversity by sector over a set of days. Sectors actually named/evidenced in the news weigh 1.0;
    sectors merely inferred from the event class (a fixed config rule, identical every day) only weigh INFERRED_WEIGHT, so the
    ranking follows the news rather than the configuration."""
    pressure: dict[str, float] = {}
    for r in sig[sig["signal_date"].isin(window)].itertuples():
        p = r.impact_score * r.confidence * (0.4 + 0.6 * r.adverse_weight)
        for s in store.split(r.affected_sectors):
            pressure[s] = pressure.get(s, 0.0) + p
        for s in store.split(r.inferred_sectors):
            pressure[s] = pressure.get(s, 0.0) + INFERRED_WEIGHT * p
    tot = sum(pressure.values()) or 1.0
    return [{"sector": s, "pressure": round(v, 2), "share": round(v / tot, 4)} for s, v in sorted(pressure.items(), key=lambda kv: -kv[1])]


def _markets() -> list[dict]:
    """Live headline market levels (S&P 500, VIX, 10-year yield) from yfinance quotes; empty without the live layer."""
    q = store.quotes()
    out = []
    for sym, label in (("^GSPC", "S&P 500"), ("^VIX", "VIX"), ("^TNX", "US 10Y yield")):
        r = q.get(sym)
        if r:
            out.append({"symbol": sym, "label": label, "price": float(r["price"]), "change_pct": r.get("change_pct"), "as_of": r["quote_time_utc"],
                        "unit": "%" if sym == "^TNX" else ""})
    return out


def market_summary(date: str | None = None) -> dict:
    # headline indicators use LIVE signals only (built from real, dated GDELT events); historical corpora are replayed
    # onto the window and would otherwise dilute 'what is happening now'
    all_sig = store.signals()
    sig = all_sig[all_sig["source_types"].str.contains("gdelt")]
    days = sorted(sig["signal_date"].unique())
    newest = days[-1]
    latest = date if date in days else newest          # selected day (default: newest)
    k = days.index(latest)
    window3 = days[max(0, k - 2): k + 1]
    series = []
    for d in days:
        g = sig[sig["signal_date"] == d]
        w = g["n_docs"]
        series.append({"date": d, "risk_index": round(_day_risk(g), 3), "avg_sentiment": round(float(np.average(g["sentiment"], weights=w)), 4), "signals": int(len(g)),
                       "high_impact": int((g["impact_score"] >= store.high_impact_threshold()).sum()),
                       "negative_share": round(float((g["sentiment"] < -0.15).mean()), 4)})
    # latest event timestamp (UTC) and whether the newest day is still filling (no daily export published for it yet)
    from src.data_prep.fetch_gdelt import local_dates

    daily = local_dates()
    partial = bool(daily) and pd.Timestamp(latest).date() > daily[-1]
    day_table = [{**s, "top_sector": (_sector_pressure(sig, days[max(0, i - 2): i + 1]) or [{"sector": None}])[0]["sector"]} for i, s in enumerate(series)][-10:]
    pure = sig[(sig["signal_date"] == latest) & (sig["source_types"] == "gdelt")]   # pure-GDELT clusters carry real event times
    as_of_time = str(pure["last_seen"].max())[:16]
    cur, prv = series[k], (series[k - 1] if k > 0 else None)

    sectors = _sector_pressure(sig, window3)

    types = sig[sig["signal_date"].isin(window3)].groupby("event_label").agg(signals=("event_id", "count"), avg_impact=("impact_score", "mean")).round(3).reset_index().sort_values("signals", ascending=False)
    top = signals_service.list_signals(limit=8, sort="impact", date=None, source_type="gdelt")
    latest_top = signals_service.list_signals(limit=8, sort="impact", date=latest, source_type="gdelt")
    ctx = store.index_context().tail(120)
    idx_last = ctx.iloc[-1]
    ps = store.pipeline_summary()
    cov = store.doc_count()
    ev = store.sentiment_eval()
    ev = ev.get(store.pipeline_summary().get("sentiment_backend"), ev)
    return {
        "as_of": latest, "newest_day": newest, "is_newest": latest == newest, "day_table": day_table, "as_of_time": as_of_time, "partial_day": partial and latest == newest, "window_days": len(days), "data_mode": "live GDELT events (company-level news from replayed corpora is shown on the Signals page)", "live_signals": int(len(sig)),
        "market_risk": {"index": cur["risk_index"], "label": _band(cur["risk_index"]), "change_1d": round(cur["risk_index"] - prv["risk_index"], 3) if prv else 0.0,
                        "description": f"Mean of top-{TOP_N} signal impacts, weighted by how adverse each event class/sentiment is for risk assets."},
        "news_sentiment": {"value": cur["avg_sentiment"], "label": "negative" if cur["avg_sentiment"] <= -0.15 else "positive" if cur["avg_sentiment"] >= 0.15 else "neutral",
                           "change_1d": round(cur["avg_sentiment"] - prv["avg_sentiment"], 4) if prv else 0.0, "negative_share": cur["negative_share"]},
        "top_sector": sectors[0] if sectors else None, "sector_pressure": sectors[:8],
        "risk_landscape": series, "event_mix": types.to_dict("records"),
        "top_signals": latest_top["items"], "top_signals_window": top["items"],
        "high_impact_count": cur["high_impact"], "high_impact_threshold": store.high_impact_threshold(),
        "markets": _markets(),
        "market_backdrop": {"index": "S&P 500", "as_of": idx_last["date"], "close": float(idx_last["close"]), "vol_20d": float(idx_last["vol_20d"]), "vol_percentile": float(idx_last["vol_pctile"]),
                            "series": [{"date": r.date, "close": float(r.close), "vol_20d": float(r.vol_20d)} for r in ctx.itertuples()]},
        "coverage": {**cov, "signals": int(len(sig)), "sentiment_backend": ps.get("sentiment_backend"), "sentiment_heldout_accuracy": ev.get("heldout_accuracy"),
                     "sentiment_heldout_macro_f1": ev.get("heldout_macro_f1")},
    }
