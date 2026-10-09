"""GDELT GKG enrichment: themes -> sectors / event evidence, and organisation names -> company tickers.

GKG (Global Knowledge Graph) tags every article with machine-extracted themes and organisations. We join it to the GDELT
events by article URL (see src/data_prep/fetch_gkg.py) and use it instead of guessing from words in a URL slug.
Mapping rules are data (data/config/gkg_themes.json), not code.
"""
from __future__ import annotations

import json
import re
from functools import lru_cache

from src.config import CONFIG

_CORP = re.compile(r"\b(corporation|corp|incorporated|inc|company|co|ltd|limited|plc|group|holdings|holding|the)\b\.?", re.I)


@lru_cache(maxsize=1)
def _cfg() -> dict:
    return json.loads((CONFIG / "gkg_themes.json").read_text(encoding="utf-8"))


@lru_cache(maxsize=1)
def _rules() -> tuple[list, list]:
    c = _cfg()
    sec = [(re.compile(r["pattern"]), r["sector"], r["weight"]) for r in c["sector_themes"]]
    ev = [(re.compile(r["pattern"]), r["event_type"], r["weight"]) for r in c["event_themes"]]
    return sec, ev


def split_themes(s: str | None) -> list[str]:
    return [t for t in (s or "").split(";") if t]


def keep_themes(themes: list[str]) -> list[str]:
    """Only themes that matter to some rule are stored (keeps /data small)."""
    sec, ev = _rules()
    rx = [r[0] for r in sec] + [r[0] for r in ev]
    return sorted({t for t in themes if any(r.search(t) for r in rx)})


def sector_tags(themes: list[str]) -> list[str]:
    """Sectors evidenced by the themes (summed rule weights >= min_sector_weight), strongest first."""
    sec, _ = _rules()
    score: dict[str, float] = {}
    for t in set(themes):                      # every DISTINCT theme counts once, at the weight of its (first) matching rule
        for rx, sector, w in sec:
            if rx.search(t):
                score[sector] = score.get(sector, 0.0) + w
                break
    c = _cfg()
    ranked = sorted(((s, v) for s, v in score.items() if v >= c["min_sector_weight"]), key=lambda kv: -kv[1])
    return [s for s, _ in ranked[: c["max_sectors"]]]


def event_evidence(themes: list[str]) -> dict[str, tuple[float, list[str]]]:
    """event_type -> (summed weight, matched theme codes)."""
    _, ev = _rules()
    scale = _cfg().get("event_theme_scale", 1.0)
    out: dict[str, tuple[float, list[str]]] = {}
    for rx, etype, w in ev:
        hit = [t for t in themes if rx.search(t)]
        if hit:
            cur = out.get(etype, (0.0, []))
            out[etype] = (cur[0] + w * scale, cur[1] + hit[:2])
    return out


def norm_org(name: str) -> str:
    return re.sub(r"\s+", " ", _CORP.sub(" ", re.sub(r"[^a-z0-9&' ]", " ", name.lower()))).strip()


@lru_cache(maxsize=1)
def _alias_map() -> dict[str, str]:
    from src.risk_engine.entity.extractor import load_universe

    m: dict[str, str] = {}
    for _, r in load_universe().iterrows():
        for a in r["aliases"]:
            m.setdefault(norm_org(a), r["ticker"])
        m.setdefault(norm_org(r["name"]), r["ticker"])
    return {k: v for k, v in m.items() if len(k) >= 3}


def link_orgs(orgs: list[str]) -> list[str]:
    """GKG organisation names -> tickers of companies in our universe (exact match after stripping 'Inc', 'Corp', ...)."""
    amap = _alias_map()
    return sorted({amap[n] for n in map(norm_org, orgs) if n in amap})


def linkable_orgs(orgs: list[str]) -> list[str]:
    amap = _alias_map()
    return [o for o in orgs if norm_org(o) in amap]
