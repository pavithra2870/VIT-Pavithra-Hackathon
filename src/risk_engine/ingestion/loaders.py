"""Source loaders: every raw dataset is normalised into ONE common document schema.

Common columns
--------------
doc_id, source_type (news|social|gdelt), source_name, dataset, original_date, replay_ts,
title, text, url, label (-1/0/1 gold sentiment where the dataset provides one),
and GDELT-only structure: cameo_root, goldstein, avg_tone, num_mentions, num_sources,
num_articles, country, actor1, actor2.

`replay_ts` places historical corpora onto the live replay window (see design.md): GDELT keeps its
own DATEADDED day, every other dataset is spread deterministically (seeded) across the same window.
"""
from __future__ import annotations

import glob
import re
from pathlib import Path

import numpy as np
import pandas as pd

from src.config import GKG_DIR, GKG_LIVE, LIVE_GDELT, MARKET, NEWS, RAW_GDELT, SOCIAL, engine_config
from src.risk_engine.ingestion.clean import clean_text, deduplicate, domain_of, slug_to_headline

COLUMNS = [
    "doc_id", "source_type", "source_name", "dataset", "original_date", "replay_ts", "title", "text", "url", "label",
    "cameo_root", "goldstein", "avg_tone", "num_mentions", "num_sources", "num_articles", "country", "actor1", "actor2", "themes", "orgs", "ticker_hint",
]

GDELT_COLS = {  # GDELT 1.0 event export (58 columns, tab separated)
    0: "event_id", 1: "sql_date", 6: "actor1", 16: "actor2", 28: "cameo_root", 26: "event_code", 30: "goldstein",
    31: "num_mentions", 32: "num_sources", 33: "num_articles", 34: "avg_tone", 50: "geo_name", 51: "country", 56: "date_added", 57: "url",
}


def _replay_timestamps(n: int, seed: int) -> pd.Series:
    rng = np.random.default_rng(seed)
    # replay window = span of the COMPLETED GDELT daily files (never the in-progress live day, so replayed documents
    # cannot appear to be newer than the newest real event); fresh daily files extend the window automatically
    dates = sorted(pd.Timestamp(Path(f).name[:8]) for d in (RAW_GDELT,) for f in glob.glob(str(d / "*.export.CSV")))
    start = dates[0] if dates else pd.Timestamp(engine_config()["ingestion"]["replay_start"])
    span = (dates[-1] - dates[0]).days + 1 if dates else engine_config()["ingestion"]["replay_days"]
    days = rng.integers(0, span, n)
    secs = rng.integers(0, 86400, n)
    return pd.Series(start + pd.to_timedelta(days, unit="D") + pd.to_timedelta(secs, unit="s"))


def _finish(df: pd.DataFrame, dataset: str, source_type: str, seed: int, prefix: str) -> pd.DataFrame:
    df = df.copy()
    df["dataset"] = dataset
    df["source_type"] = source_type
    df["doc_id"] = [f"{prefix}-{i:06d}" for i in range(len(df))]
    df["replay_ts"] = _replay_timestamps(len(df), seed)
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan if c in ("label", "goldstein", "avg_tone", "num_mentions", "num_sources", "num_articles") else ""
    return df[COLUMNS]


# --------------------------------------------------------------------------- news
def load_financial_phrasebank(n: int, seed: int) -> pd.DataFrame:
    df = pd.read_csv(NEWS / "FinancialPhraseBank.csv", header=None, names=["sentiment", "text"], encoding="latin-1")
    df = df.dropna().sample(min(n, len(df)), random_state=seed)
    df["label"] = df["sentiment"].map({"negative": -1, "neutral": 0, "positive": 1})
    df["text"] = df["text"].map(clean_text)
    df["title"] = df["text"].str.slice(0, 140)
    df["source_name"] = "FinancialPhraseBank"
    return _finish(df, "FinancialPhraseBank", "news", seed, "fpb")


def load_reuters(n: int, seed: int) -> pd.DataFrame:
    df = pd.read_excel(NEWS / "Reuters-Finance-News.xlsx")
    df = df.dropna(subset=["Headline"]).sample(min(n, len(df)), random_state=seed)
    df["title"] = df["Headline"].map(clean_text)
    body = df["Summary"].fillna("").map(clean_text)
    art = df["Article"].fillna("").map(clean_text).str.slice(0, 900)
    df["text"] = (df["title"] + ". " + body.where(body.str.len() > 20, art)).str.strip()
    df["original_date"] = df["Date"].astype(str)
    df["url"] = df["Link"].fillna("")
    df["source_name"] = "Reuters"
    return _finish(df, "Reuters-Finance-News", "news", seed, "reu")


