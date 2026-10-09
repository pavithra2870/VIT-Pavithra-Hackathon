"""Data refresh: fetch new GDELT data AND live market data (yfinance), re-run the Risk Engine when news changed (or just the market tables when only prices moved), rebuild SQLite, reload caches.

Runs in a background thread so the API is immediately usable with the data already on disk.
Disable with GDELT_AUTOFETCH=0 (everything) or MARKET_LIVE=0 (yfinance only). Any failure (offline, download, pipeline) leaves the existing data untouched.
"""
from __future__ import annotations

import os
import threading
import time

from src.backend.services import store

_state = {"status": "idle", "message": "No refresh run yet", "downloaded": [], "last_checked": None, "error": None}
_lock = threading.Lock()


def state() -> dict:
    from src.data_prep.fetch_gdelt import local_dates

    d = local_dates()
    return {**_state, "latest_gdelt": d[-1].isoformat() if d else None, "autofetch": os.environ.get("GDELT_AUTOFETCH", "1") != "0"}


def _gdelt_step() -> dict:
    """Daily files, 15-minute files and GKG enrichment. Never raises; returns what changed."""
    from src.data_prep.fetch_gdelt import fetch_live, fetch_missing
    from src.data_prep.fetch_gkg import fetch_gkg

    out = {"new": False, "what": [], "error": None, "latest": None}
    try:
        res = fetch_missing()
        _state.update(downloaded=res["downloaded"])
        live = fetch_live()        # 15-minute files for the day(s) the daily export does not cover yet
        _state.update(live_latest_utc=live["latest_slot_utc"], live_files=live["files"])
        themes = fetch_gkg()       # GKG themes/organisations for every event file held (optional enrichment)
        new_themes = themes["daily"] + themes["live"]
        out["error"] = res["error"] or live["error"] or themes["error"]
        out["latest"] = live["latest_slot_utc"] or res["latest_local"]
        out["new"] = bool(res["downloaded"] or live["downloaded"] or live["pruned"] or new_themes)
        out["what"] = res["downloaded"] + ([f"{live['downloaded']} new 15-min files"] if live["downloaded"] else []) + (["GKG themes"] if new_themes else [])
    except Exception as exc:
        out["error"] = f"{type(exc).__name__}: {exc}"
    return out


def _market_step() -> dict:
    """Live prices / caps / Yahoo headlines (yfinance). Optional: MARKET_LIVE=0 disables. Never raises."""
    if os.environ.get("MARKET_LIVE", "1") == "0":
        return {"changed": False, "news_new": 0, "error": None, "disabled": True}
    try:
        from src.data_prep.fetch_market import fetch_market

        return fetch_market()
    except Exception as exc:
        return {"changed": False, "news_new": 0, "error": f"{type(exc).__name__}: {exc}"}


def _run() -> None:
    try:
        from src.backend.db.build import build_database
        from src.risk_engine import pipeline

        _state.update(status="fetching", message="Checking GDELT and market data for updates...", error=None)
        g = _gdelt_step()
        m = _market_step()
        _state.update(last_checked=time.strftime("%Y-%m-%d %H:%M:%S"), market_as_of=store.quote_time(), market_live=bool(store.quotes()),
                      error=g["error"] or m.get("error"))
        news_changed = g["new"] or bool(m.get("news_new"))
        if news_changed:        # new text -> full Risk Engine pass (cached FinBERT scores: only new documents cost time)
            parts = g["what"] + ([f"{m['news_new']} Yahoo headlines"] if m.get("news_new") else [])
            _state.update(status="processing", message=f"Scoring new data ({', '.join(parts) or 'cleanup'}) through the Risk Engine...")
            pipeline.run("auto")
            build_database()
        elif m.get("changed"):  # only prices moved -> cheap refresh of the market tables
            _state.update(status="processing", message="Updating market data...")
            pipeline.refresh_market_context()
            build_database()
        store.reload()
        _state.update(market_as_of=store.quote_time(), market_live=bool(store.quotes()))
        tag = f"GDELT through {g['latest']}" if g["latest"] else "GDELT data"
        mk = f" · markets {store.quote_time()} UTC" if store.quotes() else ""
        if g["error"] and not g["new"]:
            _state.update(status="idle", message=f"GDELT unreachable - using {tag}{mk}")
        else:
            _state.update(status="idle", message=("Updated - " if (news_changed or m.get("changed")) else "Up to date - ") + tag + mk)
    except Exception as exc:
        _state.update(status="idle", message="Refresh failed - existing data still in use", error=f"{type(exc).__name__}: {exc}")


def start(force: bool = False) -> bool:
    if not force and os.environ.get("GDELT_AUTOFETCH", "1") == "0":
        _state.update(message="Auto-fetch disabled (GDELT_AUTOFETCH=0)")
        return False
    if not _lock.acquire(blocking=False):
        return False

    def worker():
        try:
            _run()
        finally:
            _lock.release()

    threading.Thread(target=worker, daemon=True, name="data-refresh").start()
    return True


def _loop(minutes: float) -> None:
    while True:
        time.sleep(minutes * 60)
        start()          # no-op if a refresh is still running; cheap (one HTTP 404) when nothing new is published


def start_scheduler() -> None:
    """Re-check GDELT every GDELT_REFRESH_MINUTES (default 15; 0 = only at startup) for as long as the server runs."""
    try:
        minutes = float(os.environ.get("GDELT_REFRESH_MINUTES", "15"))
    except ValueError:
        minutes = 15.0
    _state["refresh_every_minutes"] = minutes
    if minutes > 0 and os.environ.get("GDELT_AUTOFETCH", "1") != "0":
        threading.Thread(target=_loop, args=(minutes,), daemon=True, name="data-refresh-scheduler").start()
