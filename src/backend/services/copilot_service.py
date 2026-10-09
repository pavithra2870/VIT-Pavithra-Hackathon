"""AI Risk Copilot: intent routing -> deterministic facts from backend services -> template answer, optionally rephrased by Ollama.

The LLM only ever sees (and may only quote) facts produced by the Risk Engine / Stress / Rebalancer services.
"""
from __future__ import annotations

import json
import re

from src.backend.services import llm, market_service, portfolio_service, rebalance_service, signals_service, store
from src.backend.services.stress_service import compare_all, resolve_event, stress

FOLLOWUPS = ["Why is my portfolio exposed to this event?", "Which holdings are most vulnerable?", "Why did my risk score increase?",
             "What happens if rates rise 2%?", "Explain this stress-test result."]


def _py(o):
    """json default: numpy / pandas scalars -> plain python."""
    if hasattr(o, "item"):
        return o.item()
    if hasattr(o, "tolist"):
        return o.tolist()
    return str(o)


def _fmt_usd(x: float) -> str:
    a = abs(x)
    s = f"{a / 1e9:.2f}B" if a >= 1e9 else f"{a / 1e6:.1f}M" if a >= 1e6 else f"{a:,.0f}"
    return ("-" if x < 0 else "+") + "$" + s


def _intent(msg: str) -> str:
    m = msg.lower()
    if re.search(r"rates?\b.*(rise|rises|up|hike|increase|\d\s*%)|(\d+(\.\d+)?)\s*(%|percent|bp|bps)", m) and ("rate" in m or "yield" in m):
        return "rate_shock"
    if "vulnerab" in m or "most exposed" in m or "worst" in m or "biggest loss" in m:
        return "vulnerable"
    if "risk score" in m or "increase" in m and "risk" in m or "why did" in m:
        return "risk_change"
    if "explain" in m and ("stress" in m or "result" in m):
        return "explain_stress"
    if "rebalanc" in m or "weights" in m:
        return "rebalance"
    if "exposed" in m or "exposure" in m:
        return "exposure"
    return "overview"


def _parse_bp(msg: str) -> float:
    m = re.search(r"(\d+(?:\.\d+)?)\s*(%|percent|bp|bps)", msg.lower())
    if not m:
        return 200.0
    v = float(m.group(1))
    return v * 100 if m.group(2) in ("%", "percent") else v


def _default_event(event_id: str | None, portfolio_id: str):
    if event_id:
        return event_id
    top = signals_service.list_signals(limit=1, sort="impact", date=market_service.market_summary()["as_of"])["items"]
    return top[0]["event_id"] if top else store.signals().sort_values("impact_score", ascending=False).iloc[0]["event_id"]


