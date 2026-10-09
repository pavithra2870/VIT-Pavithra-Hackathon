"""Module A - Tactical Rebalancer: turns Risk Signals into constrained index-weight changes.

Per stock i, as of date T (signals inside the look-back window, exponentially decayed with half-life h):

  direct_i  = sum_signals  sentiment_i * (impact/10) * confidence * relevance_i * decay * (loss_aversion if sentiment<0)
  sector_i  = sum_signals  sentiment   * (impact/10) * confidence * 0.5 * decay          for sector-level signals naming i's sector
  macro_i   = tanh( (M_i - mean_j M_j) / K ),  M_i = sum_broad_signals -pressure * (impact/10) * confidence * decay * vulnerability_sector(i)
              pressure = 2*adverse_weight-1 (event class + sentiment), vulnerability = -sector_shock / 10%  (from stress_scenarios.json)
  score_i   = (direct + sector + macro) * vol_adjust_i        vol_adjust shrinks upgrades / amplifies downgrades for volatile names
  target_i  ~ w_i * exp(tilt_strength * tanh(score_i - index_avg_score)), renormalised   (relative signal: weights sum to 100 %)

then constraints are enforced: min/max weight, sector cap, sum = 100 %, and a one-way turnover budget (step scaled back
towards the current weights, which keeps every constraint satisfied because the feasible set is convex).
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd

from src.config import engine_config, stress_config

MACRO_K = 100.0
SECTOR_SIGNAL_SCALE = 0.5


def capped_weights(caps: dict[str, float], cap: float = 0.12) -> dict[str, float]:
    """Market-cap weights with an iterative single-stock cap (excess redistributed pro rata)."""
    s = pd.Series(caps, dtype=float)
    w = s / s.sum()
    for _ in range(50):
        over = w > cap + 1e-12
        if not over.any():
            break
        excess = (w[over] - cap).sum()
        w[over] = cap
        w[~over] += excess * w[~over] / w[~over].sum()
    return (w / w.sum()).to_dict()


def _decay(age_days: float, half_life: float) -> float:
    return 0.5 ** (max(age_days, 0.0) / half_life)


def project_weights(w: np.ndarray, lo: float, hi: float) -> np.ndarray:
    """Clip to [lo, hi] and renormalise to sum 1 by redistributing over unconstrained names."""
    w = np.asarray(w, float).copy()
    fixed = np.zeros(len(w), bool)
    for _ in range(50):
        w[~fixed] = np.maximum(w[~fixed], 0)
        free_total = 1.0 - w[fixed].sum()
        if (~fixed).sum() == 0:
            break
        s = w[~fixed].sum()
        w[~fixed] = w[~fixed] * (free_total / s) if s > 0 else free_total / (~fixed).sum()
        over, under = (w > hi + 1e-12) & ~fixed, (w < lo - 1e-12) & ~fixed
        if not over.any() and not under.any():
            break
        w[over], w[under] = hi, lo
        fixed |= over | under
    return w


def enforce_sector_cap(w: np.ndarray, sectors: np.ndarray, cap: float, lo: float, hi: float) -> np.ndarray:
    w = w.copy()
    for _ in range(20):
        changed = False
        for s in set(sectors):
            m = sectors == s
            tot = w[m].sum()
            if tot > cap + 1e-9:
                w[m] *= cap / tot
                other = ~m
                w[other] += (tot - cap) * w[other] / w[other].sum()
                changed = True
        w = project_weights(w, lo, hi)
        if not changed:
            break
    return w


def compute_scores(index: pd.DataFrame, signals: pd.DataFrame, entities: pd.DataFrame, ctx: pd.DataFrame, as_of: pd.Timestamp, cfg: dict) -> pd.DataFrame:
    sc = stress_config()["events"]
    lookback, half = cfg["lookback_days"], cfg["signal_half_life_days"]
    la = cfg["loss_aversion"]
    sig = signals.copy()
    sig["age"] = (as_of.normalize() - pd.to_datetime(sig["signal_date"])).dt.days
    sig = sig[(sig["age"] >= 0) & (sig["age"] <= lookback)]
    sig["decay"] = sig["age"].map(lambda a: _decay(a, half))
    sig["strength"] = sig["impact_score"] / 10.0 * sig["confidence"] * sig["decay"]
    tick = set(index["ticker"])
    ent = entities[entities["ticker"].isin(tick) & entities["event_id"].isin(sig["event_id"])].merge(
        sig[["event_id", "strength", "event_name", "event_type", "impact_score", "signal_date"]], on="event_id")
    ent["contrib"] = ent["sentiment"] * ent["strength"] * ent["relevance"] * np.where(ent["sentiment"] < 0, la, 1.0)

    rows, macro_raws = [], []
    sector_sigs = sig[sig["cluster_kind"] == "S"]
    broad = sig[(sig["cluster_kind"].isin(["G", "M"])) & (sig["event_type"].map(lambda t: sc.get(t, {}).get("scope") == "broad"))]
    for r in index.itertuples():
        e = ent[ent["ticker"] == r.ticker].sort_values("contrib", key=lambda s: -s.abs())
        direct = float(e["contrib"].sum())
        ss = sector_sigs[sector_sigs["affected_sectors"].fillna("").str.split(";").map(lambda l: r.sector in l)]
        sector = float((ss["sentiment"] * ss["strength"] * SECTOR_SIGNAL_SCALE).sum())
        macro_raw = 0.0
        for b in broad.itertuples():
            shock = sc[b.event_type]["equity"]["sector"].get(r.sector, sc[b.event_type]["equity"]["default"])
            vuln = -shock / 0.10
            macro_raw += -(2 * b.adverse_weight - 1) * b.strength * vuln
        macro_raws.append(macro_raw)
        c = ctx.loc[ctx["ticker"] == r.ticker]
        vol = float(c["vol_60d"].iloc[0]) if len(c) else float(ctx["vol_60d"].median())
        z = float(np.clip((vol - ctx["vol_60d"].median()) / ctx["vol_60d"].median(), -1, 1))
        top = e.iloc[0] if len(e) else None
        rows.append({
            "ticker": r.ticker, "name": r.name, "sector": r.sector, "current_weight": r.weight, "vol_60d": vol, "beta": float(c["beta"].iloc[0]) if len(c) else 1.0,
            "direct_score": direct, "sector_score": sector, "vol_z": max(z, 0.0), "n_signals": int(len(e) + len(ss)),
            "avg_sentiment": float(e["sentiment"].mean()) if len(e) else 0.0,
            "top_event_id": top["event_id"] if top is not None else "", "top_event": top["event_name"] if top is not None else "",
            "top_event_type": top["event_type"] if top is not None else "", "top_event_impact": float(top["impact_score"]) if top is not None else 0.0,
        })
    out = pd.DataFrame(rows)
    # market-wide pressure moves every stock alike and is irrelevant for RELATIVE weights: keep only the cross-sectional
    # (sector-vulnerability) differential, squashed by tanh
    mr = np.array(macro_raws)
    out["macro_score"] = np.tanh((mr - mr.mean()) / cfg.get("macro_scale", MACRO_K))
    out["macro_raw"] = mr
    out["vol_adjust"] = out.apply(lambda r: _vol_adj(r, cfg), axis=1)
    out["score"] = (out["direct_score"] + out["sector_score"] + out["macro_score"]) * out["vol_adjust"]
    return out


def _vol_adj(r: pd.Series, cfg: dict) -> float:
    raw = r["direct_score"] + r["sector_score"] + r["macro_score"]
    return 1 / (1 + cfg["volatility_dampening"] * r["vol_z"]) if raw >= 0 else 1 + cfg["volatility_dampening"] * r["vol_z"]


def _portfolio_vol(w: np.ndarray, cov: np.ndarray | None) -> float:
    return float(np.sqrt(w @ cov @ w)) if cov is not None else float("nan")


def rebalance(index: pd.DataFrame, signals: pd.DataFrame, entities: pd.DataFrame, ctx: pd.DataFrame, as_of: pd.Timestamp,
              cov: np.ndarray | None = None, overrides: dict | None = None, start_weights: dict | None = None) -> dict[str, Any]:
    cfg = {**engine_config()["rebalancer"], **{k: v for k, v in (overrides or {}).items() if v is not None}}
    idx = index.copy()
    if start_weights:
        idx["weight"] = idx["ticker"].map(start_weights).fillna(idx["weight"])
        idx["weight"] /= idx["weight"].sum()
    sc = compute_scores(idx, signals, entities, ctx, as_of, cfg)
    w0 = sc["current_weight"].to_numpy()
    lo, hi, cap, max_to = cfg["min_weight"], cfg["max_weight"], cfg["max_sector_weight"], cfg["max_turnover"]
    sectors = sc["sector"].to_numpy()

    # weights must sum to 100 %, so only a signal RELATIVE to the (weight-averaged) index signal can move weight between names
    sc["relative_score"] = sc["score"] - float((w0 * sc["score"]).sum())
    tilt = cfg["tilt_strength"] * np.tanh(sc["relative_score"].to_numpy())
    target = w0 * np.exp(tilt)
    target /= target.sum()
    target = enforce_sector_cap(project_weights(target, lo, hi), sectors, cap, lo, hi)
    raw_turnover = 0.5 * np.abs(target - w0).sum()
    scale = 1.0
    if raw_turnover > max_to:
        scale = max_to / raw_turnover
        target = w0 + scale * (target - w0)
    turnover = 0.5 * np.abs(target - w0).sum()

    sc["proposed_weight"] = target
    sc["change"] = target - w0
    sc["unconstrained_tilt"] = tilt
    sc["flags"] = [("at max weight" if abs(t - hi) < 1e-6 else "at min weight" if abs(t - lo) < 1e-6 else "") for t in target]

    def _drivers(r: pd.Series) -> str:
        parts = []
        if abs(r["direct_score"]) > 0.005:
            parts.append(f"company news {r['direct_score']:+.2f}")
        if abs(r["sector_score"]) > 0.005:
            parts.append(f"sector news {r['sector_score']:+.2f}")
        if abs(r["macro_score"]) > 0.005:
            parts.append(f"macro/geopolitical exposure {r['macro_score']:+.2f}")
        if not parts:
            return "No material signal in window - weight held (renormalised only)."
        v = "dampened" if r["vol_adjust"] < 0.995 else ("amplified" if r["vol_adjust"] > 1.005 else "")
        return ", ".join(parts) + (f"; volatility {r['vol_60d']:.0%} {v} the tilt" if v else "")

    sc["driver"] = sc.apply(_drivers, axis=1)

    def _reason(r: pd.Series) -> str:
        """One plain-language sentence naming the dominant signal behind the stock's score."""
        comp = {"company": r["direct_score"], "sector": r["sector_score"], "macro": r["macro_score"]}
        k = max(comp, key=lambda x: abs(comp[x]))
        v = comp[k]
        if abs(v) < 0.02:
            return "No strong signal either way"
        if k == "company":
            return f"Company news is {'positive' if v > 0 else 'negative'}"
        if k == "sector":
            return f"{r['sector']} sector news is {'positive' if v > 0 else 'negative'}"
        return "Well placed for the current geopolitical / macro backdrop" if v > 0 else "Exposed to the current geopolitical / macro backdrop"

    sc["reason"] = sc.apply(_reason, axis=1)
    sc["vs_index"] = np.where(sc["relative_score"] > 0.02, "stronger than the index average", np.where(sc["relative_score"] < -0.02, "weaker than the index average", "in line with the index average"))
    sc["action"] = np.where(sc["change"] > 0.0005, "increase", np.where(sc["change"] < -0.0005, "decrease", "hold"))

    sec_before = sc.groupby("sector")["current_weight"].sum()
    sec_after = sc.groupby("sector")["proposed_weight"].sum()
    metrics = {
        "turnover": float(turnover), "unscaled_turnover": float(raw_turnover), "turnover_scaled_back": bool(scale < 1.0), "step_scale": float(scale),
        "vol_before": _portfolio_vol(w0, cov), "vol_after": _portfolio_vol(target, cov),
        "hhi_before": float((w0 ** 2).sum()), "hhi_after": float((target ** 2).sum()),
        "signal_score_before": float((w0 * sc["score"]).sum()), "signal_score_after": float((target * sc["score"]).sum()),
        "weighted_sentiment_before": float((w0 * sc["avg_sentiment"]).sum()), "weighted_sentiment_after": float((target * sc["avg_sentiment"]).sum()),
        "sum_weights": float(target.sum()),
        "sector_weights": [{"sector": s, "before": float(sec_before[s]), "after": float(sec_after[s])} for s in sec_before.index],
    }
    return {"as_of": str(as_of.date()), "constraints": {"min_weight": lo, "max_weight": hi, "max_turnover": max_to, "max_sector_weight": cap, **{k: cfg[k] for k in ("tilt_strength", "loss_aversion", "volatility_dampening", "signal_half_life_days", "lookback_days")}},
            "stocks": sc.round(6).to_dict("records"), "metrics": metrics}
