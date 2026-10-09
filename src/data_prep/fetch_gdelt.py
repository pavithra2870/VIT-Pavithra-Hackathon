"""Fetch missing GDELT 1.0 daily event exports into data/gdelt-recent.

    python -m src.data_prep.fetch_gdelt            # every day after the latest local file, up to today (UTC)
    python -m src.data_prep.fetch_gdelt --from 20261006 --to 20261008

GDELT publishes the daily file for day D early on D+1, so "today" is normally not yet available (404 is expected).
Offline / blocked network is handled gracefully: nothing is fetched and the existing data keeps working.
"""
from __future__ import annotations

import argparse
import datetime as dt
import io
import re
import zipfile

import httpx

from concurrent.futures import ThreadPoolExecutor

from src.config import LIVE_GDELT, RAW_GDELT

BASE = "https://data.gdeltproject.org/events/{d}.export.CSV.zip"
BASE_V2 = "https://data.gdeltproject.org/gdeltv2/{ts}.export.CSV.zip"   # GDELT 2.0: one file every 15 minutes


def local_dates() -> list[dt.date]:
    out = []
    for p in RAW_GDELT.glob("*.export.CSV"):
        m = re.match(r"(\d{8})\.export\.CSV$", p.name)
        if m:
            out.append(dt.datetime.strptime(m.group(1), "%Y%m%d").date())
    return sorted(out)


def fetch_day(day: dt.date, timeout: float = 180.0) -> str:
    """Returns 'downloaded' | 'missing' (not published yet) | 'exists'."""
    name = day.strftime("%Y%m%d")
    target = RAW_GDELT / f"{name}.export.CSV"
    if target.exists():
        return "exists"
    r = httpx.get(BASE.format(d=name), timeout=timeout, follow_redirects=True)
    if r.status_code == 404:
        return "missing"
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        RAW_GDELT.mkdir(parents=True, exist_ok=True)
        target.write_bytes(z.read(member))
    return "downloaded"


def fetch_missing(start: dt.date | None = None, end: dt.date | None = None) -> dict:
    have = local_dates()
    today = dt.datetime.now(dt.timezone.utc).date()
    start = start or ((have[-1] + dt.timedelta(days=1)) if have else today - dt.timedelta(days=9))
    end = end or today
    got: list[str] = []
    pending: list[str] = []
    error = None
    d = start
    try:
        while d <= end:
            res = fetch_day(d)
            if res == "downloaded":
                got.append(d.isoformat())
            elif res == "missing":
                pending.append(d.isoformat())
            d += dt.timedelta(days=1)
    except Exception as exc:  # offline, DNS, TLS, timeout ...
        error = f"{type(exc).__name__}: {exc}"
    have = local_dates()
    return {"downloaded": got, "not_yet_published": pending, "error": error, "latest_local": have[-1].isoformat() if have else None}


def _live_slots(after_day: dt.date | None, max_hours: int = 48) -> list[dt.datetime]:
    """15-minute slots from the first day not covered by a daily file (at most `max_hours` back) up to now (UTC)."""
    now = dt.datetime.now(dt.timezone.utc).replace(tzinfo=None)
    last = now.replace(minute=now.minute // 15 * 15, second=0, microsecond=0)
    first = dt.datetime.combine(after_day + dt.timedelta(days=1), dt.time()) if after_day else last - dt.timedelta(hours=max_hours)
    first = max(first, last - dt.timedelta(hours=max_hours))
    out, t = [], first
    while t <= last:
        out.append(t)
        t += dt.timedelta(minutes=15)
    return out


def _fetch_slot(t: dt.datetime) -> str:
    ts = t.strftime("%Y%m%d%H%M%S")
    target = LIVE_GDELT / f"{ts}.export.CSV"
    if target.exists():
        return "exists"
    r = httpx.get(BASE_V2.format(ts=ts), timeout=60.0, follow_redirects=True)
    if r.status_code == 404:        # not published yet, or a (rare) gap in GDELT's own feed
        return "missing"
    r.raise_for_status()
    with zipfile.ZipFile(io.BytesIO(r.content)) as z:
        member = next(n for n in z.namelist() if n.lower().endswith(".csv"))
        tmp = target.with_suffix(".tmp")
        tmp.write_bytes(z.read(member))
        tmp.replace(target)          # atomic: the loader never sees a half-written file
    return "downloaded"


def prune_live() -> int:
    """Delete 15-minute files for days now covered by an authoritative daily file."""
    have = local_dates()
    if not have:
        return 0
    cutoff = have[-1].strftime("%Y%m%d")
    n = 0
    for p in LIVE_GDELT.glob("*.export.CSV"):
        if p.name[:8] <= cutoff:
            p.unlink()
            n += 1
    return n


def fetch_live(max_hours: int = 48) -> dict:
    """Download missing GDELT 2.0 15-minute files for the days after the latest daily file. Never raises."""
    LIVE_GDELT.mkdir(parents=True, exist_ok=True)
    have = local_dates()
    pruned = prune_live()
    slots = _live_slots(have[-1] if have else None, max_hours)
    todo = [t for t in slots if not (LIVE_GDELT / f"{t.strftime('%Y%m%d%H%M%S')}.export.CSV").exists()]
    got, error = 0, None
    try:
        with ThreadPoolExecutor(max_workers=6) as ex:
            got = sum(1 for r in ex.map(_fetch_slot, todo) if r == "downloaded")
    except Exception as exc:
        error = f"{type(exc).__name__}: {exc}"
    files = sorted(p.name for p in LIVE_GDELT.glob("*.export.CSV"))
    latest = dt.datetime.strptime(files[-1][:14], "%Y%m%d%H%M%S").isoformat() if files else None
    return {"downloaded": got, "files": len(files), "pruned": pruned, "latest_slot_utc": latest, "error": error}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--from", dest="start")
    ap.add_argument("--to", dest="end")
    a = ap.parse_args()

    def parse(s):
        return dt.datetime.strptime(s, "%Y%m%d").date() if s else None

    print(fetch_missing(parse(a.start), parse(a.end)))
    print(fetch_live())