def answer(message: str, portfolio_id: str, event_id: str | None) -> dict:
    intent = _intent(message)
    portfolio = portfolio_service.portfolio_detail(portfolio_id)
    facts: dict = {"portfolio": {"id": portfolio_id, "name": portfolio["name"], "total_value_usd": round(portfolio["total_value"]), "allocation": {a["key"]: round(a["weight"], 4) for a in portfolio["by_asset_class"]}}}
    text = ""

    if intent == "rate_shock":
        bp = _parse_bp(message)
        ev = resolve_event(None, "INTEREST_RATE", 10.0, -0.5, None, None)
        res = stress(portfolio_id, ev, {"parallel_bp": bp})
        facts["scenario"] = {"type": "parallel rate shock", "shift_bp": bp, "note": "custom scenario, all other shocks per INTEREST_RATE assumptions at impact 10"}
        facts["result"] = {k: res[k] for k in ("value_before", "value_after", "pnl", "pct_impact")} | {"by_driver": res["by_driver"], "top_losers": res["top_losers"]}
        worst = ", ".join("%s (%s)" % (x["name"], _fmt_usd(x["pnl"])) for x in res["top_losers"][:3])
        text = (f"A +{bp:.0f}bp parallel rate shock (with the interest-rate scenario's equity/credit read-across) changes {portfolio['name']} by {res['pct_impact']:+.2%} "
                f"({_fmt_usd(res['pnl'])}). Biggest driver: {res['by_driver'][0]['label'].lower()} ({_fmt_usd(res['by_driver'][0]['pnl'])}). "
                f"Most affected positions: {worst}. Simplified prototype model - illustrative only.")
    else:
        eid = _default_event(event_id, portfolio_id)
        ev = resolve_event(eid, None, None, None, None, None)
        res = stress(portfolio_id, ev)
        cmp_ = compare_all(ev)
        detail = signals_service.signal_detail(eid)
        facts["event"] = {"id": eid, "name": ev.name, "type": ev.event_type, "impact_score": ev.impact_score, "sentiment": round(ev.sentiment, 3), "confidence": round(detail["confidence"], 3),
                          "n_sources": detail["n_sources"], "top_risk_factors": [{"factor": f["label"], "contribution": round(f["contribution"], 3)} for f in detail["factors"][:3]]}
        facts["stress_result"] = {k: res[k] for k in ("value_before", "value_after", "pnl", "pct_impact")} | {"by_driver": res["by_driver"][:4], "by_asset_class": res["by_asset_class"], "top_losers": res["top_losers"]}
        facts["same_event_other_portfolios"] = cmp_
        loser = res["top_losers"][0] if res["top_losers"] else None
        if intent == "vulnerable":
            lst = ", ".join("%s (%s)" % (x["name"], _fmt_usd(x["pnl"])) for x in res["top_losers"][:5])
            text = f"Under '{ev.name}' ({ev.event_type.replace('_', ' ').title()}, impact {ev.impact_score:.1f}/10) the most vulnerable holdings in {portfolio['name']} are: {lst}."
        elif intent == "risk_change":
            d = market_service.market_summary()
            r = d["market_risk"]
            top3 = ", ".join(f"{f['factor'].lower()}" for f in facts["event"]["top_risk_factors"])
            facts["market_risk"] = r
            text = (f"The market risk index is {r['index']:.1f}/10 ({r['label']}), {r['change_1d']:+.2f} versus the previous day. The leading signal '{ev.name}' scores {ev.impact_score:.1f}/10 mainly because of "
                    f"{top3}. For {portfolio['name']}, replaying it as a stress scenario implies {res['pct_impact']:+.2%}.")
        elif intent == "rebalance":
            rb = rebalance_service.run(None, {}, include_history=False)
            mv = sorted(rb["stocks"], key=lambda s: s["change"])
            facts["rebalance"] = {"turnover": rb["metrics"]["turnover"], "largest_cuts": [(s["ticker"], round(s["change"], 4), s["driver"]) for s in mv[:3]], "largest_adds": [(s["ticker"], round(s["change"], 4), s["driver"]) for s in mv[-3:]]}
            cuts = ", ".join("%s (%+.2f%%)" % (s["ticker"], s["change"] * 100) for s in mv[:3])
            adds = ", ".join("%s (%+.2f%%)" % (s["ticker"], s["change"] * 100) for s in mv[::-1][:3])
            text = f"The tactical rebalancer proposes {rb['metrics']['turnover']:.1%} turnover as of {rb['as_of']}. Largest reductions: {cuts}. Largest additions: {adds}."

        else:  # exposure / explain_stress / overview
            others = "; ".join("%s %+.2f%%" % (c["portfolio_name"], c["pct_impact"] * 100) for c in cmp_)
            tail = res["explanation"].split(". ", 1)[1] if ". " in res["explanation"] else ""
            text = (f"{portfolio['name']} would move {res['pct_impact']:+.2%} ({_fmt_usd(res['pnl'])}) if '{ev.name}' ({ev.event_type.replace('_', ' ').title()}, impact {ev.impact_score:.1f}/10) played out. "
                    f"{tail} Same event across portfolios: {others}. Exposure, not the event alone, drives the outcome.")
    facts = json.loads(json.dumps(facts, default=_py))
    status = llm.status()
    source, note = "template", None
    if status["available"]:
        try:
            text_llm = llm.chat(message, json.dumps(facts), status["model"])
            if text_llm:
                text, source = text_llm, f"ollama:{status['model']}"
        except Exception as exc:  # keep the deterministic answer
            note = f"LLM call failed ({type(exc).__name__}); showing the deterministic explanation."
    return {"answer": text, "intent": intent, "source": source, "llm": status, "facts": facts, "suggested_prompts": FOLLOWUPS, "note": note}
