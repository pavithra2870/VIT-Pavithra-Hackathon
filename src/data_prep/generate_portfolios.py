"""Generate the SYNTHETIC portfolios and the mock index used by the prototype (seeded -> reproducible).

    python -m src.data_prep.generate_portfolios

Outputs (all under /data/portfolios):
  portfolios.csv   portfolio metadata and mandate
  holdings.csv     one row per position across equities, bonds, loans and derivatives
  mock_index.csv   15-stock mock index for the Tactical Rebalancer

Real inputs: last closing prices and traded dollar volume come from data/market/sp500-daily-tickers-2025.csv.
Everything else (position sizes, ratings, PDs, coupons, option strikes, swap notionals, borrower names) is synthetic.
No proprietary data is used.
"""
from __future__ import annotations

import numpy as np
import pandas as pd
from scipy.stats import norm

from src.config import MARKET, PORTFOLIOS, PROCESSED
from src.risk_engine.entity.extractor import load_universe

SEED = 20260927
RNG = np.random.default_rng(SEED)

RATING_PD = {"AAA": 0.0002, "AA": 0.0005, "A": 0.0012, "BBB": 0.0045, "BB": 0.015, "B": 0.04, "CCC": 0.12}
RATING_SPREAD = {"AAA": 25, "AA": 45, "A": 80, "BBB": 140, "BB": 280, "B": 450, "CCC": 900}
HY = {"BB", "B", "CCC"}

MOCK_INDEX = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "TSLA", "JPM", "BAC", "XOM", "CVX", "JNJ", "PFE", "PG", "BA", "CAT"]

PORTFOLIO_SPECS = [
    dict(portfolio_id="GGF", name="Global Growth Fund", strategy="Long-only growth equity with option overlay", total_mv=2_400_000_000,
         description="Equity-heavy growth mandate: technology, communication and consumer-discretionary leaders, a small short-duration bond sleeve and a call/put option overlay. Highly sensitive to equity and rate-duration shocks."),
    dict(portfolio_id="WBP", name="Wholesale Bank Portfolio", strategy="Wholesale banking book: corporate loans, liquidity bonds, hedging derivatives", total_mv=6_000_000_000,
         description="Loan-dominated banking book with a liquid bond buffer, bank-equity stakes and an interest-rate / credit derivatives hedge book. Sensitive to credit events and default stress."),
    dict(portfolio_id="BIF", name="Balanced Institutional Fund", strategy="60/40-style multi-asset with hedges", total_mv=3_200_000_000,
         description="Diversified pension-style fund: broad equities, government and corporate bonds, a private-credit loan sleeve and index put / swap hedges."),
    dict(portfolio_id="CAF", name="Conservative Allocation", strategy="Capital preservation: long-duration IG bonds, defensive equities, senior secured loans", total_mv=1_200_000_000,
         description="Low-risk mandate dominated by investment-grade bonds and defensive equities. Insulated from equity shocks but exposed to duration and spread widening."),
]

_ADJ = ["Meridian", "Northgate", "Halcyon", "Brightwater", "Ironbridge", "Cobalt", "Summit", "Larkspur", "Harbor", "Westfield", "Granite", "Evergreen", "Pinnacle", "Redwood", "Sterling", "Atlas",
        "Beacon", "Crescent", "Falcon", "Orchard", "Sable", "Tidewater", "Vanguard", "Wexford", "Zenith", "Alder", "Kestrel", "Marlow"]
_NOUN = {"Industrials": ["Logistics", "Aerospace Components", "Industrial Holdings", "Rail Services", "Machinery"], "Energy": ["Midstream", "Petroleum", "Energy Partners", "Offshore Drilling"],
         "Real Estate": ["Properties", "Realty Trust", "Logistics Parks", "Commercial REIT"], "Consumer Discretionary": ["Retail Group", "Hospitality", "Motors", "Leisure"],
         "Information Technology": ["Software", "Semiconductors", "Data Systems"], "Financials": ["Capital", "Specialty Finance", "Insurance Holdings"], "Health Care": ["Therapeutics", "Hospital Group", "MedTech"],
         "Utilities": ["Power & Light", "Water Utilities", "Renewables"], "Materials": ["Chemicals", "Metals & Mining", "Packaging"], "Consumer Staples": ["Foods", "Beverages", "Household Brands"],
         "Communication Services": ["Telecom", "Media Group"]}


