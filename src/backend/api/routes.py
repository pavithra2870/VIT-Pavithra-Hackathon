"""HTTP API (FastAPI). Thin layer: validate -> call service -> typed response. No business logic here."""
from __future__ import annotations

from typing import Optional

from fastapi import APIRouter, Query

from src.backend.models import schemas as S
from src.backend.services import copilot_service, llm, market_service, refresh, portfolio_service, rebalance_service, signals_service, store, stress_service

router = APIRouter(prefix="/api")


@router.get("/health", response_model=S.Health, tags=["system"])
def health():
    return {"status": "ok", "database": True, "signals": int(len(store.signals())), "portfolios": int(len(store.portfolios())),
            "sentiment_backend": store.pipeline_summary().get("sentiment_backend"), "llm": llm.status(force=True)}


@router.get("/market/summary", response_model=S.MarketSummary, tags=["risk engine"])
def market_summary(date: Optional[str] = Query(None, pattern=r"^\d{4}-\d{2}-\d{2}$", description="Show the overview as of this day (default: newest)")):
    return market_service.market_summary(date)


@router.get("/risk-signals", response_model=S.SignalList, tags=["risk engine"])
def risk_signals(event_type: Optional[str] = None, min_impact: float = Query(0, ge=0, le=10), sentiment: Optional[str] = Query(None, pattern="^(negative|neutral|positive)$"),
                 date: Optional[str] = None, ticker: Optional[str] = None, sector: Optional[str] = None, source_type: Optional[str] = Query(None, pattern="^(news|social|gdelt)$"),
                 q: Optional[str] = None, sort: str = Query("date", pattern="^(impact|date|confidence|sentiment)$"), limit: int = Query(50, ge=1, le=500), offset: int = Query(0, ge=0)):
    return signals_service.list_signals(event_type, min_impact, sentiment, date, ticker, sector, q, sort, limit, offset, source_type)


@router.get("/risk-signals/{event_id}", response_model=S.SignalDetail, tags=["risk engine"])
def risk_signal(event_id: str):
    return signals_service.signal_detail(event_id)


@router.get("/portfolios", response_model=list[S.PortfolioSummary], tags=["portfolios"])
def portfolios():
    return portfolio_service.list_portfolios()


@router.get("/portfolios/{portfolio_id}", response_model=S.PortfolioDetail, tags=["portfolios"])
def portfolio(portfolio_id: str):
    return portfolio_service.portfolio_detail(portfolio_id)


@router.post("/rebalance", response_model=S.RebalanceResponse, tags=["module A"])
def rebalance(req: S.RebalanceRequest):
    ov = req.model_dump(exclude={"as_of", "include_history"}, exclude_none=True)
    return rebalance_service.run(req.as_of, ov, req.include_history)


@router.get("/stress-scenarios", tags=["module B"])
def stress_scenarios():
    return stress_service.list_scenarios()


@router.post("/stress-test", response_model=S.StressResponse, tags=["module B"])
def stress_test(req: S.StressRequest):
    ev = stress_service.resolve_event(req.event_id, req.event_type, req.impact_score, req.sentiment, req.tickers, req.sectors)
    res = stress_service.stress(req.portfolio_id, ev, req.overrides, with_curve=True)
    res["portfolio_comparison"] = stress_service.compare_all(ev, req.overrides)
    return res


@router.get("/stress-ranking", tags=["module B"])
def stress_ranking(portfolio_id: str, limit: int = Query(20, ge=1, le=40)):
    return stress_service.rank_events(portfolio_id, limit)


@router.post("/copilot", response_model=S.CopilotResponse, tags=["copilot"])
def copilot(req: S.CopilotRequest):
    return copilot_service.answer(req.message, req.portfolio_id, req.event_id)


@router.get("/data-status", tags=["system"])
def data_status():
    return refresh.state()


@router.post("/data-refresh", tags=["system"])
def data_refresh():
    return {"started": refresh.start(force=True), **refresh.state()}
