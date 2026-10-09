"""Fetch LIVE market data with yfinance into data/market/yfinance (optional layer; the app falls back to the committed snapshot).

    python -m src.data_prep.fetch_market [--force]

Outputs (all small CSVs, written atomically):
  prices_daily.csv   date,ticker,close      ~1 year of daily adjusted closes: index + portfolio + universe tickers + ^GSPC ^VIX ^TNX
  quotes.csv         ticker,price,quote_time_utc,prev_close,change_pct   latest (intraday if market open, else last close)
  market_caps.csv    ticker,market_cap,shares,price,as_of   for the 15 mock-index stocks (refreshed ~daily)
  news.csv           uuid,ticker,title,publisher,published_utc,link   Yahoo Finance headlines for the index stocks (accumulated)

yfinance reads Yahoo Finance public endpoints (unofficial, no key, quotes can be ~15 min delayed, personal-use terms).
Every function degrades gracefully: on any failure the previous files are left untouched.
"""
from __future__ import annotations

import datetime as dt
import time
from pathlib import Path

import pandas as pd

from src.config import PORTFOLIOS, REFERENCE, MARKET

YF_DIR = MARKET / "yfinance"
MACRO = ["^GSPC", "^VIX", "^TNX"]
DAILY_MIN_AGE_S = 600          # re-download daily history at most every 10 minutes
CAPS_MIN_AGE_S = 20 * 3600     # market caps move slowly: ~daily
NEWS_MIN_AGE_S = 55 * 60       # news at most hourly (15 requests)
NEWS_KEEP_DAYS = 21


def _age(path: Path) -> float:
    return time.time() - path.stat().st_mtime if path.exists() else 1e12


def _write(df: pd.DataFrame, name: str) -> None:
    YF_DIR.mkdir(parents=True, exist_ok=True)
    tmp = YF_DIR / (name + ".tmp")
    df.to_csv(tmp, index=False)
    tmp.replace(YF_DIR / name)


def index_tickers() -> list[str]:
    return list(pd.read_csv(PORTFOLIOS / "mock_index.csv")["ticker"])


def all_tickers() -> list[str]:
    """Mock index + every equity / option underlying in the portfolios + US universe names (for volatility ranking)."""
    h = pd.read_csv(PORTFOLIOS / "holdings.csv")
    held = set(h.loc[h["asset_class"] == "equity", "ticker"].dropna()) | set(h["underlying_ticker"].dropna())
    uni = set()
    for line in (REFERENCE / "company_universe.csv").read_text(encoding="utf-8").splitlines()[1:]:
        p = line.split("|")
        if len(p) >= 4 and p[3] == "US":
            uni.add(p[0])
    out = (set(index_tickers()) | held | uni) - {"SPX", ""}
    return sorted(out)


def fetch_daily(tickers: list[str]) -> bool:
    import yfinance as yf

    d = yf.download(tickers + MACRO, period="1y", interval="1d", auto_adjust=True, progress=False, threads=True)["Close"]
    d = d.dropna(how="all")
    if d.empty:
        return False
    d.index = pd.to_datetime(d.index).tz_localize(None).normalize()
    long = d.stack().rename("close").reset_index()
    long.columns = ["date", "ticker", "close"]
    long["date"] = long["date"].dt.strftime("%Y-%m-%d")
    _write(long.round(4), "prices_daily.csv")
    return True


def fetch_quotes(tickers: list[str]) -> int:
    """Latest price per ticker: last intraday 5-minute bar when available, else the last daily close."""
    import yfinance as yf

    daily = pd.read_csv(YF_DIR / "prices_daily.csv", parse_dates=["date"])
    wide = daily.pivot(index="date", columns="ticker", values="close").sort_index()
    syms = tickers + MACRO
    intr = yf.download(syms, period="1d", interval="5m", auto_adjust=False, progress=False, threads=True, prepost=False)
    close = intr["Close"] if len(intr) else pd.DataFrame()
    rows = []
    for t in syms:
        price, qt = None, None
        if t in close.columns:
            s = close[t].dropna()
            if len(s):
                price, qt = float(s.iloc[-1]), pd.Timestamp(s.index[-1])
        if price is None and t in wide.columns and wide[t].dropna().size:
            s = wide[t].dropna()
            price, qt = float(s.iloc[-1]), pd.Timestamp(s.index[-1]).tz_localize("America/New_York").tz_convert("UTC")
        if price is None:
            continue
        qt = qt.tz_convert("UTC") if qt.tzinfo else qt.tz_localize("UTC")
        qdate = qt.tz_convert("America/New_York").tz_localize(None).normalize()
        prev = wide[t].dropna() if t in wide.columns else pd.Series(dtype=float)
        prev = prev[prev.index < qdate]
        pc = float(prev.iloc[-1]) if len(prev) else None
        rows.append({"ticker": t, "price": round(price, 4), "quote_time_utc": qt.strftime("%Y-%m-%d %H:%M:%S"), "prev_close": pc,
                     "change_pct": round(price / pc - 1, 6) if pc else None})
    if rows:
        _write(pd.DataFrame(rows), "quotes.csv")
    return len(rows)


