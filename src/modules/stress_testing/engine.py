"""Module B - Strategic Portfolio Stress Lab: event-driven scenario engine.

SIMPLIFIED PROTOTYPE METHODOLOGY - not a production, regulatory (CCAR / EBA) or VaR model.

For a selected Risk Signal (event type + impact score + affected names) the engine applies the explicit shock
assumptions in data/config/stress_scenarios.json to every holding of a portfolio:

  severity s = (impact / 10) ** severity_exponent                   -> shocks are specified at impact 10 and scaled by s
  equity     dP/P  = sector_shock * s * (0.6 + 0.4 * beta)          (+ direct-exposure multiplier / named-entity shock)
  bond       dP/P  = -D*dy + 0.5*C*dy^2 - D*ds                      dy = parallel shift * s ; ds = rating-bucket spread widening * s
  loan       dV    = -(PD_stressed - PD) * LGD * notional  - D_fixed*dy - beta_spread * D * ds
             PD_stressed = PD * (1 + (sector_multiplier - 1) * s) [* floating-rate pass-through for rate rises]
  option     dV    = delta$ * r + 0.5 * gamma$ * r^2 + vega$ * dVol  r = underlying equity shock
  IRS        dV    = DV01$ * dy_bp                                  (payer positive, receiver negative)
  CDS (prot) dV    = CS01$ * ds_bp

Everything is deterministic and reproducible from the portfolio holdings, the event and the configuration.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from src.config import stress_config

DISCLAIMER = ("Simplified prototype methodology: single-factor shocks specified per event class, scaled linearly by impact score. "
              "It is illustrative decision support, not a production or regulatory risk model.")
HY = {"BB", "B", "CCC"}
DRIVER_LABELS = {"equity_price": "Equity price shock", "rates": "Interest-rate move", "credit_spread": "Credit-spread widening",
                 "default": "Default / credit-loss (PD x LGD)", "volatility": "Volatility (vega)", "convexity": "Convexity"}


@dataclass
class StressEvent:
    event_type: str
    impact_score: float
    sentiment: float = -0.5
    tickers: tuple = ()
    sectors: tuple = ()
    name: str = ""
    event_id: str = ""


def _usd(x: float) -> str:
    a = abs(x)
    body = f"{a / 1e9:.2f}bn" if a >= 1e9 else f"{a / 1e6:.1f}m" if a >= 1e6 else f"{a:,.0f}"
    return f"{'-' if x < 0 else '+'}${body}"


def _num(x: Any, default: float = 0.0) -> float:
    try:
        v = float(x)
        return default if np.isnan(v) else v
    except (TypeError, ValueError):
        return default


def scenario_assumptions(event: StressEvent, overrides: dict | None = None) -> dict:
    cfg = stress_config()
    spec = cfg["events"].get(event.event_type, cfg["events"]["OTHER"])
    g = cfg["global"]
    sev = float(np.clip(event.impact_score / 10.0, 0.0, 1.0)) ** g["severity_exponent"]
    ov = overrides or {}
    parallel = ov.get("parallel_bp", spec["rates"]["parallel_bp"])
    eq_default = ov.get("equity_default", spec["equity"]["default"])
    direction = 1.0
    sector_active = True
    if spec["direction_from_sentiment"]:
        direction = 1.0 if event.sentiment >= 0 else -1.0
        sector_active = event.sentiment <= 0           # adverse sector read-across only for negative news
    return {
        "event_type": event.event_type, "label": spec["label"], "description": spec["description"], "scope": spec["scope"],
        "severity_scalar": round(sev, 4), "impact_score": event.impact_score,
        "equity_default_at_impact10": eq_default, "equity_default_effective": round(eq_default * sev, 5),
        "sector_shocks_at_impact10": spec["equity"]["sector"], "named_entity_shock_at_impact10": spec["equity"].get("named_entity_shock", 0.0),
        "parallel_bp_effective": round(parallel * sev, 2), "spread_bp_effective": {k: round(v * sev, 2) for k, v in spec["spreads_bp"].items()},
        "sector_shocks_effective": {k: round(v * sev, 5) for k, v in spec["equity"]["sector"].items()} if sector_active else {},
        "pd_multiplier_effective": round(1 + (spec["loans"]["pd_multiplier"] - 1) * sev, 3),
        "pd_multiplier_at_impact10": spec["loans"]["pd_multiplier"], "sector_pd_multiplier_at_impact10": spec["loans"]["sector_pd_multiplier"],
        "vol_shock_effective": round(spec["derivatives"]["vol_shock"] * sev, 4), "direction": direction, "sector_shocks_active": sector_active,
        "direct_exposure_multiplier": g["direct_exposure_multiplier"], "named_tickers": list(event.tickers), "named_sectors": list(event.sectors),
        "disclaimer": DISCLAIMER, "_spec": spec, "_sev": sev, "_parallel": parallel * sev, "_eq_default": eq_default,
    }


def _equity_shock(ticker: str, sector: str, beta: float, a: dict, named: bool) -> float:
    spec = a["_spec"]
    sev, g_mult = a["_sev"], a["direct_exposure_multiplier"]
    base = spec["equity"]["sector"].get(sector, a["_eq_default"]) if a["sector_shocks_active"] else (0.0 if spec["scope"] == "targeted" else a["_eq_default"])
    if spec["scope"] == "targeted" and not a["sector_shocks_active"]:
        base = 0.0
    shock = base * sev * (0.6 + 0.4 * beta)
    if named:
        named_shock = spec["equity"].get("named_entity_shock", 0.0)
        shock = shock * g_mult + named_shock * sev * a["direction"] * (1.0 if spec["direction_from_sentiment"] else 1.0)
    return float(np.clip(shock, -0.95, 1.0))


def run_stress(holdings: pd.DataFrame, event: StressEvent, betas: dict[str, float] | None = None, overrides: dict | None = None, base_yield: float | None = None) -> dict:
    cfg = stress_config()
    g = cfg["global"]
    a = scenario_assumptions(event, overrides)
    spec, sev = a["_spec"], a["_sev"]
    betas = betas or {}
    named_t, named_s = set(event.tickers), set(event.sectors)
    dy_bp = a["_parallel"]
    dy = dy_bp / 1e4
    spreads = {k: v * sev for k, v in spec["spreads_bp"].items()}
    vol_shock = spec["derivatives"]["vol_shock"] * sev
    loan_cfg = g.get("loan", {"floating_pd_uplift_per_100bp": 0.20, "spread_beta": 0.5})
    rows = []

    for h in holdings.to_dict("records"):
        cls, sector, ticker = h["asset_class"], h.get("sector") or "", h.get("ticker") or ""
        mv = _num(h["market_value"])
        named = bool(ticker and ticker in named_t) or (sector in named_s and cls != "derivative" and spec["scope"] == "targeted" and not ticker)
        d: dict[str, float] = {}
        note = ""
        if cls == "equity":
            beta = betas.get(ticker, 1.0)
            r = _equity_shock(ticker, sector, beta, a, named)
            d["equity_price"] = mv * r
        elif cls == "bond":
            D, C = _num(h.get("modified_duration")), _num(h.get("convexity"))
            bucket = "HY" if str(h.get("rating")) in HY else "IG"
            ds = 0.0 if h["instrument_type"] == "govt_bond" else spreads.get(bucket, 0.0) * (g["direct_exposure_multiplier"] if (ticker and ticker in named_t) else 1.0)
            d["rates"] = mv * (-D * dy)
            d["convexity"] = mv * (0.5 * C * dy * dy) if g.get("bond_convexity_term", True) else 0.0
            d["credit_spread"] = mv * (-D * ds / 1e4)
            if ticker and ticker in named_t:
                nshock = spec["equity"].get("named_entity_shock", 0.0) * sev * a["direction"] * 0.25
                d["credit_spread"] += mv * nshock
                note = "issuer named in event"
        elif cls == "loan":
            pd0, lgd, notional = _num(h.get("pd")), _num(h.get("lgd"), 0.45), _num(h["notional"])
            mult = spec["loans"]["sector_pd_multiplier"].get(sector, spec["loans"]["pd_multiplier"])
            m = 1 + (mult - 1) * sev
            if ticker and ticker in named_t:
                m *= g["direct_exposure_multiplier"]
            floating = h.get("rate_type") == "floating"
            if floating and dy_bp > 0:
                m *= 1 + loan_cfg["floating_pd_uplift_per_100bp"] * dy_bp / 100.0
            pd1 = min(1.0, pd0 * m)
            D = _num(h.get("modified_duration"))
            bucket = "HY" if str(h.get("rating")) in HY else "IG"
            d["default"] = -(pd1 - pd0) * lgd * notional
            d["credit_spread"] = -mv * D * loan_cfg["spread_beta"] * spreads.get(bucket, 0.0) / 1e4
            d["rates"] = 0.0 if floating else -mv * D * dy
        elif cls == "derivative":
            it = h["instrument_type"]
            if it.endswith("_option"):
                und = h.get("underlying_ticker") or ""
                if und in ("SPX", ""):
                    r = float(np.clip(a["_eq_default"] * sev * (1 if a["sector_shocks_active"] or spec["scope"] == "broad" else 0), -0.95, 1.0))
                else:
                    u_sector = (h.get("sector") or "")
                    r = _equity_shock(und, u_sector, betas.get(und, 1.0), a, und in named_t)
                d["equity_price"] = _num(h.get("delta_usd")) * r + 0.5 * _num(h.get("gamma_usd")) * r * r
                d["volatility"] = _num(h.get("vega_usd")) * vol_shock
            elif it.startswith("irs"):
                d["rates"] = _num(h.get("dv01_usd")) * dy_bp
            elif it == "cds_protection":
                bucket = "HY" if str(h.get("rating")) in HY else "IG"
                ds_bp = spreads.get(bucket, 0.0) * (g["direct_exposure_multiplier"] if (ticker and ticker in named_t) else 1.0)
                d["credit_spread"] = _num(h.get("cs01_usd")) * ds_bp
        total = float(sum(d.values()))
        rows.append({
            "holding_id": h["holding_id"], "name": h["name"], "asset_class": cls, "instrument_type": h["instrument_type"], "sector": sector, "ticker": ticker,
            "rating": h.get("rating") or "", "value_before": round(mv, 2), "value_after": round(mv + total, 2), "pnl": round(total, 2),
            "pct_change": round(total / mv, 6) if abs(mv) > 1e-6 and cls != "derivative" else (round(total / _num(h["notional"]), 6) if _num(h["notional"]) else 0.0),
            "named_in_event": named, "drivers": {k: round(v, 2) for k, v in d.items() if abs(v) > 0.005}, "note": note,
        })

    df = pd.DataFrame(rows)
    before, pnl = float(df["value_before"].sum()), float(df["pnl"].sum())

    def group(col: str) -> list[dict]:
        gdf = df.groupby(col).agg(value_before=("value_before", "sum"), pnl=("pnl", "sum")).reset_index().rename(columns={col: "key"})
        gdf["pct_change"] = np.where(gdf["value_before"].abs() > 1, gdf["pnl"] / gdf["value_before"], 0.0)
        gdf["share_of_loss"] = np.where(abs(pnl) > 1e-6, gdf["pnl"] / pnl, 0.0)
        gdf["weight"] = gdf["value_before"] / before if before else 0.0
        return gdf.sort_values("pnl").round(6).to_dict("records")

    drivers: dict[str, float] = {}
    for r in rows:
        for k, v in r["drivers"].items():
            drivers[k] = drivers.get(k, 0.0) + v
    by_driver = [{"key": k, "label": DRIVER_LABELS.get(k, k), "pnl": round(v, 2), "share_of_loss": round(v / pnl, 4) if abs(pnl) > 1e-6 else 0.0}
                 for k, v in sorted(drivers.items(), key=lambda kv: kv[1])]
    top = df.sort_values("pnl").head(5)
    by_class, by_sector = group("asset_class"), group("sector")
    a_public = {k: v for k, v in a.items() if not k.startswith("_")}
    if base_yield is not None:      # live 10-year US Treasury yield (display context: shocks are additive shifts)
        a_public["base_10y_yield"] = round(base_yield, 5)
        a_public["shocked_10y_yield"] = round(base_yield + dy, 5)

    parts = []
    parts.append(f"A {a['label'].lower()} scenario at impact {event.impact_score:.1f}/10 (severity scalar {sev:.2f}) moves this portfolio by {pnl / before:+.2%} ({_usd(pnl)}).")
    if by_driver:
        w = by_driver[0]
        parts.append(f"The largest driver is {w['label'].lower()} ({_usd(w['pnl'])}).")
    if by_class:
        wc = by_class[0]
        parts.append(f"{wc['key'].replace('_', ' ').title()} holdings contribute the most to the move ({_usd(wc['pnl'])}; {wc['weight']:.0%} of portfolio value).")
    if len(top) and top.iloc[0]["pnl"] < 0:
        parts.append("Most vulnerable positions: " + "; ".join(f"{r.name} ({_usd(r.pnl)})" for r in top.head(3).itertuples()) + ".")
    if abs(dy_bp) >= 1:
        parts.append(f"Effective parallel yield shift {dy_bp:+.0f} bp.")
    if a["scope"] == "targeted":
        parts.append("Targeted event: only named companies and directly affected sectors are shocked.")

    notes = []
    if event.sentiment >= 0.15 and spec["scope"] == "broad":
        notes.append("The news tone of this event is positive (for example a ceasefire or a deal), but the stress test always applies the ADVERSE version of the scenario. Read the result as a worst case if the situation went wrong, not as a prediction.")
    return {
        "event": {"event_id": event.event_id, "name": event.name, "event_type": event.event_type, "impact_score": event.impact_score, "sentiment": event.sentiment,
                  "tickers": list(event.tickers), "sectors": list(event.sectors)},
        "assumptions": a_public,
        "value_before": round(before, 2), "value_after": round(before + pnl, 2), "pnl": round(pnl, 2), "pct_impact": round(pnl / before, 6) if before else 0.0,
        "by_asset_class": by_class, "by_sector": by_sector, "by_driver": by_driver,
        "assets": df.sort_values("pnl").to_dict("records"), "top_losers": top[["holding_id", "name", "pnl"]].round(2).to_dict("records"),
        "explanation": " ".join(parts), "disclaimer": DISCLAIMER, "notes": notes,
    }
