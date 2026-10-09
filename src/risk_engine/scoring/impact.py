"""Deterministic, explainable impact scoring (1-10).

impact = 1 + 9 * (sum_i w_i * f_i) ** exponent        with weights/exponent from data/config/engine_config.json

Factors f_i (each 0-1):
  severity             event-class prior (taxonomy base_severity) scaled by observed intensity
                       (GDELT Goldstein magnitude, or keyword evidence strength for text)
  sentiment_magnitude  |aggregated sentiment|  - strong language in EITHER direction matters; polarity is NOT the score
  confidence           geometric mean of event-classification and sentiment confidence
  entity_relevance     how directly the text concerns identifiable companies / sectors / markets
  source_consensus     breadth (distinct sources, article volume), directional agreement, cross-source-type corroboration
  market_volatility    volatility regime of the affected name (or the S&P 500 index) as a percentile - events land harder in turbulent markets
  novelty              decays with the number of recent days the same event/entity already produced a signal
"""
from __future__ import annotations

import math
from dataclasses import dataclass

from src.config import engine_config, taxonomy

FACTOR_LABELS = {
    "severity": "Event severity",
    "sentiment_magnitude": "Sentiment strength",
    "confidence": "Model confidence",
    "entity_relevance": "Entity / market relevance",
    "source_consensus": "Source consensus",
    "market_volatility": "Market volatility regime",
    "novelty": "Novelty",
}


@dataclass
class ImpactResult:
    impact: float
    raw: float
    factors: dict[str, float]
    contributions: dict[str, float]


def base_severity(event_type: str) -> float:
    return taxonomy()["event_types"].get(event_type, taxonomy()["event_types"]["OTHER"])["base_severity"]


def compute_impact(factors: dict[str, float]) -> ImpactResult:
    cfg = engine_config()["scoring"]
    w = cfg["weights"]
    contrib = {k: round(w[k] * min(max(factors[k], 0.0), 1.0), 4) for k in w}
    raw = sum(contrib.values()) / sum(w.values())
    curve = cfg["impact_curve"]
    # fixed calibration window (documented in design.md): maps the observed raw-score range onto [0, 1] before the curve
    span = max(curve["raw_ceiling"] - curve["raw_floor"], 1e-6)
    scaled = min(max((raw - curve["raw_floor"]) / span, 0.0), 1.0)
    impact = curve["min"] + (curve["max"] - curve["min"]) * (scaled ** curve["exponent"])
    return ImpactResult(round(impact, 2), round(raw, 4), {k: round(v, 4) for k, v in factors.items()}, contrib)


def source_consensus(n_sources: int, total_articles: float, agreement: float, n_source_types: int) -> float:
    breadth = min(1.0, math.log1p(n_sources) / math.log1p(30))
    volume = min(1.0, math.log1p(total_articles) / math.log1p(5000))
    diversity = min(1.0, n_source_types / 3)
    return 0.35 * breadth + 0.15 * volume + 0.35 * agreement + 0.15 * diversity


def novelty(prior_days: int) -> float:
    return max(0.1, math.exp(-0.35 * prior_days))
