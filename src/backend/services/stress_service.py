"""Stress Lab service: resolves an event (Risk Signal or custom scenario) + portfolio -> engine -> typed result."""
from __future__ import annotations

from fastapi import HTTPException

from src.backend.services import store
from src.backend.services.signals_service import get_signal_row
from src.config import stress_config
from src.modules.stress_testing.engine import StressEvent, run_stress


def list_scenarios() -> list[dict]:
    cfg = stress_config()
    return [{"event_type": k, "label": v["label"], "scope": v["scope"], "description": v["description"],
             "parallel_bp_at_impact10": v["rates"]["parallel_bp"], "equity_default_at_impact10": v["equity"]["default"]} for k, v in cfg["events"].items()]


def resolve_event(event_id: str | None, event_type: str | None, impact_score: float | None, sentiment: float | None, tickers: list[str] | None, sectors: list[str] | None) -> StressEvent:
    if event_id:
        row = get_signal_row(event_id)
        tk = tuple(tickers) if tickers is not None else tuple(store.split(row["affected_tickers"]))
        sc = tuple(sectors) if sectors is not None else tuple(store.split(row["affected_sectors"]))
        return StressEvent(event_type or row["event_type"], float(impact_score if impact_score is not None else row["impact_score"]),
                           float(sentiment if sentiment is not None else row["sentiment"]), tk, sc, row["event_name"], event_id)
    if not event_type:
        raise HTTPException(422, "Provide event_id or event_type")
    if event_type not in stress_config()["events"]:
        raise HTTPException(422, f"Unknown event_type {event_type}")
    return StressEvent(event_type, float(impact_score if impact_score is not None else 8.0), float(sentiment if sentiment is not None else -0.5),
                       tuple(tickers or ()), tuple(sectors or ()), f"Custom {stress_config()['events'][event_type]['label']} scenario", "")


def holdings_for(portfolio_id: str):
    h = store.holdings()
    h = h[h["portfolio_id"] == portfolio_id]
    if h.empty:
        raise HTTPException(404, f"Unknown portfolio {portfolio_id}")
    return h


BREAK_EVEN_LEVELS = (0.01, 0.03, 0.05, 0.10)


def impact_curve(portfolio_id: str, event: StressEvent, overrides: dict | None = None) -> tuple[list[dict], list[dict]]:
    """Portfolio loss across impact scores 1..10 for the same event class, plus the impact at which fixed loss levels are first reached."""
    h, betas = holdings_for(portfolio_id), store.betas()
    grid = [round(1 + 0.5 * i, 1) for i in range(19)]
    curve = []
    for imp in grid:
        r = run_stress(h, StressEvent(event.event_type, imp, event.sentiment, event.tickers, event.sectors, event.name, event.event_id), betas, overrides)
        curve.append({"impact": imp, "pct_impact": r["pct_impact"], "pnl": r["pnl"]})
    be = []
    for lvl in BREAK_EVEN_LEVELS:
        hit = next((i for i, c in enumerate(curve) if c["pct_impact"] <= -lvl), None)
        if hit is None:
            be.append({"loss_pct": lvl, "impact": None})
        elif hit == 0:
            be.append({"loss_pct": lvl, "impact": curve[0]["impact"]})
        else:                                   # linear interpolation inside the grid cell
            a, b = curve[hit - 1], curve[hit]
            t = (-lvl - a["pct_impact"]) / ((b["pct_impact"] - a["pct_impact"]) or -1e-9)
            be.append({"loss_pct": lvl, "impact": round(a["impact"] + t * (b["impact"] - a["impact"]), 1)})
    return curve, be


def rank_events(portfolio_id: str, limit: int = 20) -> list[dict]:
    """Latest live GDELT events (impact >= 5) stress-tested on ONE portfolio and ranked by dollar loss: exposure, not headline size, decides."""
    from src.backend.services.signals_service import list_signals

    items = list_signals(source_type="gdelt", min_impact=5.0, sort="date", limit=limit)["items"]
    h, betas, y = holdings_for(portfolio_id), store.betas(), store.ten_year_yield()
    out = []
    for it in items:
        ev = resolve_event(it["event_id"], None, None, None, None, None)
        r = run_stress(h, ev, betas, None, y)
        out.append({"event_id": it["event_id"], "event_name": it["event_name"], "signal_date": it["signal_date"], "event_label": it["event_label"],
                    "impact_score": it["impact_score"], "sentiment": it["sentiment"], "pnl": r["pnl"], "pct_impact": r["pct_impact"],
                    "main_driver": r["by_driver"][0]["label"] if r["by_driver"] else "-"})
    return sorted(out, key=lambda d: d["pnl"])


def stress(portfolio_id: str, event: StressEvent, overrides: dict | None = None, with_curve: bool = False) -> dict:
    res = run_stress(holdings_for(portfolio_id), event, store.betas(), overrides, store.ten_year_yield())
    if with_curve:
        res["impact_curve"], res["break_even"] = impact_curve(portfolio_id, event, overrides)
    res["portfolio_id"] = portfolio_id
    res["portfolio_name"] = store.portfolios().set_index("portfolio_id").loc[portfolio_id, "name"]
    return res


def compare_all(event: StressEvent, overrides: dict | None = None) -> list[dict]:
    """The same event applied to every portfolio: event risk is not universal, it depends on exposure."""
    out = []
    for p in store.portfolios().itertuples():
        r = run_stress(holdings_for(p.portfolio_id), event, store.betas(), overrides)
        out.append({"portfolio_id": p.portfolio_id, "portfolio_name": p.name, "value_before": r["value_before"], "value_after": r["value_after"],
                    "pnl": r["pnl"], "pct_impact": r["pct_impact"], "main_driver": r["by_driver"][0]["label"] if r["by_driver"] else "-"})
    return sorted(out, key=lambda d: d["pct_impact"])
