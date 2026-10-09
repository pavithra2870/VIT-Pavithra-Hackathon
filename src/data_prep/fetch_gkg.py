"""Fetch GDELT GKG (themes + organisations) for the events we hold and store a COMPACT per-day file.

    python -m src.data_prep.fetch_gkg

* Days with a daily event export -> GKG 1.0 daily file   (https://data.gdeltproject.org/gkg/YYYYMMDD.gkg.csv.zip, ~30 MB zip)
* The in-progress day            -> GKG 2.0 15-minute file for each live event slot (https://data.gdeltproject.org/gdeltv2/<slot>.gkg.csv.zip)
The raw download is filtered to the article URLs present in our event files, reduced to themes that matter to
data/config/gkg_themes.json and to organisations that link to our company universe, and written to
data/gdelt-themes/<day>.csv (url, themes, orgs). The raw GKG is never stored. Failures never raise.
"""
from __future__ import annotations

import datetime as dt
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd

from src.config import GKG_DIR, GKG_LIVE, LIVE_GDELT, RAW_GDELT
from src.risk_engine import gkg

BASE_V1 = "https://data.gdeltproject.org/gkg/{d}.gkg.csv.zip"
BASE_V2 = "https://data.gdeltproject.org/gdeltv2/{ts}.gkg.csv.zip"


def _compact(df: pd.DataFrame, urls: set[str]) -> pd.DataFrame:
    """df columns: url, themes, orgs (raw, ';'-separated) -> compact rows for urls we hold."""
    df = df[df["url"].isin(urls)].copy()
    df["themes"] = df["themes"].fillna("").map(lambda s: ";".join(gkg.keep_themes(gkg.split_themes(s))))
    df["orgs"] = df["orgs"].fillna("").map(lambda s: ";".join(gkg.linkable_orgs(gkg.split_themes(s))))
    df = df[(df["themes"] != "") | (df["orgs"] != "")]
    return df.drop_duplicates("url")[["url", "themes", "orgs"]]


def _read_zip_csv(content: bytes, usecols: list[int], names: list[str]) -> pd.DataFrame:
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        return pd.read_csv(io.BytesIO(z.read(member)), sep="\t", header=None, usecols=usecols, names=None, dtype=str, quoting=3,
                           on_bad_lines="skip", encoding_errors="replace").rename(columns=dict(zip(usecols, names)))


def build_day(day: str) -> str:
    out = GKG_DIR / f"{day}.csv"
    ev = RAW_GDELT / f"{day}.export.CSV"
    if out.exists():
        return "exists"
    if not ev.exists():
        return "no-events"
    urls = set(pd.read_csv(ev, sep="\t", header=None, usecols=[57], dtype=str)[57].dropna())
    r = httpx.get(BASE_V1.format(d=day), timeout=300.0, follow_redirects=True)
    if r.status_code == 404:
        return "missing"
    r.raise_for_status()
    g = _read_zip_csv(r.content, [3, 6, 10], ["themes", "orgs", "url"])
    g["url"] = g["url"].fillna("").str.split("<UDIV>")
    g = g.explode("url")
    GKG_DIR.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    _compact(g, urls).to_csv(tmp, index=False)
    tmp.replace(out)
    return "downloaded"


def build_live_slot(slot: str) -> str:
    out = GKG_LIVE / f"{slot}.csv"
    ev = LIVE_GDELT / f"{slot}.export.CSV"
    if out.exists():
        return "exists"
    if not ev.exists():
        return "no-events"
    urls = set(pd.read_csv(ev, sep="\t", header=None, usecols=[60], dtype=str)[60].dropna())
    r = httpx.get(BASE_V2.format(ts=slot), timeout=120.0, follow_redirects=True)
    if r.status_code == 404:
        return "missing"
    r.raise_for_status()
    g = _read_zip_csv(r.content, [4, 7, 13], ["url", "themes", "orgs"])
    GKG_LIVE.mkdir(parents=True, exist_ok=True)
    tmp = out.with_suffix(".tmp")
    _compact(g, urls).to_csv(tmp, index=False)
    tmp.replace(out)
    return "downloaded"


def prune_live() -> int:
    """Drop live GKG files whose event slot has been superseded by a daily export."""
    n = 0
    for p in GKG_LIVE.glob("*.csv"):
        if not (LIVE_GDELT / f"{p.stem}.export.CSV").exists():
            p.unlink()
            n += 1
    return n


def fetch_gkg() -> dict:
    """Bring GKG enrichment up to date for every event file we hold. Never raises."""
    GKG_DIR.mkdir(parents=True, exist_ok=True)
    res = {"daily": 0, "live": 0, "pruned": 0, "error": None}
    try:
        res["pruned"] = prune_live() if GKG_LIVE.exists() else 0
        days = sorted(p.name[:8] for p in RAW_GDELT.glob("*.export.CSV"))
        with ThreadPoolExecutor(max_workers=2) as ex:
            res["daily"] = sum(1 for r in ex.map(build_day, days) if r == "downloaded")
        slots = sorted(p.name[:14] for p in LIVE_GDELT.glob("*.export.CSV"))
        with ThreadPoolExecutor(max_workers=6) as ex:
            res["live"] = sum(1 for r in ex.map(build_live_slot, slots) if r == "downloaded")
    except Exception as exc:
        res["error"] = f"{type(exc).__name__}: {exc}"
    return res


if __name__ == "__main__":
    print(fetch_gkg())
