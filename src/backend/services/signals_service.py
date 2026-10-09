"""Risk Signal service: query + detail view over the SQLite-backed signals."""
from __future__ import annotations

import json

import pandas as pd
from fastapi import HTTPException

from src.backend.db.database import query_df
from src.backend.services import store
from src.config import taxonomy
from src.risk_engine.scoring.impact import FACTOR_LABELS

LIST_COLS = ["event_id", "signal_date", "event_name", "subject", "event_type", "event_label", "sentiment", "sentiment_label", "confidence", "impact_score", "adverse_weight",
             "n_docs", "n_sources", "source_types", "source_consensus", "affected_sectors", "inferred_sectors", "countries", "affected_tickers", "primary_ticker"]


def _row_to_item(r: pd.Series) -> dict:
    d = {c: (None if pd.isna(r[c]) else r[c]) for c in LIST_COLS}
    for k in ("source_types", "affected_sectors", "inferred_sectors", "countries", "affected_tickers"):
        d[k] = store.split(d[k])
    d["high_impact"] = bool(d["impact_score"] >= store.high_impact_threshold())
    return d


def list_signals(event_type: str | None = None, min_impact: float = 0.0, sentiment: str | None = None, date: str | None = None, ticker: str | None = None,
                 sector: str | None = None, q: str | None = None, sort: str = "date", limit: int = 50, offset: int = 0, source_type: str | None = None) -> dict:
    df = store.signals()
    if event_type:
        df = df[df["event_type"] == event_type]
    if min_impact:
        df = df[df["impact_score"] >= min_impact]
    if sentiment:
        df = df[df["sentiment_label"] == sentiment]
    if date:
        df = df[df["signal_date"] == date]
    if ticker:
        df = df[df["affected_tickers"].str.split(";").map(lambda l: ticker in l) | (df["primary_ticker"] == ticker)]
    if sector:
        df = df[df["affected_sectors"].str.split(";").map(lambda l: sector in l) | df["inferred_sectors"].str.split(";").map(lambda l: sector in l)]
    if source_type:
        df = df[df["source_types"].str.contains(source_type)]
    if q:
        m = df["event_name"].str.contains(q, case=False, na=False) | df["subject"].str.contains(q, case=False, na=False)
        df = df[m]
    key = {"impact": ("impact_score", False), "date": ("last_seen", False), "confidence": ("confidence", False), "sentiment": ("sentiment", True)}.get(sort, ("last_seen", False))
    df = df.sort_values([key[0], "impact_score"], ascending=[key[1], False])   # "date" = most recently updated first
    total = len(df)
    page = df.iloc[offset: offset + limit]
    return {"total": total, "items": [_row_to_item(r) for _, r in page.iterrows()], "high_impact_threshold": store.high_impact_threshold()}


def _term(t: str) -> str:
    """Show CAMEO codes as readable labels, e.g. cameo:19 -> 'CAMEO 19: armed conflict'."""
    if t.startswith("cameo:"):
        code = t.split(":", 1)[1]
        return f"CAMEO {code}: {taxonomy()['cameo_root_labels'].get(code, 'event code')}"
    if t.startswith("theme:"):
        return "GKG theme: " + t.split(":", 1)[1].replace("_", " ").lower()
    return t


def get_signal_row(event_id: str) -> pd.Series:
    df = store.signals()
    m = df[df["event_id"] == event_id]
    if m.empty:
        raise HTTPException(404, f"Unknown event {event_id}")
    return m.iloc[0]


def signal_detail(event_id: str) -> dict:
    r = get_signal_row(event_id)
    item = _row_to_item(r)
    fj = json.loads(r["factors_json"])
    w = fj["weights"]
    total_w = sum(w.values())
    factors = [{"key": k, "label": FACTOR_LABELS[k], "value": fj["factors"][k], "weight": w[k] / total_w, "contribution": fj["contributions"][k] / total_w} for k in w]
    ent = store.signal_entities()
    ent = ent[ent["event_id"] == event_id].sort_values("relevance", ascending=False)
    docs = query_df(
        "SELECT d.doc_id, d.source_type, d.source_name, d.dataset, d.title, d.url, d.sentiment FROM signal_documents sd JOIN documents d ON d.doc_id = sd.doc_id WHERE sd.event_id = ?", (event_id,))
    docs["abs_s"] = docs["sentiment"].abs()
    breakdown = docs.groupby("source_type").agg(documents=("doc_id", "count"), sentiment=("sentiment", "mean")).reset_index().round(4).to_dict("records")
    top_sources = docs["source_name"].value_counts().head(8).rename_axis("source").reset_index(name="documents").to_dict("records")
    sample = docs.sort_values("abs_s", ascending=False).head(10).drop(columns="abs_s")
    from src.backend.services.stress_service import compare_all, resolve_event

    exposure = compare_all(resolve_event(event_id, None, None, None, None, None))
    raw = float(r["raw_score"]) or 1.0
    top3 = sorted(factors, key=lambda f: -f["contribution"])[:3]
    where = ", ".join([item["subject"]] + [c for c in item["countries"] if c != item["subject"]])
    types = " + ".join(item["source_types"])
    summary = [
        {"label": "What", "text": f"{item['event_label']} event concerning {where}, based on {item['n_docs']} document{'s' if item['n_docs'] != 1 else ''} from {item['n_sources']} source{'s' if item['n_sources'] != 1 else ''} ({types})."},
        {"label": "Tone", "text": f"{item['sentiment_label'].capitalize()} ({item['sentiment']:+.2f}); {r['source_agreement']:.0%} of the weighted sources agree on the direction."},
        {"label": f"Why {item['impact_score']:.1f} / 10", "text": "Driven mainly by " + ", ".join(f"{f['label'].lower()} ({f['contribution'] / raw:.0%})" for f in top3) + " of the raw score."},
        {"label": "Reliability", "text": f"Overall confidence {item['confidence']:.0%}, source consensus {item['source_consensus']:.0%}, novelty {r['novelty']:.0%}."},
    ]
    return {
        **item, "event_type_description": taxonomy()["event_types"][item["event_type"]]["label"], "first_seen": r["first_seen"], "last_seen": r["last_seen"],
        "source_agreement": r["source_agreement"], "novelty": r["novelty"], "total_articles": r["total_articles"], "matched_terms": [_term(t) for t in store.split(r["matched_terms"])],
        "raw_score": r["raw_score"], "factors": sorted(factors, key=lambda f: -f["contribution"]), "explanation": r["explanation"], "summary": summary,
        "entities": ent.drop(columns="event_id").to_dict("records"), "source_breakdown": breakdown, "top_sources": top_sources,
        "sample_documents": [{k: (None if pd.isna(v) else v) for k, v in d.items()} for d in sample.to_dict("records")],
        "portfolio_exposure": exposure, "top_url": r["top_url"] if pd.notna(r["top_url"]) else None,
    }