def _prices() -> pd.DataFrame:
    ctx = pd.read_csv(PROCESSED / "market_context.csv").set_index("ticker")
    raw = pd.read_csv(MARKET / "sp500-daily-tickers-2025.csv", usecols=["ticker", "close", "volume"])
    dv = (raw["close"] * raw["volume"]).groupby(raw["ticker"]).mean().rename("avg_dollar_volume")
    return ctx.join(dv)


def bs(S, K, T, r, sig, kind):
    d1 = (np.log(S / K) + (r + 0.5 * sig ** 2) * T) / (sig * np.sqrt(T))
    d2 = d1 - sig * np.sqrt(T)
    if kind == "call":
        price, delta = S * norm.cdf(d1) - K * np.exp(-r * T) * norm.cdf(d2), norm.cdf(d1)
    else:
        price, delta = K * np.exp(-r * T) * norm.cdf(-d2) - S * norm.cdf(-d1), norm.cdf(d1) - 1
    gamma = norm.pdf(d1) / (S * sig * np.sqrt(T))
    vega = S * norm.pdf(d1) * np.sqrt(T)  # per 1.00 (=100 vol points) change in sigma
    return price, delta, gamma, vega


def _equity(pid, tickers, weights, mv_total, px, uni):
    rows = []
    w = np.asarray(weights, float)
    w = w / w.sum()
    for t, wi in zip(tickers, w):
        mv = mv_total * wi
        price = float(px.loc[t, "last_close"])
        u = uni.loc[t]
        rows.append(dict(portfolio_id=pid, asset_class="equity", instrument_type="common_stock", name=u["name"], ticker=t, issuer=u["name"], sector=u["sector"], country=u["country"],
                         quantity=round(mv / price), price=round(price, 2), market_value=round(round(mv / price) * price, 2), notional=round(round(mv / price) * price, 2)))
    return rows


def _pick(uni, sectors=None, n=10, exclude=()):
    u = uni[(uni["country"] == "US") & (~uni.index.isin(exclude))]
    if sectors:
        u = u[u["sector"].isin(sectors)]
    return list(RNG.choice(u.index.to_numpy(), size=min(n, len(u)), replace=False))


def _bond(pid, name, issuer, ticker, sector, rating, mv, dur, coupon, kind="corp_bond", country="US"):
    spread = 0 if kind == "govt_bond" else RATING_SPREAD[rating] * float(RNG.uniform(0.85, 1.15))
    ytm = 0.0415 + spread / 1e4 + (dur - 5) * 0.0004
    return dict(portfolio_id=pid, asset_class="bond", instrument_type=kind, name=name, ticker=ticker, issuer=issuer, sector=sector, country=country, rating=rating,
                quantity=round(mv / 1000), price=100.0, market_value=round(mv, 2), notional=round(mv, 2), coupon=round(coupon, 4), maturity_years=round(dur * 1.18, 1),
                modified_duration=round(dur, 2), convexity=round(dur ** 2 * 1.05 + dur * 2, 1), ytm=round(ytm, 4), spread_bp=round(spread, 0), rate_type="fixed")


def _loan(pid, borrower, ticker, sector, rating, notional, floating, secured, country="US"):
    pd_ = RATING_PD[rating]
    lgd = 0.30 if secured else 0.45
    dur = float(RNG.uniform(0.25, 0.6)) if floating else float(RNG.uniform(2.0, 4.5))
    return dict(portfolio_id=pid, asset_class="loan", instrument_type="term_loan" if secured else "revolver", name=f"{borrower} {'Secured Term Loan' if secured else 'Revolving Facility'}",
                ticker=ticker, issuer=borrower, sector=sector, country=country, rating=rating, quantity=1, price=round(float(RNG.uniform(98.2, 100.0)), 2),
                market_value=round(notional * 0.99, 2), notional=round(notional, 2), coupon=round(0.0415 + RATING_SPREAD[rating] / 1e4 * 1.3, 4),
                maturity_years=round(float(RNG.uniform(2, 7)), 1), modified_duration=round(dur, 2), spread_bp=round(RATING_SPREAD[rating] * 1.3, 0),
                rate_type="floating" if floating else "fixed", pd=pd_, lgd=lgd)


