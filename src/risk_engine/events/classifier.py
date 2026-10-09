"""Deterministic, explainable event classifier.

Score(type) = sum of weights of matched keyword patterns (+ CAMEO prior for GDELT events).
Confidence combines the winner's share of total evidence with how much evidence there is, so a single
weak keyword hit does not produce a confident label.
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from functools import lru_cache

from src.config import taxonomy

CAMEO_PRIOR_WEIGHT = 2.0


@dataclass
class EventResult:
    event_type: str
    confidence: float
    evidence: float                      # keyword evidence strength 0-1 (saturating)
    matched: list[str] = field(default_factory=list)
    scores: dict[str, float] = field(default_factory=dict)


@lru_cache(maxsize=1)
def _compiled() -> dict[str, list[tuple[re.Pattern, float]]]:
    out = {}
    for etype, spec in taxonomy()["event_types"].items():
        out[etype] = [(re.compile(p, re.I), w) for p, w in spec["keywords"].items()]
    return out


def classify_event(text: str, cameo_root: str | None = None, themes: list[str] | None = None) -> EventResult:
    scores: dict[str, float] = {}
    matched: dict[str, list[str]] = {}
    for etype, pats in _compiled().items():
        for rx, w in pats:
            m = rx.search(text)
            if m:
                scores[etype] = scores.get(etype, 0.0) + w
                matched.setdefault(etype, []).append(m.group(0).lower())
    if themes:      # GDELT GKG themes are machine-extracted topic tags: independent textual evidence for the class
        from src.risk_engine import gkg

        for etype, (w, hits) in gkg.event_evidence(themes).items():
            scores[etype] = scores.get(etype, 0.0) + w
            matched.setdefault(etype, []).extend(f"theme:{h}" for h in hits)
    keyword_total = sum(scores.values())
    if cameo_root:
        mapped = taxonomy()["cameo_map"].get(cameo_root)
        if mapped:
            scores[mapped] = scores.get(mapped, 0.0) + CAMEO_PRIOR_WEIGHT
            matched.setdefault(mapped, []).append(f"cameo:{cameo_root}")
    if not scores:
        return EventResult("OTHER", 0.30, 0.0, [], {})
    top = max(scores, key=scores.get)
    total = sum(scores.values())
    share = scores[top] / total
    saturation = 1 - math.exp(-scores[top] / 3.0)
    conf = float(min(0.98, 0.15 + 0.80 * share * (0.25 + 0.75 * saturation)))
    kw_top = scores[top] - (CAMEO_PRIOR_WEIGHT if (cameo_root and taxonomy()["cameo_map"].get(cameo_root) == top) else 0.0)
    if keyword_total == 0:  # label rests on the CAMEO code alone: no textual corroboration
        conf = min(conf, 0.40)
    evidence = 1 - math.exp(-max(kw_top, 0.0) / 3.0)
    return EventResult(top, round(conf, 3), round(evidence, 3), matched.get(top, [])[:5], {k: round(v, 2) for k, v in scores.items()})
