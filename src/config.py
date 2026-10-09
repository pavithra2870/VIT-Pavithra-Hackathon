"""Central paths and configuration loaders. Every runtime input lives under /data."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "data"
RAW_GDELT = DATA / "gdelt-recent"
LIVE_GDELT = DATA / "gdelt-live"   # transient GDELT 2.0 15-minute files for days whose daily file is not published yet
GKG_DIR = DATA / "gdelt-themes"            # compact GKG (themes + linkable organisations) per day, joined to events by URL
GKG_LIVE = GKG_DIR / "live"                  # transient, for the in-progress day
NEWS = DATA / "news"
SOCIAL = DATA / "social"
MARKET = DATA / "market"
PORTFOLIOS = DATA / "portfolios"
PROCESSED = DATA / "processed"
CONFIG = DATA / "config"
REFERENCE = DATA / "reference"
DB_PATH = PROCESSED / "risk_intelligence.db"


def _load(name: str) -> dict:
    with open(CONFIG / name, encoding="utf-8") as f:
        return json.load(f)


@lru_cache(maxsize=1)
def engine_config() -> dict:
    return _load("engine_config.json")


@lru_cache(maxsize=1)
def taxonomy() -> dict:
    return _load("event_taxonomy.json")


@lru_cache(maxsize=1)
def stress_config() -> dict:
    return _load("stress_scenarios.json")