def load_indian_news(n: int, seed: int) -> pd.DataFrame:
    # the 110 MB source file is too large for GitHub: the repo ships the exact 5,000-row sample this step draws from it
    path = NEWS / "indian-finance-news.csv"
    if not path.exists():
        path = NEWS / "indian-finance-news-sample.csv"
    df = pd.read_csv(path, usecols=["URL", "Summary"], engine="c").dropna(subset=["Summary"])
    if len(df) > n:                       # the shipped sample is already n rows: keep its order, do not reshuffle
        df = df.sample(n, random_state=seed)
    df["text"] = df["Summary"].map(clean_text).str.slice(0, 700)
    df["title"] = df["text"].str.split(r"(?<=[.!?]) ", n=1, regex=True).str[0].str.slice(0, 160)
    df["url"] = df["URL"]
    df["source_name"] = df["URL"].map(domain_of).replace("", "indian-finance-news")
    return _finish(df, "Indian-Finance-News", "news", seed, "ind")


def load_yfinance_news() -> pd.DataFrame:
    """Live company headlines from Yahoo Finance (data/market/yfinance/news.csv, written by fetch_market). Optional."""
    path = MARKET / "yfinance" / "news.csv"
    if not path.exists():
        return pd.DataFrame(columns=COLUMNS)
    n = pd.read_csv(path, dtype=str).dropna(subset=["title"])
    start = pd.Timestamp(engine_config()["ingestion"]["replay_start"])
    df = pd.DataFrame({
        "title": n["title"].map(clean_text), "text": n["title"].map(clean_text), "url": n["link"].fillna(""),
        "source_name": "Yahoo Finance · " + n["publisher"].fillna(""), "ticker_hint": n["ticker"],
        "original_date": n["published_utc"], "replay_ts": pd.to_datetime(n["published_utc"], errors="coerce"),   # real publication time (UTC)
    })
    df = df.dropna(subset=["replay_ts"])
    df = df[df["replay_ts"] >= start]
    df["dataset"], df["source_type"] = "yfinance-news", "news"
    df["doc_id"] = [f"yfn-{i:05d}" for i in range(len(df))]
    for c in COLUMNS:
        if c not in df.columns:
            df[c] = np.nan if c in ("label", "goldstein", "avg_tone", "num_mentions", "num_sources", "num_articles") else ""
    return df[COLUMNS]


# --------------------------------------------------------------------------- social
def load_financial_tweets(n: int, seed: int) -> pd.DataFrame:
    df = pd.read_excel(SOCIAL / "financial-tweets-senti.xlsx").dropna(subset=["tweet"])
    df = df.sample(min(n, len(df)), random_state=seed)
    df["label"] = df["sentiment"].map({0: -1, 1: 1, 2: 0})  # zeroshot/twitter-financial-news-sentiment: 0 bearish, 1 bullish, 2 neutral
    df["text"] = df["tweet"].map(clean_text)
    df["title"] = df["text"].str.slice(0, 140)
    df["source_name"] = "Twitter/X"
    return _finish(df, "Financial-Tweets-Sentiment", "social", seed, "ftw")


def load_twitter_general(n: int, seed: int) -> pd.DataFrame:
    df = pd.read_csv(SOCIAL / "Twitter_Data.csv").dropna()
    df = df.sample(min(n, len(df)), random_state=seed)
    df["label"] = df["category"].astype(float).astype(int)
    df["text"] = df["clean_text"].map(clean_text)
    df["title"] = df["text"].str.slice(0, 140)
    df["source_name"] = "Twitter/X"
    return _finish(df, "Twitter-Data", "social", seed, "twt")


# --------------------------------------------------------------------------- GDELT
# GDELT 2.0 (15-minute files, 61 columns) differs from 1.0 only by three extra ADM2 columns, which shifts these fields
GDELT2_COLS = {**{k: v for k, v in GDELT_COLS.items() if k < 35}, 52: "geo_name", 53: "country", 59: "date_added", 60: "url"}


def _prepare_gdelt(g: pd.DataFrame, min_articles: int, keep: int) -> pd.DataFrame:
    """Shared filtering for both GDELT generations: CAMEO roots, minimum coverage, headline from URL slug, de-dup."""
    cfg = engine_config()["ingestion"]
    for c in ("goldstein", "num_mentions", "num_sources", "num_articles", "avg_tone"):
        g[c] = pd.to_numeric(g[c], errors="coerce")
    g = g[g["cameo_root"].isin(cfg["gdelt_root_codes"]) & (g["num_articles"] >= min_articles)]
    g = g.dropna(subset=["url", "date_added"])
    g["title"] = g["url"].map(slug_to_headline)
    g = g[g["title"].str.len() > 0]
    g = g.sort_values("num_articles", ascending=False).drop_duplicates("url")
    return g.head(keep)


def _read_gdelt(path: str, cols: dict) -> pd.DataFrame:
    g = pd.read_csv(path, sep="	", header=None, usecols=list(cols), dtype=str, on_bad_lines="skip", encoding="utf-8", encoding_errors="replace")
    return g.rename(columns=cols)


