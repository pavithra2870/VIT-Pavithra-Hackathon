"""Market context features (volatility, momentum, beta).

Two layers, same interface:
  * snapshot  data/market/sp500-daily-tickers-2025.csv + %5ESPX.csv   (committed; always available)
  * live      data/market/yfinance/prices_daily.csv                    (optional; ~1 year of daily closes up to today, written by
                                                                        src/data_prep/fetch_market.py). For tickers it covers, live data REPLACES the
                                                                        snapshot (adjusted vs raw closes must not be spliced); others keep the snapshot.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

from src.config import MARKET

TRADING_DAYS = 252
LIVE_PRICES = MARKET / "yfinance" / "prices_daily.csv"


def _live_wide() -> pd.DataFrame | None:
    if not LIVE_PRICES.exists():
        return None
    try:
        d = pd.read_csv(LIVE_PRICES, parse_dates=["date"])
        w = d.pivot(index="date", columns="ticker", values="close").sort_index()
        return w if len(w) > 30 else None
    except Exception:
        return None


def load_prices(live: bool = True) -> pd.DataFrame:
    """Wide close-price matrix (date x ticker). Live columns (incl. ^GSPC, ^VIX, ^TNX) replace snapshot columns when available."""
    df = pd.read_csv(MARKET / "sp500-daily-tickers-2025.csv", usecols=["ticker", "date", "close"])
    df["date"] = pd.to_datetime(df["date"], utc=True).dt.tz_localize(None).dt.normalize()
    base = df.pivot_table(index="date", columns="ticker", values="close").sort_index()
    lw = _live_wide() if live else None
    if lw is None:
        return base
    idx = base.index.union(lw.index)
    out = pd.concat([base.drop(columns=[c for c in lw.columns if c in base.columns]).reindex(idx), lw.reindex(idx)], axis=1)
    return out.sort_index()


def load_index(since: str = "2018-01-01", live: bool = True) -> pd.DataFrame:
    """S&P 500 close series: committed history, extended with live ^GSPC. A 'seg' column marks the history/live gap so
    returns are never computed across it."""
    spx = pd.read_csv(MARKET / "%5ESPX.csv", parse_dates=["Date"])
    spx = spx[spx["Date"] >= since].set_index("Date")[["Close"]]
    spx["seg"] = 0
    lw = _live_wide() if live else None
    if lw is not None and "^GSPC" in lw.columns:
        g = lw[["^GSPC"]].dropna().rename(columns={"^GSPC": "Close"})
        g = g[g.index > spx.index.max()]
        g["seg"] = 1
        spx = pd.concat([spx, g])
    return spx


def _tail_stat(s: pd.Series, n: int, fn) -> float:
    s = s.dropna()
    return float(fn(s.tail(n))) if len(s) >= max(5, n // 3) else np.nan


def build_market_context() -> tuple[pd.DataFrame, pd.DataFrame]:
    """Returns (ticker-level context, index-level daily context)."""
    px_all = load_prices()
    macro = [c for c in px_all.columns if str(c).startswith("^")]
    px = px_all.drop(columns=macro)
    rets = np.log(px).diff().dropna(how="all")
    mkt_ew = rets.mean(axis=1)                              # equal-weight proxy for tickers without live data
    mkt_idx = np.log(px_all["^GSPC"]).diff().reindex(rets.index) if "^GSPC" in px_all.columns else None
    last_live = px_all["^GSPC"].dropna().index.max() if "^GSPC" in px_all.columns else None

    rows = []
    for t in px.columns:
        s = px[t].dropna()
        r = rets[t].dropna()
        if len(r) < 30:
            continue
        end = s.index[-1]
        is_live = last_live is not None and mkt_idx is not None and end >= last_live - pd.Timedelta(days=4)
        mk = (mkt_idx if is_live else mkt_ew).reindex(r.index)
        rr = r.tail(250)
        ok = rr.notna() & mk.reindex(rr.index).notna()
        mm = mk.reindex(rr.index)[ok]
        beta = float(rr[ok].cov(mm) / mm.var()) if ok.sum() > 60 and mm.var() > 0 else np.nan
        rows.append({
            "ticker": t, "as_of": end.strftime("%Y-%m-%d"), "last_close": float(s.iloc[-1]),
            "ret_5d": float(s.iloc[-1] / s.iloc[-6] - 1) if len(s) > 6 else np.nan,
            "ret_20d": float(s.iloc[-1] / s.iloc[-21] - 1) if len(s) > 21 else np.nan,
            "vol_20d": _tail_stat(r, 20, np.std) * np.sqrt(TRADING_DAYS),
            "vol_60d": _tail_stat(r, 60, np.std) * np.sqrt(TRADING_DAYS),
            "beta": beta, "drawdown_60d": float(s.iloc[-1] / s.tail(60).max() - 1), "source": "live" if is_live else "snapshot",
        })
    ctx = pd.DataFrame(rows).dropna(subset=["vol_60d"]).reset_index(drop=True)
    ctx["vol_pctile"] = ctx["vol_60d"].rank(pct=True)

    spx = load_index()
    r = spx.groupby("seg")["Close"].transform(lambda x: np.log(x).diff())
    idx = pd.DataFrame({
        "date": spx.index.strftime("%Y-%m-%d"), "close": spx["Close"].values, "ret_1d": r.values,
        "vol_20d": (r.rolling(20).std() * np.sqrt(TRADING_DAYS)).values,
    }).dropna()
    hist = idx["vol_20d"].tail(1500)
    idx["vol_pctile"] = idx["vol_20d"].map(lambda v: float((hist <= v).mean()))
    return ctx, idx


def market_vol_percentile(ctx: pd.DataFrame, idx: pd.DataFrame, ticker: str | None) -> float:
    """Volatility context factor (0-1) for a signal: ticker vol percentile, else index vol percentile."""
    if ticker:
        row = ctx.loc[ctx["ticker"] == ticker]
        if len(row):
            return float(row["vol_pctile"].iloc[0])
    return float(idx["vol_pctile"].iloc[-1])
