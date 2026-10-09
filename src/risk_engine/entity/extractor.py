"""Entity detection and ticker normalisation.

Gazetteer-driven (data/reference/company_universe.csv) so it is fully offline and deterministic:
  * company names / aliases  -> canonical ticker   (case-sensitive for prose, case-insensitive 'safe' aliases for URL slugs)
  * cashtags ($NVDA)         -> ticker (validated against the S&P 500 ticker list)
  * Reuters RIC tags (GE.N)  -> ticker
  * sector keywords          -> GICS sector (used when no company is named)
The extractor is a plain class so a spaCy NER pass can be swapped in behind the same `extract` interface.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache

import pandas as pd

from src.config import MARKET, REFERENCE, taxonomy

# Aliases that are ordinary English words: only matched in prose when capitalised, never inside lower-case URL slugs.
_AMBIGUOUS_SLUG = {"apple", "ford", "meta", "oracle", "intel", "dow", "visa", "abbott", "johnson", "goldman", "lilly", "reliance",
                   "southern", "target", "amazon", "alphabet", "cisco", "ups", "atandt", "linde", "deere", "schwab", "boeing", "sbi", "tcs", "itc",
                   "merck", "pepsi", "shell", "gap", "citi", "disney", "airtel", "adani", "lockheed", "duke", "berkshire", "hon", "gilead"}
# slug-safe subset: distinctive tokens that realistically refer to the company
_SLUG_SAFE = {"nvidia", "tesla", "microsoft", "amazon", "google", "alphabet", "boeing", "exxon", "chevron", "jpmorgan", "walmart", "netflix", "pfizer",
              "goldman sachs", "morgan stanley", "wells fargo", "citigroup", "bank of america", "blackrock", "mastercard", "salesforce", "broadcom",
              "qualcomm", "caterpillar", "lockheed martin", "honeywell", "starbucks", "mcdonald", "coca cola", "costco", "unitedhealth", "reliance industries",
              "tata motors", "infosys", "hdfc bank", "icici bank", "state bank of india", "adani", "apple inc", "general motors", "disney", "intel corp", "meta platforms"}


@dataclass
class Mention:
    ticker: str
    name: str
    sector: str
    country: str
    count: int
    in_title: bool
    first_pos: float
    relevance: float


@dataclass
class Entities:
    mentions: list[Mention]
    sectors: list[str]


@lru_cache(maxsize=1)
def load_universe() -> pd.DataFrame:
    rows = []
    for line in (REFERENCE / "company_universe.csv").read_text(encoding="utf-8").splitlines()[1:]:
        parts = line.split("|")
        if len(parts) < 5:
            continue
        rows.append({"ticker": parts[0], "name": parts[1], "sector": parts[2], "country": parts[3], "aliases": parts[4:]})
    return pd.DataFrame(rows)


@lru_cache(maxsize=1)
def sp500_tickers() -> frozenset:
    return frozenset(pd.read_csv(MARKET / "sp500-daily-tickers-2025.csv", usecols=["ticker"])["ticker"].unique())


class EntityExtractor:
    def __init__(self) -> None:
        uni = load_universe()
        self.meta = uni.set_index("ticker")[["name", "sector", "country"]].to_dict("index")
        alias_to_ticker: dict[str, str] = {}
        for _, r in uni.iterrows():
            for a in r["aliases"]:
                alias_to_ticker[a] = r["ticker"]
        # longest alias first so "Goldman Sachs" wins over "Goldman"
        aliases = sorted(alias_to_ticker, key=len, reverse=True)
        self.alias_to_ticker = alias_to_ticker
        self.prose_re = re.compile(r"(?<![A-Za-z0-9])(" + "|".join(re.escape(a) for a in aliases) + r")(?![A-Za-z0-9])")
        slug_aliases = [a for a in aliases if a.lower() in _SLUG_SAFE or (a.lower() not in _AMBIGUOUS_SLUG and len(a) >= 7 and " " in a)]
        self.slug_alias_to_ticker = {a.lower(): alias_to_ticker[a] for a in slug_aliases}
        self.slug_re = re.compile(r"(?<![a-z0-9])(" + "|".join(re.escape(a.lower()) for a in sorted(slug_aliases, key=len, reverse=True)) + r")(?![a-z0-9])") if slug_aliases else None
        self.cashtag_re = re.compile(r"\$([A-Z]{1,5})(?![A-Za-z])")
        self.paren_re = re.compile(r"\(\s*(?:NASDAQ|NYSE|Nasdaq)?\s*:?\s*([A-Z]{1,5})\s*\)|(?:NASDAQ|NYSE)\s*:\s*([A-Z]{1,5})")
        self.ric_re = re.compile(r"\(\s*([A-Z]{1,5})\.(?:N|O|OQ|L|T|NS|BO|PA|DE|HK|AX)\s*\)")
        self.sector_res = {s: re.compile(p, re.I) for s, p in taxonomy()["sector_keywords"].items()}
        self.tickers = sp500_tickers()

    def _meta(self, ticker: str) -> tuple[str, str, str]:
        m = self.meta.get(ticker)
        return (m["name"], m["sector"], m["country"]) if m else (ticker, "Unknown", "US")

    def extract(self, text: str, title: str = "", slug_mode: bool = False) -> Entities:
        counts: dict[str, int] = {}
        first: dict[str, int] = {}
        in_title: set[str] = set()
        body = text or ""

        def add(t: str, pos: int, from_title: bool) -> None:
            counts[t] = counts.get(t, 0) + 1
            first[t] = min(first.get(t, pos), pos)
            if from_title:
                in_title.add(t)

        if slug_mode:
            low = body.lower()
            if self.slug_re:
                for m in self.slug_re.finditer(low):
                    add(self.slug_alias_to_ticker[m.group(1)], m.start(), True)
        else:
            for m in self.prose_re.finditer(body):
                add(self.alias_to_ticker[m.group(1)], m.start(), m.start() < len(title or ""))
            for m in self.cashtag_re.finditer(body):
                t = m.group(1)
                if t in self.tickers or t in self.meta:
                    add(t, m.start(), True)
            for m in self.paren_re.finditer(body):          # "(NVDA)", "(NASDAQ: NVDA)", "NYSE: JPM"
                t = m.group(1) or m.group(2)
                if t in self.meta or (t in self.tickers and len(t) >= 2):
                    add(t, m.start(), m.start() < len(title or ""))
            for m in self.ric_re.finditer(body):
                t = m.group(1)
                if t in self.tickers or t in self.meta:
                    add(t, m.start(), False)

        n = max(len(body), 1)
        mentions = []
        for t, c in counts.items():
            name, sector, country = self._meta(t)
            pos = first[t] / n
            # relevance: saturating mention count + headline prominence + early position
            rel = 0.45 * (1 - 0.5 ** c) + 0.35 * (1.0 if t in in_title else 0.0) + 0.20 * (1 - min(pos * 2, 1.0))
            mentions.append(Mention(t, name, sector, country, c, t in in_title, round(pos, 3), round(min(rel, 1.0), 3)))
        mentions.sort(key=lambda m: -m.relevance)

        sectors = [m.sector for m in mentions if m.sector != "Unknown"]
        for s, rx in self.sector_res.items():
            if rx.search(body) and s not in sectors:
                sectors.append(s)
        return Entities(mentions, sectors)