def _option(pid, ticker, S, qty_notional, kind, moneyness, T, vol, name_prefix, sector="Index", country="US", underlying_name=None):
    K = S * moneyness
    price, delta, gamma, vega = bs(S, K, T, 0.04, vol, kind)
    qty = qty_notional / S  # underlying-equivalent units
    return dict(portfolio_id=pid, asset_class="derivative", instrument_type=f"{kind}_option", name=f"{name_prefix} {kind.upper()} {K:,.0f} {T * 12:.0f}M", ticker=ticker, issuer="Exchange-listed", sector=sector,
                country=country, quantity=round(qty, 0), price=round(price, 2), market_value=round(price * qty, 2), notional=round(qty_notional, 2), maturity_years=round(T, 2),
                underlying_ticker=ticker, strike=round(K, 2), delta_usd=round(delta * qty * S, 2), gamma_usd=round(gamma * qty * S * S, 2), vega_usd=round(vega * qty, 2), implied_vol=vol)


def _swap(pid, notional, payer, tenor):
    dv01 = notional * tenor * 0.85 * 1e-4 * (1 if payer else -1)  # approx. fixed-leg annuity DV01, + for payer (gains when rates rise)
    mv = float(RNG.normal(0, 0.002)) * notional
    return dict(portfolio_id=pid, asset_class="derivative", instrument_type="irs_payer" if payer else "irs_receiver", name=f"{tenor}Y USD IRS {'Pay' if payer else 'Receive'} Fixed",
                ticker="", issuer="OTC CCP", sector="Rates", country="US", quantity=1, price=0.0, market_value=round(mv, 2), notional=round(notional, 2), maturity_years=tenor, dv01_usd=round(dv01, 2))


def _cds(pid, ref_name, ref_ticker, sector, rating, notional, tenor=5):
    cs01 = notional * tenor * 0.9 * 1e-4
    return dict(portfolio_id=pid, asset_class="derivative", instrument_type="cds_protection", name=f"{ref_name} {tenor}Y CDS (protection bought)", ticker=ref_ticker, issuer="OTC CCP", sector=sector,
                country="US", rating=rating, quantity=1, price=0.0, market_value=round(float(RNG.normal(0.0004, 0.0003)) * notional, 2), notional=round(notional, 2), maturity_years=tenor, cs01_usd=round(cs01, 2))