def fetch_caps() -> bool:
    import yfinance as yf

    rows = []
    for t in index_tickers():
        try:
            fi = yf.Ticker(t).fast_info
            cap, sh, px = fi["marketCap"], fi["shares"], fi["lastPrice"]
            if cap and cap > 0:
                rows.append({"ticker": t, "market_cap": float(cap), "shares": float(sh) if sh else None, "price": float(px), "as_of": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M")})
        except Exception:
            continue
    if len(rows) >= max(3, len(index_tickers()) // 2):
        _write(pd.DataFrame(rows), "market_caps.csv")
        return True
    return False


def fetch_news() -> int:
    """Accumulate Yahoo Finance headlines for the index stocks; returns the number of NEW items."""
    import yfinance as yf

    path = YF_DIR / "news.csv"
    old = pd.read_csv(path, dtype=str) if path.exists() else pd.DataFrame(columns=["uuid", "ticker", "title", "publisher", "published_utc", "link"])
    seen = set(old["uuid"])
    rows = []
    for t in index_tickers():
        try:
            items = yf.Search(t, news_count=10, max_results=1).news
        except Exception:
            continue
        for x in items:
            u = x.get("uuid")
            if not u or u in seen or not x.get("title"):
                continue
            seen.add(u)
            ts = dt.datetime.fromtimestamp(x["providerPublishTime"], dt.timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            rel = x.get("relatedTickers") or []
            if rel and (t not in rel or (rel[0] != t and len(rel) > 2)):   # keep stories where the ticker is the main subject, not a passing mention
                continue
            rows.append({"uuid": u, "ticker": t, "title": x["title"], "publisher": x.get("publisher", ""), "published_utc": ts, "link": x.get("link", "")})
    new = pd.DataFrame(rows, columns=old.columns)
    if len(new) or not path.exists():
        allr = pd.concat([old, new], ignore_index=True)
        cutoff = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=NEWS_KEEP_DAYS)).strftime("%Y-%m-%d")
        allr = allr[allr["published_utc"] >= cutoff]
        _write(allr, "news.csv")
    return len(new)


def fetch_market(force: bool = False) -> dict:
    """Refresh whatever is stale. Never raises. Returns what changed so the caller can decide how much to recompute."""
    res = {"daily": False, "quotes": 0, "caps": False, "news_new": 0, "error": None, "changed": False}
    try:
        import yfinance  # noqa: F401
    except Exception as exc:
        res["error"] = f"yfinance not installed ({type(exc).__name__})"
        return res
    tickers = all_tickers()
    for name, step in (
        ("daily", lambda: fetch_daily(tickers) if force or _age(YF_DIR / "prices_daily.csv") > DAILY_MIN_AGE_S else False),
        ("quotes", lambda: fetch_quotes(tickers) if (YF_DIR / "prices_daily.csv").exists() else 0),
        ("caps", lambda: fetch_caps() if force or _age(YF_DIR / "market_caps.csv") > CAPS_MIN_AGE_S else False),
        ("news_new", lambda: fetch_news() if force or _age(YF_DIR / "news.csv") > NEWS_MIN_AGE_S else 0),
    ):
        try:
            res[name] = step()
        except Exception as exc:        # one failing source must not block the others
            res["error"] = f"{name}: {type(exc).__name__}: {str(exc)[:120]}"
    res["changed"] = bool(res["daily"] or res["quotes"] or res["caps"] or res["news_new"])
    return res


if __name__ == "__main__":
    import sys

    print(fetch_market(force="--force" in sys.argv))
