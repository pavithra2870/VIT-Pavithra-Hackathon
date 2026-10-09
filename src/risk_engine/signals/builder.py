"""Aggregate document-level analysis into structured Risk Signals (the engine's output contract).

A Risk Signal = one (entity-or-topic, event type, day) cluster of documents from one or more sources, carrying
sentiment (-1..1), event classification, impact score (1..10), confidence, consensus, factor breakdown,
affected tickers/sectors and a deterministic natural-language explanation.
"""
from __future__ import annotations

import json
import math
from collections import defaultdict

import numpy as np
import pandas as pd

from src.config import engine_config, stress_config, taxonomy
from src.risk_engine.ingestion.market import market_vol_percentile
from src.risk_engine.scoring.impact import FACTOR_LABELS, base_severity, compute_impact, novelty, source_consensus

FIPS = {"US": "United States", "UP": "Ukraine", "RS": "Russia", "IS": "Israel", "CH": "China", "IN": "India", "UK": "United Kingdom", "IR": "Iran",
        "GM": "Germany", "FR": "France", "JA": "Japan", "KS": "South Korea", "KN": "North Korea", "TW": "Taiwan", "SA": "Saudi Arabia", "TU": "Turkey",
        "PK": "Pakistan", "AF": "Afghanistan", "SY": "Syria", "IZ": "Iraq", "LE": "Lebanon", "EG": "Egypt", "BR": "Brazil", "MX": "Mexico", "CA": "Canada",
        "AS": "Australia", "SF": "South Africa", "NI": "Nigeria", "VE": "Venezuela", "SU": "Sudan", "YM": "Yemen", "GZ": "Gaza Strip", "WE": "West Bank",
        "EZ": "Czechia", "PL": "Poland", "SP": "Spain", "IT": "Italy", "ID": "Indonesia", "TH": "Thailand", "RP": "Philippines", "BG": "Bangladesh", "AE": "UAE",
        "QA": "Qatar", "ET": "Ethiopia", "KE": "Kenya", "CO": "Colombia", "AR": "Argentina", "CU": "Cuba", "NO": "Norway", "SW": "Sweden", "NL": "Netherlands"}


def _label(s: float) -> str:
    return "negative" if s <= -0.15 else ("positive" if s >= 0.15 else "neutral")


def adverse_weight(event_type: str, sentiment: float) -> float:
    """How adverse an event is for risk assets (0-1). Broad systemic classes are adverse unless sentiment is clearly positive
    (e.g. a ceasefire); targeted classes (M&A, earnings, launches) are only adverse when sentiment is negative."""
    scope = stress_config()["events"].get(event_type, {}).get("scope", "targeted")
    if scope == "broad":
        return float(np.clip(0.5 - 0.5 * sentiment, 0.0, 1.0))
    return float(np.clip(-sentiment, 0.0, 1.0))


def inferred_sectors(event_type: str, k: int = 3) -> list[str]:
    sec = stress_config()["events"].get(event_type, {}).get("equity", {}).get("sector", {})
    return [s for s, v in sorted(sec.items(), key=lambda kv: -abs(kv[1]))[:k]] if sec else []


