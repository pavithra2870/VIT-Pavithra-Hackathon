"""Cleaning and de-duplication shared by every text source."""
from __future__ import annotations

import hashlib
import html
import re
import unicodedata
from urllib.parse import urlparse

import pandas as pd

_URL = re.compile(r"https?://\S+|www\.\S+")
_WS = re.compile(r"\s+")
_USER = re.compile(r"@\w+")
_SLUG_ID = re.compile(r"^(?:[0-9a-f]{6,}|\d+|[A-Za-z]\d{3,}|[0-9a-f-]{20,})$")


def clean_text(text: str) -> str:
    """Strip html, urls, user handles and collapse whitespace; keep casing (needed for entity matching)."""
    if not isinstance(text, str):
        return ""
    t = html.unescape(text)
    t = unicodedata.normalize("NFKC", t)
    t = _URL.sub(" ", t)
    t = _USER.sub(" ", t)
    t = t.replace("’", "'").replace("“", '"').replace("”", '"')
    return _WS.sub(" ", t).strip()


def domain_of(url: str) -> str:
    try:
        host = urlparse(url).netloc.lower()
    except Exception:
        return ""
    return host[4:] if host.startswith("www.") else host


def slug_to_headline(url: str) -> str:
    """Recover a human-readable headline from a news URL slug (GDELT exports carry no article text)."""
    try:
        path = urlparse(url).path
    except Exception:
        return ""
    best = ""
    for seg in path.split("/"):
        seg = re.sub(r"\.(html?|php|aspx?|cms|ece)$", "", seg)
        words = [w for w in re.split(r"[-_+]", seg) if w]
        words = [w for w in words if not _SLUG_ID.match(w)]
        alpha = [w for w in words if re.match(r"^[A-Za-z']+$", w)]
        if len(alpha) >= 4 and len(alpha) >= 0.7 * len(words) and len(" ".join(alpha)) > len(best):
            best = " ".join(alpha)
    # many publishers cut URL slugs at a fixed length, leaving a dangling fragment ("...war escalates c"): drop a trailing
    # 1-2 letter token (never a real last word of a headline) so titles do not end mid-word
    words = best.split()
    while len(words) > 4 and len(words[-1]) <= 2 and words[-1].lower() not in {"us", "uk", "un", "eu", "ai"}:
        words.pop()
    best = " ".join(words)
    return best[:1].upper() + best[1:] if best else ""


def fingerprint(text: str) -> str:
    norm = re.sub(r"[^a-z0-9]+", " ", text.lower()).strip()
    return hashlib.md5(norm.encode("utf-8")).hexdigest()


def deduplicate(df: pd.DataFrame, text_col: str = "text") -> pd.DataFrame:
    """Drop exact/near-exact duplicates (normalised-text fingerprint); keeps first occurrence."""
    fp = df[text_col].map(fingerprint)
    return df.loc[~fp.duplicated()].reset_index(drop=True)