def _load_gkg() -> pd.DataFrame:
    """Compact GKG enrichment (url, themes, orgs) - daily files first so they win over transient live ones."""
    files = sorted(GKG_DIR.glob("*.csv")) + sorted(GKG_LIVE.glob("*.csv"))
    if not files:
        return pd.DataFrame(columns=["url", "themes", "orgs"])
    return pd.concat([pd.read_csv(f, dtype=str) for f in files], ignore_index=True).drop_duplicates("url", keep="first")


def load_gdelt(seed: int) -> pd.DataFrame:
    """Daily GDELT 1.0 exports (authoritative) + GDELT 2.0 15-minute files for days not yet covered by a daily file."""
    cfg = engine_config()["ingestion"]
    frames = [_prepare_gdelt(_read_gdelt(p, GDELT_COLS), cfg["gdelt_min_articles"], cfg["gdelt_keep_per_day"])
              for p in sorted(glob.glob(str(RAW_GDELT / "*.export.CSV")))]
    if not frames:                        # no GDELT files yet (the refresh job downloads them): nothing to return
        return pd.DataFrame(columns=COLUMNS)
    daily = pd.concat(frames, ignore_index=True).sort_values("num_articles", ascending=False).drop_duplicates("url")
    daily["replay_ts"] = pd.to_datetime(daily["date_added"], format="%Y%m%d", errors="coerce") + pd.to_timedelta(
        np.random.default_rng(seed).integers(0, 86400, len(daily)), unit="s")

    live_paths = sorted(glob.glob(str(LIVE_GDELT / "*.export.CSV")))
    parts = [daily]
    if live_paths:
        live = pd.concat([_read_gdelt(p, GDELT2_COLS) for p in live_paths], ignore_index=True)
        live["day"] = live["date_added"].str[:8]
        live = pd.concat([_prepare_gdelt(g, cfg["gdelt_live_min_articles"], cfg["gdelt_live_keep_per_day"]) for _, g in live.groupby("day")], ignore_index=True)
        live["replay_ts"] = pd.to_datetime(live["date_added"], format="%Y%m%d%H%M%S", errors="coerce")   # true event time (UTC)
        parts.append(live.drop(columns="day"))
    df = pd.concat(parts, ignore_index=True).drop_duplicates("url", keep="first")   # daily file wins over live duplicates
    df = df.dropna(subset=["replay_ts"])
    df["text"] = df["title"]
    df["source_name"] = df["url"].map(domain_of)
    df["original_date"] = df["sql_date"]
    df["actor1"] = df["actor1"].fillna("")
    df["actor2"] = df["actor2"].fillna("")
    df["country"] = df["country"].fillna("")
    df = df.merge(_load_gkg(), on="url", how="left")      # GKG themes / organisations by article URL (optional layer)
    df["themes"] = df["themes"].fillna("")
    df["orgs"] = df["orgs"].fillna("")
    df["ticker_hint"] = ""
    df["dataset"] = "GDELT-events"
    df["source_type"] = "gdelt"
    df["label"] = np.nan
    df = df.sort_values("num_articles", ascending=False).reset_index(drop=True)
    df["doc_id"] = [f"gdl-{i:06d}" for i in range(len(df))]
    return df[COLUMNS]


def load_all(seed: int | None = None) -> pd.DataFrame:
    """Load + clean + de-duplicate every source into the common document schema."""
    cfg = engine_config()["ingestion"]
    seed = cfg["random_seed"] if seed is None else seed
    sources = [
        ("GDELT events", lambda: load_gdelt(seed)),
        ("Reuters", lambda: load_reuters(cfg["news_sample"]["reuters"], seed)),
        ("Indian finance news", lambda: load_indian_news(cfg["news_sample"]["indian"], seed)),
        ("FinancialPhraseBank", lambda: load_financial_phrasebank(cfg["news_sample"]["financial_phrasebank"], seed)),
        ("Yahoo Finance headlines", load_yfinance_news),
        ("Financial tweets", lambda: load_financial_tweets(cfg["social_sample"]["financial_tweets"], seed)),
        ("Twitter general", lambda: load_twitter_general(cfg["social_sample"]["twitter_modi"], seed)),
    ]
    parts = []
    for name, fn in sources:              # a missing optional source is skipped with a warning, never fatal
        try:
            part = fn()
            if len(part):
                parts.append(part)
        except (FileNotFoundError, ValueError) as exc:
            print(f"WARNING: skipping source '{name}': {type(exc).__name__}: {exc}")
    if not parts:
        raise RuntimeError("No source data found under /data - nothing to process")
    df = pd.concat(parts, ignore_index=True)
    df = df[df["text"].str.len() >= 15]
    df = deduplicate(df, "text")
    return df.reset_index(drop=True)