def build_signals(docs: pd.DataFrame, mentions: pd.DataFrame, ctx: pd.DataFrame, idx: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """docs: one row per document with analysis columns. mentions: doc_id x ticker. Returns (signals, signal_entities, signal_docs)."""
    cfg = engine_config()
    tax = taxonomy()["event_types"]
    docs = docs.copy()
    docs["day"] = pd.to_datetime(docs["replay_ts"]).dt.strftime("%Y-%m-%d")

    # ---- assign each document to a signal cluster
    keys = []
    for r in docs.itertuples():
        if r.primary_ticker:
            k = ("T", r.primary_ticker)
        elif r.source_type == "gdelt":
            k = ("G", r.country or "GLOBAL")
        elif r.primary_sector:
            k = ("S", r.primary_sector)
        else:
            k = ("M", "MARKET")
        keys.append(k)
    docs["cluster_kind"] = [k[0] for k in keys]
    docs["cluster_key"] = [k[1] for k in keys]
    docs = docs[~((docs["event_type"] == "OTHER") & (docs["cluster_kind"].isin(["M", "S"])))]  # unclassifiable, entity-less chatter
    docs = docs[~((docs["event_type"] == "OTHER") & (docs["source_type"] == "gdelt"))]

    docs["attention"] = 1.0 + np.where(docs["source_type"] == "gdelt", np.log1p(docs["num_articles"].fillna(0)) / 2.0, 0.0)
    docs["weight"] = docs["sentiment_confidence"] * docs["doc_relevance"].clip(lower=0.05) * docs["attention"]

    rows, ent_rows, link_rows = [], [], []
    seen_days: dict[tuple, list[str]] = defaultdict(list)
    ordered = sorted(docs.groupby(["day", "cluster_kind", "cluster_key", "event_type"]), key=lambda kv: kv[0])
    counter: dict[str, int] = defaultdict(int)

    for (day, kind, key, etype), g in ordered:
        w = g["weight"].to_numpy()
        wsum = w.sum()
        sent = float((g["sentiment"].to_numpy() * w).sum() / wsum)
        sent_conf = float((g["sentiment_confidence"].to_numpy() * w).sum() / wsum)
        type_conf = float((g["event_confidence"].to_numpy() * w).sum() / wsum)
        evidence = float((g["event_evidence"].to_numpy() * w).sum() / wsum)
        n_docs = len(g)
        n_sources = g["source_name"].nunique()
        total_articles = float(np.where(g["source_type"] == "gdelt", g["num_articles"].fillna(1), 1).sum())
        s_types = sorted(g["source_type"].unique())
        signs = np.sign(np.where(np.abs(g["sentiment"]) < 0.05, 0, g["sentiment"]))
        agree = float((w * np.where(signs == 0, 0.5, (signs == np.sign(sent)).astype(float))).sum() / wsum)

        gd = g[g["source_type"] == "gdelt"]
        intensity = float(np.average(gd["goldstein"].abs().fillna(0) / 10.0, weights=gd["weight"])) if len(gd) else evidence
        intensity = min(1.0, 0.5 * intensity + 0.5 * evidence) if len(gd) else evidence
        severity = base_severity(etype) * (0.7 + 0.3 * intensity)

        # entities
        ment = mentions[mentions["doc_id"].isin(g["doc_id"])]
        ticker_stats = []
        if len(ment):
            for t, mg in ment.groupby("ticker"):
                dw = g.set_index("doc_id").loc[mg["doc_id"], "weight"].to_numpy()
                ds = g.set_index("doc_id").loc[mg["doc_id"], "sentiment"].to_numpy()
                ticker_stats.append({"ticker": t, "name": mg["name"].iloc[0], "sector": mg["sector"].iloc[0], "relevance": float(mg["relevance"].max()),
                                     "mentions": int(mg["count"].sum()), "docs": int(mg["doc_id"].nunique()), "sentiment": float((ds * dw).sum() / dw.sum())})
            ticker_stats.sort(key=lambda d: -d["relevance"])
        rel_max = float(g["doc_relevance"].max())
        entity_rel = 0.6 * rel_max + 0.4 * float((g["doc_relevance"] * g["weight"]).sum() / wsum)
        primary_ticker = key if kind == "T" else None
        vol_pct = market_vol_percentile(ctx, idx, primary_ticker)

        sig_key = (kind, key, etype)
        prior_days = len([d for d in seen_days[sig_key] if (pd.Timestamp(day) - pd.Timestamp(d)).days <= 7])
        seen_days[sig_key].append(day)

        factors = {
            "severity": severity,
            "sentiment_magnitude": abs(sent),
            "confidence": math.sqrt(max(type_conf, 1e-6) * max(sent_conf, 1e-6)),
            "entity_relevance": entity_rel,
            "source_consensus": source_consensus(n_sources, total_articles, agree, len(s_types)),
            "market_volatility": vol_pct,
            "novelty": novelty(prior_days),
        }
        res = compute_impact(factors)

        # sectors
        sectors = [s for s, _ in sorted(pd.Series([s for ts in ticker_stats for s in [ts["sector"]] if s != "Unknown"] + [x for sl in g["sectors"] for x in sl.split(";") if x]).value_counts().items(), key=lambda kv: -kv[1])][:4] if len(g) else []
        sectors = list(dict.fromkeys(sectors))
        inferred = [s for s in inferred_sectors(etype) if s not in sectors] if not sectors or kind in ("G", "M") else []

        rep = g.sort_values("weight", ascending=False).iloc[0]
        countries = sorted({FIPS.get(c, c) for c in g["country"] if c})[:3]
        if kind == "T":
            subject = ticker_stats[0]["name"] if ticker_stats and ticker_stats[0]["ticker"] == key else key
        elif kind == "G":
            subject = FIPS.get(key, key) if key != "GLOBAL" else "Global"
        elif kind == "S":
            subject = f"{key} sector"
        else:
            subject = "Broad market"
        counter[day] += 1
        eid = f"SIG-{day.replace('-', '')}-{counter[day]:04d}"
        adv = adverse_weight(etype, sent)
        top_factors = sorted(res.contributions.items(), key=lambda kv: -kv[1])[:3]
        expl = (
            f"{tax[etype]['label']} signal for {subject}: {n_docs} document{'s' if n_docs != 1 else ''} from {n_sources} source{'s' if n_sources != 1 else ''} "
            f"({', '.join(s_types)}) with {_label(sent)} aggregate sentiment ({sent:+.2f}). Impact {res.impact:.1f}/10 - largest contributors: "
            + ", ".join(f"{FACTOR_LABELS[k].lower()} ({v / sum(res.contributions.values()):.0%} of the raw score)" for k, v in top_factors)
            + f". Source agreement {agree:.0%}; classification confidence {type_conf:.0%}."
        )
        rows.append({
            "event_id": eid, "signal_date": day, "first_seen": g["replay_ts"].min(), "last_seen": g["replay_ts"].max(),
            "event_name": str(rep["title"])[:180], "subject": subject, "event_type": etype, "event_label": tax[etype]["label"],
            "cluster_kind": kind, "cluster_key": key, "primary_ticker": primary_ticker or (ticker_stats[0]["ticker"] if ticker_stats else ""),
            "sentiment": round(sent, 4), "sentiment_label": _label(sent), "confidence": round(factors["confidence"], 4),
            "impact_score": res.impact, "raw_score": res.raw, "adverse_weight": round(adv, 4),
            "n_docs": n_docs, "n_sources": int(n_sources), "total_articles": total_articles, "source_types": ";".join(s_types),
            "source_consensus": round(factors["source_consensus"], 4), "source_agreement": round(agree, 4), "novelty": round(factors["novelty"], 4),
            "affected_sectors": ";".join(sectors), "inferred_sectors": ";".join(inferred), "countries": ";".join(countries),
            "affected_tickers": ";".join(t["ticker"] for t in ticker_stats[:8]),
            "matched_terms": ";".join(sorted({m for ms in g["matched_terms"] for m in ms.split(";") if m})[:8]),
            "factors_json": json.dumps({"factors": res.factors, "contributions": res.contributions, "weights": engine_config()["scoring"]["weights"]}),
            "explanation": expl, "top_url": rep["url"], "top_source": rep["source_name"],
        })
        for ts in ticker_stats:
            ent_rows.append({"event_id": eid, "ticker": ts["ticker"], "name": ts["name"], "sector": ts["sector"], "relevance": round(ts["relevance"], 4),
                             "mentions": ts["mentions"], "docs": ts["docs"], "sentiment": round(ts["sentiment"], 4)})
        for d in g.itertuples():
            link_rows.append({"event_id": eid, "doc_id": d.doc_id})

    signals = pd.DataFrame(rows)
    return signals, pd.DataFrame(ent_rows), pd.DataFrame(link_rows)
