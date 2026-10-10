"""Unit + API tests. Run: pytest -q"""
import numpy as np
import pandas as pd
import pytest
from fastapi.testclient import TestClient

from src.modules.rebalancer.engine import enforce_sector_cap, project_weights, rebalance
from src.modules.stress_testing.engine import StressEvent, run_stress
from src.risk_engine.entity.extractor import EntityExtractor
from src.risk_engine.events.classifier import classify_event
from src.risk_engine.scoring.impact import compute_impact


def test_classifier_basic():
    assert classify_event("Fed announces interest rate hike of 25 basis points").event_type == "INTEREST_RATE"
    assert classify_event("Company files for bankruptcy after debt default").event_type == "CREDIT_EVENT"
    assert classify_event("hello world").event_type == "OTHER"
    assert classify_event("random headline", cameo_root="19").confidence <= 0.40  # CAMEO-only label is low confidence


def test_entities():
    ex = EntityExtractor()
    e = ex.extract("Nvidia and Microsoft rally; $TSLA drops", "Nvidia and Microsoft rally")
    assert {m.ticker for m in e.mentions} >= {"NVDA", "MSFT", "TSLA"}


def test_impact_not_polarity_driven():
    base = dict(severity=0.5, sentiment_magnitude=0.5, confidence=0.5, entity_relevance=0.5, source_consensus=0.5, market_volatility=0.5, novelty=0.5)
    assert 1 <= compute_impact(base).impact <= 10
    hi = {**base, "severity": 1, "source_consensus": 1, "entity_relevance": 1}
    assert compute_impact(hi).impact > compute_impact(base).impact


def test_project_weights_constraints():
    w = project_weights(np.array([0.5, 0.3, 0.1, 0.05, 0.05]), 0.05, 0.3)
    assert abs(w.sum() - 1) < 1e-9 and w.max() <= 0.3 + 1e-9 and w.min() >= 0.05 - 1e-9
    s = enforce_sector_cap(w, np.array(["a", "a", "b", "b", "c"]), 0.5, 0.05, 0.3)
    assert abs(s.sum() - 1) < 1e-9


def test_capped_weights():
    from src.modules.rebalancer.engine import capped_weights

    w = capped_weights({"A": 100, "B": 50, "C": 10, "D": 5}, 0.4)
    assert abs(sum(w.values()) - 1) < 1e-9 and max(w.values()) <= 0.4 + 1e-9 and w["A"] == pytest.approx(0.4)


def test_live_prices_overlay_replaces_snapshot_columns():
    from src.risk_engine.ingestion import market

    snap, live = market.load_prices(live=False), market.load_prices(live=True)
    assert set(snap.columns) <= set(live.columns)
    assert live.index.max() >= snap.index.max()          # live data never shortens history


def _holdings():
    return pd.read_csv("data/portfolios/holdings.csv")


def test_stress_differs_by_portfolio_and_scales_with_impact():
    h = _holdings()
    res = {p: run_stress(g, StressEvent("GEOPOLITICAL", 8, -0.5)) for p, g in h.groupby("portfolio_id")}
    assert res["GGF"]["pct_impact"] < res["CAF"]["pct_impact"] < 0       # growth fund hurt more than conservative
    lo = run_stress(h[h.portfolio_id == "GGF"], StressEvent("GEOPOLITICAL", 4, -0.5))
    assert abs(lo["pnl"]) < abs(res["GGF"]["pnl"])
    r = res["WBP"]
    assert abs(r["value_after"] - r["value_before"] - r["pnl"]) < 1.0
    assert abs(sum(a["pnl"] for a in r["assets"]) - r["pnl"]) < 1.0       # asset level reconciles to total


def test_rate_shock_hurts_duration():
    h = _holdings()
    caf = run_stress(h[h.portfolio_id == "CAF"], StressEvent("INTEREST_RATE", 10, -0.5))
    assert caf["pct_impact"] < -0.05
    assert any(d["key"] == "rates" and d["pnl"] < 0 for d in caf["by_driver"])


def test_rebalance_constraints():
    sig = pd.read_csv("data/processed/risk_signals.csv"); ent = pd.read_csv("data/processed/signal_entities.csv")
    ctx = pd.read_csv("data/processed/market_context.csv"); idx = pd.read_csv("data/portfolios/mock_index.csv")
    r = rebalance(idx, sig, ent, ctx, pd.Timestamp(sig["signal_date"].max()))
    w = np.array([s["proposed_weight"] for s in r["stocks"]])
    assert abs(w.sum() - 1) < 1e-4 and w.max() <= 0.15 + 1e-6 and w.min() >= 0.01 - 1e-6
    assert r["metrics"]["turnover"] <= 0.10 + 1e-6


@pytest.fixture(scope="module")
def client():
    from src.backend.main import app
    return TestClient(app)


def test_api_end_to_end(client):
    assert client.get("/api/health").json()["status"] == "ok"
    sigs = client.get("/api/risk-signals?limit=5").json()
    assert sigs["total"] > 0 and 1 <= sigs["items"][0]["impact_score"] <= 10
    eid = sigs["items"][0]["event_id"]
    assert len(client.get(f"/api/risk-signals/{eid}").json()["factors"]) == 7
    assert len(client.get("/api/portfolios").json()) >= 3
    st = client.post("/api/stress-test", json={"portfolio_id": "WBP", "event_id": eid}).json()
    assert st["value_before"] > 0 and len(st["portfolio_comparison"]) == 4
    assert client.post("/api/rebalance", json={"include_history": False}).status_code == 200
    cp = client.post("/api/copilot", json={"message": "What happens if rates rise 2%?", "portfolio_id": "CAF"}).json()
    assert cp["answer"] and cp["source"] in ("template",) or cp["source"].startswith("ollama")
    assert client.get("/api/risk-signals/NOPE").status_code == 404


def test_stress_curve_ranking_and_notice():
    from src.backend.services import stress_service as ss

    ev = StressEvent("GEOPOLITICAL", 6, 0.5)                       # positive tone + broad scope -> adverse-case notice
    res = run_stress(_holdings()[lambda d: d.portfolio_id == "GGF"], ev)
    assert res["notes"] and "ADVERSE" in res["notes"][0]
    curve, be = ss.impact_curve("GGF", StressEvent("GEOPOLITICAL", 6, -0.5))
    losses = [c["pct_impact"] for c in curve]
    assert losses == sorted(losses, reverse=True)                  # loss deepens as impact rises
    reached = [b["impact"] for b in be if b["impact"] is not None]
    assert reached == sorted(reached)                              # bigger loss needs a bigger impact
    rank = ss.rank_events("WBP", limit=5)
    assert [r["pnl"] for r in rank] == sorted(r["pnl"] for r in rank)
