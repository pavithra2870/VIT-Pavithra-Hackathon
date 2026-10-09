"""End-to-end preprocessing + Risk Engine pipeline.

    python -m src.risk_engine.pipeline [--sentiment auto|tfidf|finbert] [--retrain]

Raw data (/data) -> cleaning -> dedup -> entity extraction/normalisation -> sentiment -> event classification
-> source aggregation -> impact scoring -> risk signals -> /data/processed/*.csv
(the SQLite database is then built from those artifacts by `python -m src.backend.db.build`).
"""
from __future__ import annotations

import argparse
import json
import time

import numpy as np
import pandas as pd

from src.config import PROCESSED, engine_config
from src.risk_engine import gkg
from src.risk_engine.entity.extractor import EntityExtractor, Mention
from src.risk_engine.events.classifier import classify_event
from src.risk_engine.ingestion import loaders
from src.risk_engine.ingestion.market import build_market_context
from src.risk_engine.sentiment.model import TfidfSentiment, ensemble_scores, evaluate_backend, load_backend
from src.risk_engine.signals.builder import build_signals


def _t(msg: str, t0: float) -> float:
    print(f"[{time.time() - t0:6.1f}s] {msg}", flush=True)
    return time.time()


def refresh_market_context() -> dict:
    """Cheap refresh for when only market data changed (no new news): rewrite the market tables from the live prices."""
    ctx, idx = build_market_context()
    ctx.to_csv(PROCESSED / "market_context.csv", index=False)
    idx.tail(1500).to_csv(PROCESSED / "index_context.csv", index=False)
    return {"tickers": int(len(ctx)), "live": int((ctx["source"] == "live").sum()), "index_as_of": idx["date"].iloc[-1]}


