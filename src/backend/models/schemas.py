"""Pydantic request/response models: the typed contract between FastAPI and the React frontend."""
from __future__ import annotations

from typing import Any, Optional

from pydantic import BaseModel, Field


# ------------------------------------------------------------------ health
class LLMStatus(BaseModel):
    available: bool
    model: Optional[str] = None
    models: list[str] = []
    base_url: str
    reason: Optional[str] = None


class Health(BaseModel):
    status: str
    database: bool
    signals: int
    portfolios: int
    sentiment_backend: Optional[str] = None
    llm: LLMStatus


# ------------------------------------------------------------------ risk signals
class SignalItem(BaseModel):
    event_id: str
    signal_date: str
    event_name: str
    subject: str
    event_type: str
    event_label: str
    sentiment: float = Field(..., ge=-1, le=1)
    sentiment_label: str
    confidence: float
    impact_score: float = Field(..., ge=1, le=10)
    adverse_weight: float
    n_docs: int
    n_sources: int
    source_types: list[str]
    source_consensus: float
    affected_sectors: list[str]
    inferred_sectors: list[str]
    countries: list[str]
    affected_tickers: list[str]
    primary_ticker: Optional[str] = None
    high_impact: bool


class SignalList(BaseModel):
    total: int
    items: list[SignalItem]
    high_impact_threshold: float


class Factor(BaseModel):
    key: str
    label: str
    value: float
    weight: float
    contribution: float


class PortfolioExposure(BaseModel):
    portfolio_id: str
    portfolio_name: str
    value_before: float
    value_after: float
    pnl: float
    pct_impact: float
    main_driver: str


class SignalDetail(SignalItem):
    event_type_description: str
    first_seen: str
    last_seen: str
    source_agreement: float
    novelty: float
    total_articles: float
    matched_terms: list[str]
    raw_score: float
    factors: list[Factor]
    explanation: str
    summary: list[dict[str, str]] = []
    entities: list[dict[str, Any]]
    source_breakdown: list[dict[str, Any]]
    top_sources: list[dict[str, Any]]
    sample_documents: list[dict[str, Any]]
    portfolio_exposure: list[PortfolioExposure]
    top_url: Optional[str] = None


# ------------------------------------------------------------------ market
class MarketSummary(BaseModel):
    as_of: str
    newest_day: str
    is_newest: bool = True
    day_table: list[dict[str, Any]] = []
    as_of_time: Optional[str] = None
    partial_day: bool = False
    window_days: int
    data_mode: str
    market_risk: dict[str, Any]
    news_sentiment: dict[str, Any]
    top_sector: Optional[dict[str, Any]] = None
    sector_pressure: list[dict[str, Any]]
    risk_landscape: list[dict[str, Any]]
    event_mix: list[dict[str, Any]]
    top_signals: list[SignalItem]
    top_signals_window: list[SignalItem]
    high_impact_count: int
    high_impact_threshold: float
    market_backdrop: dict[str, Any]
    markets: list[dict[str, Any]] = []
    coverage: dict[str, Any]


# ------------------------------------------------------------------ portfolios
class PortfolioSummary(BaseModel):
    portfolio_id: str
    name: str
    strategy: str
    description: str
    valuation_date: str
    synthetic: bool
    total_value: float
    n_holdings: int
    by_asset_class: list[dict[str, Any]]
    equity_beta: Optional[float] = None
    bond_duration: float
    derivative_notional: float
    loan_notional: float
    priced_at: Optional[str] = None
    equities_live: bool = False


class PortfolioDetail(PortfolioSummary):
    by_sector: list[dict[str, Any]]
    top_holdings: list[dict[str, Any]]
    holdings: list[dict[str, Any]]


# ------------------------------------------------------------------ rebalancer
class RebalanceRequest(BaseModel):
    as_of: Optional[str] = Field(None, description="Replay date (YYYY-MM-DD); defaults to the latest signal date")
    max_turnover: Optional[float] = Field(None, gt=0, le=1)
    min_weight: Optional[float] = Field(None, ge=0, lt=1)
    max_weight: Optional[float] = Field(None, gt=0, le=1)
    max_sector_weight: Optional[float] = Field(None, gt=0, le=1)
    tilt_strength: Optional[float] = Field(None, ge=0, le=3)
    include_history: bool = True


class RebalanceResponse(BaseModel):
    as_of: str
    constraints: dict[str, Any]
    stocks: list[dict[str, Any]]
    metrics: dict[str, Any]
    history: list[dict[str, Any]]
    index: dict[str, Any]
    market_data: dict[str, Any] = {}
    available_dates: list[str]


# ------------------------------------------------------------------ stress
class StressRequest(BaseModel):
    portfolio_id: str
    event_id: Optional[str] = Field(None, description="Risk Signal to replay as a stress scenario")
    event_type: Optional[str] = Field(None, description="Custom scenario: one of the configured event types")
    impact_score: Optional[float] = Field(None, ge=1, le=10)
    sentiment: Optional[float] = Field(None, ge=-1, le=1)
    tickers: Optional[list[str]] = None
    sectors: Optional[list[str]] = None
    overrides: Optional[dict[str, float]] = Field(None, description="e.g. {'parallel_bp': 200, 'equity_default': -0.15}")


class StressResponse(BaseModel):
    portfolio_id: str
    portfolio_name: str
    event: dict[str, Any]
    assumptions: dict[str, Any]
    value_before: float
    value_after: float
    pnl: float
    pct_impact: float
    by_asset_class: list[dict[str, Any]]
    by_sector: list[dict[str, Any]]
    by_driver: list[dict[str, Any]]
    assets: list[dict[str, Any]]
    top_losers: list[dict[str, Any]]
    explanation: str
    disclaimer: str
    notes: list[str] = []
    impact_curve: list[dict[str, Any]] = []
    break_even: list[dict[str, Any]] = []
    portfolio_comparison: list[PortfolioExposure]


# ------------------------------------------------------------------ copilot
class CopilotRequest(BaseModel):
    message: str = Field(..., min_length=2, max_length=600)
    portfolio_id: str = "GGF"
    event_id: Optional[str] = None


class CopilotResponse(BaseModel):
    answer: str
    intent: str
    source: str
    llm: LLMStatus
    facts: dict[str, Any]
    suggested_prompts: list[str]
    note: Optional[str] = None