def build() -> None:
    PORTFOLIOS_DIR = PORTFOLIOS
    PORTFOLIOS_DIR.mkdir(parents=True, exist_ok=True)
    uni = load_universe().set_index("ticker")
    px = _prices()
    avail = set(px.index)
    uni = uni[uni.index.isin(avail)]
    rows: list[dict] = []

    # ---------------- GGF: Global Growth Fund
    mv = 2_400_000_000
    ggf_tickers = ["NVDA", "MSFT", "AAPL", "AMZN", "GOOGL", "META", "TSLA", "AVGO", "NFLX", "CRM", "ADBE", "AMD", "ORCL", "V", "MA", "LLY", "UNH", "COST", "NKE", "BKNG", "QCOM", "INTC", "DIS", "JPM", "HD", "TXN", "CAT", "GE"]
    ggf_w = np.array([9, 8, 7.5, 6.5, 6.5, 5.5, 5, 4.2, 3.2, 3.0, 2.8, 2.8, 2.5, 2.5, 2.4, 2.2, 2.0, 1.9, 1.8, 1.6, 1.5, 1.3, 1.3, 1.2, 1.2, 1.0, 1.0, 1.0])
    rows += _equity("GGF", ggf_tickers, ggf_w, mv * 0.87, px, uni)
    rows.append(_bond("GGF", "US Treasury 2Y Note", "US Treasury", "", "Government", "AAA", mv * 0.05, 1.9, 0.0405, "govt_bond"))
    rows.append(_bond("GGF", "US Treasury 5Y Note", "US Treasury", "", "Government", "AAA", mv * 0.03, 4.5, 0.0395, "govt_bond"))
    for t in ("NVDA", "AAPL", "MSFT"):
        rows.append(_option("GGF", t, float(px.loc[t, "last_close"]), mv * 0.012, "call", 1.05, 0.5, float(px.loc[t, "vol_60d"]) + 0.05, f"{t}", uni.loc[t, "sector"], underlying_name=uni.loc[t, "name"]))
    for t in ("TSLA", "AMZN"):
        rows.append(_option("GGF", t, float(px.loc[t, "last_close"]), mv * 0.008, "put", 0.92, 0.5, float(px.loc[t, "vol_60d"]) + 0.05, f"{t}", uni.loc[t, "sector"]))

    # ---------------- WBP: Wholesale Bank Portfolio (exposure in notional; loans dominate)
    mv = 6_000_000_000
    sector_mix = {"Industrials": 0.17, "Real Estate": 0.17, "Energy": 0.13, "Consumer Discretionary": 0.13, "Financials": 0.10, "Information Technology": 0.08, "Health Care": 0.07, "Utilities": 0.06, "Materials": 0.05, "Consumer Staples": 0.04}
    loan_total = mv * 0.55
    used = set()
    for sec, share in sector_mix.items():
        n = 2 if share >= 0.1 else 1
        for i in range(n):
            while True:
                nm = f"{RNG.choice(_ADJ)} {RNG.choice(_NOUN[sec])}"
                if nm not in used:
                    used.add(nm)
                    break
            rating = RNG.choice(["BBB", "BBB", "BB", "BB", "B", "A"], p=[0.25, 0.2, 0.2, 0.15, 0.1, 0.1])
            rows.append(_loan("WBP", nm, "", sec, str(rating), loan_total * share / n * float(RNG.uniform(0.8, 1.2)), bool(RNG.random() < 0.65), bool(RNG.random() < 0.55)))
    for t, sec, rating in [("F", "Consumer Discretionary", "BB"), ("BA", "Industrials", "BBB"), ("OXY", "Energy", "BBB"), ("AMT", "Real Estate", "BBB")]:
        rows.append(_loan("WBP", uni.loc[t, "name"], t, sec, rating, loan_total * 0.035, True, False))
    bonds = [("US Treasury 10Y Note", "US Treasury", "", "Government", "AAA", 0.09, 8.2, "govt_bond"), ("US Treasury 3Y Note", "US Treasury", "", "Government", "AAA", 0.06, 2.8, "govt_bond"),
             ("JPMorgan 4.6% 2030", "JPMorgan Chase", "JPM", "Financials", "A", 0.025, 4.3, "corp_bond"), ("Bank of America 4.9% 2031", "Bank of America", "BAC", "Financials", "A", 0.02, 5.0, "corp_bond"),
             ("Exxon Mobil 4.2% 2032", "Exxon Mobil", "XOM", "Energy", "AA", 0.02, 5.8, "corp_bond"), ("Verizon 4.8% 2033", "Verizon Communications", "VZ", "Communication Services", "BBB", 0.015, 6.2, "corp_bond"),
             ("Ford Motor 6.1% 2029", "Ford Motor Co.", "F", "Consumer Discretionary", "BB", 0.012, 3.6, "corp_bond"), ("Boeing 5.7% 2030", "Boeing Co.", "BA", "Industrials", "BBB", 0.014, 4.1, "corp_bond"),
             ("Prologis 4.5% 2032", "Prologis Inc.", "PLD", "Real Estate", "A", 0.012, 5.7, "corp_bond"), ("Citigroup 5.0% 2031", "Citigroup Inc.", "C", "Financials", "BBB", 0.01, 4.9, "corp_bond")]
    for nm, iss, t, sec, rt, share, dur, kind in bonds:
        rows.append(_bond("WBP", nm, iss, t, sec, rt, mv * share, dur, 0.04 + RATING_SPREAD[rt] / 1e4, kind))
    rows += _equity("WBP", ["JPM", "BAC", "C", "GS", "WFC", "MS"], [3, 2.5, 2, 1.5, 1.5, 1], mv * 0.05, px, uni)
    rows.append(_swap("WBP", mv * 0.06, True, 5))
    rows.append(_swap("WBP", mv * 0.04, False, 10))
    rows.append(_swap("WBP", mv * 0.03, True, 3))
    rows.append(_cds("WBP", "CDX IG Index", "", "Financials", "BBB", mv * 0.03))
    rows.append(_cds("WBP", "Ford Motor Co.", "F", "Consumer Discretionary", "BB", mv * 0.01))
    rows.append(_cds("WBP", "Boeing Co.", "BA", "Industrials", "BBB", mv * 0.01))
    rows.append(_option("WBP", "JPM", float(px.loc["JPM", "last_close"]), mv * 0.006, "put", 0.9, 0.5, float(px.loc["JPM", "vol_60d"]) + 0.04, "JPM", "Financials"))
    rows.append(_option("WBP", "XOM", float(px.loc["XOM", "last_close"]), mv * 0.004, "put", 0.92, 0.5, float(px.loc["XOM", "vol_60d"]) + 0.04, "XOM", "Energy"))

    # ---------------- BIF: Balanced Institutional Fund
    mv = 3_200_000_000
    bif_t = ["AAPL", "MSFT", "AMZN", "GOOGL", "JPM", "JNJ", "XOM", "PG", "UNH", "V", "HD", "CVX", "KO", "PEP", "WMT", "CSCO", "MRK", "ABBV", "BAC", "NEE", "CAT", "HON", "LIN", "VZ", "T", "PFE"]
    rows += _equity("BIF", bif_t, RNG.uniform(1.0, 4.0, len(bif_t)), mv * 0.52, px, uni)
    for nm, iss, t, sec, rt, share, dur, kind in [
        ("US Treasury 10Y Note", "US Treasury", "", "Government", "AAA", 0.10, 8.1, "govt_bond"), ("US Treasury 5Y Note", "US Treasury", "", "Government", "AAA", 0.08, 4.6, "govt_bond"),
        ("US Treasury 30Y Bond", "US Treasury", "", "Government", "AAA", 0.04, 16.5, "govt_bond"), ("Apple 3.9% 2032", "Apple Inc.", "AAPL", "Information Technology", "AA", 0.03, 6.3, "corp_bond"),
        ("Microsoft 3.7% 2033", "Microsoft Corp.", "MSFT", "Information Technology", "AAA", 0.025, 7.0, "corp_bond"), ("Walmart 4.1% 2031", "Walmart Inc.", "WMT", "Consumer Staples", "AA", 0.02, 5.2, "corp_bond"),
        ("Chevron 4.4% 2032", "Chevron Corp.", "CVX", "Energy", "AA", 0.02, 5.9, "corp_bond"), ("AT&T 5.2% 2032", "AT&T Inc.", "T", "Communication Services", "BBB", 0.02, 6.0, "corp_bond"),
        ("Pfizer 4.5% 2033", "Pfizer Inc.", "PFE", "Health Care", "A", 0.015, 6.8, "corp_bond"), ("GM 5.6% 2030", "General Motors", "GM", "Consumer Discretionary", "BBB", 0.015, 4.2, "corp_bond")]:
        rows.append(_bond("BIF", nm, iss, t, sec, rt, mv * share, dur, 0.04 + RATING_SPREAD[rt] / 1e4, kind))
    for sec, rating, share in [("Industrials", "BB", 0.012), ("Information Technology", "BB", 0.012), ("Health Care", "BBB", 0.012), ("Real Estate", "BB", 0.009)]:
        rows.append(_loan("BIF", f"{RNG.choice(_ADJ)} {RNG.choice(_NOUN[sec])}", "", sec, rating, mv * share, True, True))
    spx = 6800.0
    rows.append(_option("BIF", "SPX", spx, mv * 0.06, "put", 0.92, 0.5, 0.17, "S&P 500 Index", "Index"))
    rows.append(_option("BIF", "SPX", spx, mv * 0.02, "call", 1.04, 0.5, 0.15, "S&P 500 Index", "Index"))
    rows.append(_swap("BIF", mv * 0.07, False, 10))
    rows.append(_cds("BIF", "CDX HY Index", "", "Financials", "BB", mv * 0.015))

    # ---------------- CAF: Conservative Allocation
    mv = 1_200_000_000
    rows += _equity("CAF", ["PG", "KO", "JNJ", "PEP", "WMT", "DUK", "SO", "NEE", "MRK", "VZ", "CL", "MDLZ"], RNG.uniform(1.5, 3.5, 12), mv * 0.21, px, uni)
    for nm, iss, t, sec, rt, share, dur, kind in [
        ("US Treasury 10Y Note", "US Treasury", "", "Government", "AAA", 0.18, 8.0, "govt_bond"), ("US Treasury 30Y Bond", "US Treasury", "", "Government", "AAA", 0.08, 16.8, "govt_bond"),
        ("US Treasury 5Y Note", "US Treasury", "", "Government", "AAA", 0.07, 4.5, "govt_bond"), ("Johnson & Johnson 3.7% 2033", "Johnson & Johnson", "JNJ", "Health Care", "AAA", 0.05, 7.4, "corp_bond"),
        ("Procter & Gamble 3.6% 2032", "Procter & Gamble", "PG", "Consumer Staples", "AA", 0.045, 6.4, "corp_bond"), ("Coca-Cola 3.9% 2034", "Coca-Cola Co.", "KO", "Consumer Staples", "AA", 0.04, 7.9, "corp_bond"),
        ("Duke Energy 4.3% 2033", "Duke Energy", "DUK", "Utilities", "A", 0.04, 7.2, "corp_bond"), ("NextEra 4.5% 2034", "NextEra Energy", "NEE", "Utilities", "A", 0.035, 8.1, "corp_bond"),
        ("JPMorgan 4.4% 2032", "JPMorgan Chase", "JPM", "Financials", "A", 0.03, 5.9, "corp_bond"), ("Verizon 4.6% 2033", "Verizon Communications", "VZ", "Communication Services", "BBB", 0.025, 6.5, "corp_bond"),
        ("Walmart 4.0% 2033", "Walmart Inc.", "WMT", "Consumer Staples", "AA", 0.03, 7.0, "corp_bond"), ("Pfizer 4.4% 2034", "Pfizer Inc.", "PFE", "Health Care", "A", 0.02, 7.6, "corp_bond")]:
        rows.append(_bond("CAF", nm, iss, t, sec, rt, mv * share, dur, 0.04 + RATING_SPREAD[rt] / 1e4, kind))
    for sec, rating, share in [("Utilities", "BBB", 0.04), ("Health Care", "A", 0.03), ("Consumer Staples", "A", 0.03), ("Industrials", "BBB", 0.025), ("Information Technology", "A", 0.02), ("Real Estate", "BBB", 0.015)]:
        rows.append(_loan("CAF", f"{RNG.choice(_ADJ)} {RNG.choice(_NOUN[sec])}", "", sec, rating, mv * share, False, True))
    rows.append(_option("CAF", "SPX", spx, mv * 0.03, "put", 0.9, 0.75, 0.16, "S&P 500 Index", "Index"))
    rows.append(_swap("CAF", mv * 0.04, True, 5))
    rows.append(_cds("CAF", "CDX IG Index", "", "Financials", "BBB", mv * 0.015))

    h = pd.DataFrame(rows)
    h.insert(1, "holding_id", [f"{p}-{i:03d}" for p, i in zip(h["portfolio_id"], h.groupby("portfolio_id").cumcount() + 1)])
    for c in ("rating", "ticker", "rate_type", "underlying_ticker", "issuer"):
        h[c] = h[c].fillna("") if c in h else ""
    cols = ["portfolio_id", "holding_id", "asset_class", "instrument_type", "name", "ticker", "issuer", "sector", "country", "rating", "quantity", "price", "market_value", "notional", "coupon", "maturity_years",
            "modified_duration", "convexity", "ytm", "spread_bp", "rate_type", "pd", "lgd", "underlying_ticker", "strike", "implied_vol", "delta_usd", "gamma_usd", "vega_usd", "dv01_usd", "cs01_usd"]
    h = h.reindex(columns=cols)
    h.to_csv(PORTFOLIOS / "holdings.csv", index=False)

    meta = pd.DataFrame(PORTFOLIO_SPECS)
    meta["valuation_date"] = px["as_of"].iloc[0]
    meta["synthetic"] = True
    meta.to_csv(PORTFOLIOS / "portfolios.csv", index=False)

    # ---------------- mock index (weights from sqrt of 2025 average traded dollar volume, capped at 12%)
    dv = px.loc[MOCK_INDEX, "avg_dollar_volume"]
    w = np.sqrt(dv) / np.sqrt(dv).sum()            # square-root dampening of traded value: avoids a 2-stock index
    for _ in range(20):                           # iterative 12% cap
        w = (w.clip(upper=0.12)) / w.clip(upper=0.12).sum()
    idx = pd.DataFrame({"ticker": MOCK_INDEX, "name": [load_universe().set_index("ticker").loc[t, "name"] for t in MOCK_INDEX],
                        "sector": [load_universe().set_index("ticker").loc[t, "sector"] for t in MOCK_INDEX], "weight": w.round(6).values})
    idx["weight"] = idx["weight"] / idx["weight"].sum()
    idx.to_csv(PORTFOLIOS / "mock_index.csv", index=False)
    print(h.groupby(["portfolio_id", "asset_class"])["market_value"].sum().unstack().round(0))
    print(idx)


if __name__ == "__main__":
    build()