def run(sentiment: str = "auto", retrain: bool = False) -> dict:
    PROCESSED.mkdir(parents=True, exist_ok=True)
    t0 = time.time()
    cfg = engine_config()

    # 1. ingest + clean + dedup -------------------------------------------------------------
    docs = loaders.load_all()
    _t(f"ingested {len(docs):,} documents ({docs['source_type'].value_counts().to_dict()})", t0)
    docs[docs["source_type"].isin(["news", "gdelt"])].to_csv(PROCESSED / "normalized_news.csv", index=False)
    docs[docs["source_type"] == "social"].to_csv(PROCESSED / "normalized_social.csv", index=False)

    # 2. market context ---------------------------------------------------------------------
    ctx, idx = build_market_context()
    ctx.to_csv(PROCESSED / "market_context.csv", index=False)
    idx.tail(1500).to_csv(PROCESSED / "index_context.csv", index=False)
    _t(f"market context: {len(ctx)} tickers, index vol percentile {idx['vol_pctile'].iloc[-1]:.2f}", t0)

    # 3. entities ---------------------------------------------------------------------------
    ex = EntityExtractor()
    ment_rows, primary, psector, sectors, doc_rel = [], [], [], [], []
    for r in docs.itertuples():
        e = ex.extract(r.text, r.title, slug_mode=(r.source_type == "gdelt"))
        if r.source_type == "gdelt" and (r.themes or r.orgs):
            # GKG enrichment: organisations the article names -> tickers; themes -> sectors (replaces headline-word guessing)
            have = {m.ticker for m in e.mentions}
            for t in gkg.link_orgs(gkg.split_themes(r.orgs)):
                if t not in have:
                    nm, sec, ctry = ex._meta(t)
                    e.mentions.append(Mention(t, nm, sec, ctry, 1, False, 0.5, 0.5))
            e.mentions.sort(key=lambda m: -m.relevance)
            if r.themes:
                e.sectors = list(dict.fromkeys([m.sector for m in e.mentions if m.sector != "Unknown"] + gkg.sector_tags(gkg.split_themes(r.themes))))
        if r.ticker_hint and not e.mentions:   # headline came from this company's Yahoo feed and names no company itself
            nm, sec, ctry = ex._meta(r.ticker_hint)
            e.mentions.append(Mention(r.ticker_hint, nm, sec, ctry, 1, True, 0.0, 0.6))
            e.mentions.sort(key=lambda m: -m.relevance)
            if sec != "Unknown" and sec not in e.sectors:
                e.sectors.insert(0, sec)
        for m in e.mentions:
            ment_rows.append({"doc_id": r.doc_id, "ticker": m.ticker, "name": m.name, "sector": m.sector, "country": m.country, "count": m.count,
                              "in_title": m.in_title, "relevance": m.relevance})
        top = e.mentions[0] if e.mentions else None
        primary.append(top.ticker if top and top.relevance >= 0.3 else "")
        psector.append((top.sector if top and top.sector != "Unknown" else (e.sectors[0] if e.sectors else "")) if (top or e.sectors) else "")
        sectors.append(";".join(e.sectors))
        doc_rel.append(top.relevance if top else 0.0)
    mentions = pd.DataFrame(ment_rows, columns=["doc_id", "ticker", "name", "sector", "country", "count", "in_title", "relevance"])
    mentions.to_csv(PROCESSED / "entity_mentions.csv", index=False)
    docs["primary_ticker"], docs["primary_sector"], docs["sectors"], docs["entity_rel"] = primary, psector, sectors, doc_rel
    _t(f"entities: {len(mentions):,} mentions across {mentions['doc_id'].nunique():,} docs", t0)

    # 4. sentiment --------------------------------------------------------------------------
    backend = TfidfSentiment(retrain=retrain) if sentiment == "tfidf" else load_backend(sentiment)
    if backend.name != "tfidf-lr" and backend.name not in json.loads((PROCESSED / "sentiment_model_eval.json").read_text() if (PROCESSED / "sentiment_model_eval.json").exists() else "{}"):
        _t(f"{backend.name} held-out eval: {evaluate_backend(backend)}", t0)
    prior = np.where(docs["source_type"] == "gdelt", (docs["avg_tone"] / cfg["sentiment"]["gdelt_tone_scale"]).clip(-1, 1), np.nan)
    sent = ensemble_scores(docs["text"].tolist(), prior, backend)
    docs = pd.concat([docs.reset_index(drop=True), sent], axis=1)
    docs["sentiment_backend"] = backend.name
    docs[["doc_id", "source_type", "dataset", "label", "model_score", "lexicon_score", "source_prior", "sentiment", "sentiment_confidence", "sentiment_backend"]].round(4).to_csv(
        PROCESSED / "sentiment_scores.csv", index=False)
    _t(f"sentiment ({backend.name}) done", t0)

    # 5. event classification ---------------------------------------------------------------
    res = [classify_event(r.text, r.cameo_root if r.source_type == "gdelt" else None, gkg.split_themes(r.themes) if r.themes else None) for r in docs.itertuples()]
    docs["event_type"] = [x.event_type for x in res]
    docs["event_confidence"] = [x.confidence for x in res]
    docs["event_evidence"] = [x.evidence for x in res]
    docs["matched_terms"] = [";".join(x.matched) for x in res]
    docs[["doc_id", "event_type", "event_confidence", "event_evidence", "matched_terms"]].to_csv(PROCESSED / "event_classifications.csv", index=False)
    # document-level market relevance: named company > named sector > keyword evidence (never zero for classified events)
    has_sector = docs["sectors"].str.len() > 0
    # a company/sector mention only counts as market-relevant in proportion to the textual evidence of an actual event
    gate = 0.35 + 0.65 * docs["event_evidence"]
    docs["doc_relevance"] = np.maximum.reduce([docs["entity_rel"] * gate, np.where(has_sector, 0.5, 0.0) * gate, 0.2 + 0.6 * docs["event_evidence"]])
    docs.loc[docs["event_type"] == "OTHER", "doc_relevance"] = np.minimum(docs["doc_relevance"], 0.15)
    _t(f"events: {docs['event_type'].value_counts().head(6).to_dict()}", t0)

    # 6. aggregation + impact scoring -> risk signals ---------------------------------------
    signals, sig_ent, sig_docs = build_signals(docs, mentions, ctx, idx)
    signals.to_csv(PROCESSED / "risk_signals.csv", index=False)
    sig_ent.to_csv(PROCESSED / "signal_entities.csv", index=False)
    sig_docs.to_csv(PROCESSED / "signal_documents.csv", index=False)
    docs[["doc_id", "source_type", "source_name", "dataset", "title", "url", "replay_ts", "primary_ticker", "sentiment", "event_type"]].to_csv(PROCESSED / "documents.csv", index=False)

    daily = signals.groupby("signal_date").agg(signals=("event_id", "count"), avg_impact=("impact_score", "mean"), max_impact=("impact_score", "max"),
                                                avg_sentiment=("sentiment", "mean")).round(4).reset_index()
    daily.to_csv(PROCESSED / "daily_risk.csv", index=False)
    summary = {
        "documents": int(len(docs)), "signals": int(len(signals)), "high_impact_signals": int((signals["impact_score"] >= cfg["scoring"]["high_impact_threshold"]).sum()),
        "impact_quantiles": signals["impact_score"].quantile([0.5, 0.9, 0.99]).round(2).to_dict(), "sentiment_backend": backend.name,
        "event_types": signals["event_type"].value_counts().to_dict(),
    }
    (PROCESSED / "pipeline_summary.json").write_text(json.dumps(summary, indent=2), encoding="utf-8")
    _t(f"signals: {summary}", t0)
    return summary


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--sentiment", default="auto", choices=["auto", "tfidf", "finbert"])
    ap.add_argument("--retrain", action="store_true")
    a = ap.parse_args()
    run(a.sentiment, a.retrain)
