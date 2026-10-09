"""Sentiment analysis: pluggable backends behind one interface, blended into an explainable ensemble.

Backends
--------
* ``tfidf-lr``  - TF-IDF + logistic regression trained locally on FinancialPhraseBank + financial tweets
                  (+ a slice of general tweets). Always available, trains in seconds, no downloads.
* ``finbert``   - ProsusAI/finbert via Hugging Face Transformers. Optional; used when ``transformers``/``torch``
                  are installed and the weights are available locally or downloadable.
* lexicon       - domain risk lexicon (data/config/event_taxonomy.json) used as an independent signal.
* source prior  - GDELT AvgTone (a pre-computed tone of the underlying article cluster).

The ensemble weights live in data/config/engine_config.json.
"""
from __future__ import annotations

import json
import re
from pathlib import Path

import joblib
import numpy as np
import pandas as pd
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, classification_report, f1_score
from sklearn.model_selection import train_test_split
from sklearn.pipeline import Pipeline

from src.config import NEWS, PROCESSED, SOCIAL, engine_config, taxonomy
from src.risk_engine.ingestion.clean import clean_text

MODEL_DIR = PROCESSED / "models"
MODEL_PATH = MODEL_DIR / "sentiment_tfidf_lr.joblib"
EVAL_PATH = PROCESSED / "sentiment_model_eval.json"
FINBERT_CACHE = MODEL_DIR / "finbert_cache.csv"
_TOKEN = re.compile(r"[a-z']+")


# ----------------------------------------------------------------------------- lexicon
class LexiconSentiment:
    def __init__(self) -> None:
        lex = taxonomy()["risk_lexicon"]
        self.neg = set(lex["negative"])
        self.pos = set(lex["positive"])

    def score(self, text: str) -> float:
        toks = _TOKEN.findall(text.lower())
        n = sum(t in self.neg for t in toks)
        p = sum(t in self.pos for t in toks)
        return float(np.tanh((p - n) / (p + n + 1.5) * 1.6)) if (p + n) else 0.0


# ----------------------------------------------------------------------------- tf-idf LR
def _training_frames() -> tuple[pd.DataFrame, pd.DataFrame]:
    fpb = pd.read_csv(NEWS / "FinancialPhraseBank.csv", header=None, names=["y", "text"], encoding="latin-1").dropna()
    fpb["y"] = fpb["y"].map({"negative": -1, "neutral": 0, "positive": 1})
    tw = pd.read_excel(SOCIAL / "financial-tweets-senti.xlsx").dropna(subset=["tweet"])
    tw = pd.DataFrame({"text": tw["tweet"], "y": tw["sentiment"].map({0: -1, 1: 1, 2: 0})})
    gen = pd.read_csv(SOCIAL / "Twitter_Data.csv").dropna().sample(8000, random_state=7)
    gen = pd.DataFrame({"text": gen["clean_text"], "y": gen["category"].astype(float).astype(int)})
    for d in (fpb, tw, gen):
        d["text"] = d["text"].map(clean_text)
    fpb["src"], tw["src"], gen["src"] = "fpb", "fin_tweets", "twitter_general"
    return pd.concat([fpb, tw], ignore_index=True), gen


def train_tfidf_lr(seed: int = 42) -> dict:
    fin, gen = _training_frames()
    tr, te = train_test_split(fin, test_size=0.2, random_state=seed, stratify=fin["src"] + fin["y"].astype(str))
    train = pd.concat([tr, gen], ignore_index=True)
    pipe = Pipeline([
        ("tfidf", TfidfVectorizer(ngram_range=(1, 2), min_df=2, max_features=200_000, sublinear_tf=True, lowercase=True)),
        ("lr", LogisticRegression(C=4.0, max_iter=400, class_weight="balanced")),
    ])
    pipe.fit(train["text"], train["y"])
    pred = pipe.predict(te["text"])
    report = {
        "backend": "tfidf-lr", "train_rows": int(len(train)), "heldout_rows": int(len(te)),
        "heldout_accuracy": round(accuracy_score(te["y"], pred), 4), "heldout_macro_f1": round(f1_score(te["y"], pred, average="macro"), 4),
        "per_source": {}, "classes": [-1, 0, 1],
    }
    for s, g in te.groupby("src"):
        p = pipe.predict(g["text"])
        report["per_source"][s] = {"n": int(len(g)), "accuracy": round(accuracy_score(g["y"], p), 4), "macro_f1": round(f1_score(g["y"], p, average="macro"), 4)}
    lex = LexiconSentiment()
    lex_pred = te["text"].map(lex.score).map(lambda s: 1 if s > 0.15 else (-1 if s < -0.15 else 0))
    report["lexicon_only_heldout_accuracy"] = round(accuracy_score(te["y"], lex_pred), 4)
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    joblib.dump(pipe, MODEL_PATH)
    EVAL_PATH.write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


def evaluate_backend(backend, n_tweets: int = 1000, seed: int = 42) -> dict:
    """Held-out evaluation (same split as the TF-IDF model) of any backend; merged into sentiment_model_eval.json."""
    fin, _ = _training_frames()
    _, te = train_test_split(fin, test_size=0.2, random_state=seed, stratify=fin["src"] + fin["y"].astype(str))
    te = pd.concat([te[te["src"] == "fpb"], te[te["src"] == "fin_tweets"].sample(n_tweets, random_state=seed)])
    score, _, _ = backend.predict(te["text"].tolist())
    pred = np.where(score > 0.25, 1, np.where(score < -0.25, -1, 0))
    rep = {"heldout_rows": int(len(te)), "heldout_accuracy": round(accuracy_score(te["y"], pred), 4), "heldout_macro_f1": round(f1_score(te["y"], pred, average="macro"), 4),
           "note": "FinBERT is zero-shot here (not fine-tuned); FinancialPhraseBank is the FPB test split + a tweet sample; decision thresholds +-0.25"}
    cur = json.loads(EVAL_PATH.read_text(encoding="utf-8")) if EVAL_PATH.exists() else {}
    cur[backend.name] = rep
    EVAL_PATH.write_text(json.dumps(cur, indent=2), encoding="utf-8")
    return rep


class TfidfSentiment:
    name = "tfidf-lr"

    def __init__(self, retrain: bool = False) -> None:
        if retrain or not MODEL_PATH.exists():
            train_tfidf_lr()
        self.pipe = joblib.load(MODEL_PATH)
        self.classes = list(self.pipe.classes_)

    def predict(self, texts: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Returns (score in [-1,1], confidence in [0,1], argmax label)."""
        proba = self.pipe.predict_proba(texts)
        idx = {c: i for i, c in enumerate(self.classes)}
        score = proba[:, idx[1]] - proba[:, idx[-1]]
        ent = -(proba * np.log(proba + 1e-12)).sum(axis=1) / np.log(3)
        label = np.array(self.classes)[proba.argmax(axis=1)]
        return score, 1 - ent, label


# ----------------------------------------------------------------------------- FinBERT (optional)
class FinBertSentiment:
    name = "finbert"

    def __init__(self, model_id: str = "ProsusAI/finbert", batch: int = 64) -> None:
        import torch  # noqa: WPS433 (optional dependency)
        from transformers import AutoModelForSequenceClassification, AutoTokenizer

        self.torch = torch
        self.tok = AutoTokenizer.from_pretrained(model_id)
        self.model = AutoModelForSequenceClassification.from_pretrained(model_id).eval()
        self.batch = batch
        lab = {int(k): v.lower() for k, v in self.model.config.id2label.items()}
        self.pos_i = next(i for i, v in lab.items() if v == "positive")
        self.neg_i = next(i for i, v in lab.items() if v == "negative")

    def _infer(self, texts: list[str]) -> np.ndarray:
        out = []
        with self.torch.no_grad():
            for i in range(0, len(texts), self.batch):
                enc = self.tok(texts[i:i + self.batch], padding=True, truncation=True, max_length=96, return_tensors="pt")
                out.append(self.torch.softmax(self.model(**enc).logits, dim=-1).numpy())
        return np.concatenate(out) if out else np.zeros((0, 3))

    def predict(self, texts: list[str]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Cached on disk (keyed by text hash) so pipeline re-runs do not repeat ~25 min of CPU inference."""
        import hashlib

        keys = [hashlib.md5(t.encode("utf-8")).hexdigest() for t in texts]
        cache = {}
        if FINBERT_CACHE.exists():
            c = pd.read_csv(FINBERT_CACHE)
            cache = {k: (p, n) for k, p, n in zip(c["key"], c["pos"], c["neg"])}
        todo = sorted({(k, t) for k, t in zip(keys, texts) if k not in cache}, key=lambda kt: len(kt[1]))
        if todo:
            probs = self._infer([t for _, t in todo])
            for (k, _), p in zip(todo, probs):
                cache[k] = (float(p[self.pos_i]), float(p[self.neg_i]))
            FINBERT_CACHE.parent.mkdir(parents=True, exist_ok=True)
            pd.DataFrame([(k, v[0], v[1]) for k, v in cache.items()], columns=["key", "pos", "neg"]).to_csv(FINBERT_CACHE, index=False)
        pos = np.array([cache[k][0] for k in keys])
        neg = np.array([cache[k][1] for k in keys])
        neu = np.clip(1 - pos - neg, 1e-9, 1)
        p = np.stack([pos, neg, neu], axis=1)
        conf = 1 - (-(p * np.log(p + 1e-12)).sum(axis=1) / np.log(3))
        label = np.where(pos >= neg, 1, -1) * ((np.maximum(pos, neg) > neu))
        return pos - neg, conf, label


def load_backend(name: str = "auto"):
    """'auto' prefers FinBERT when importable AND cached locally, else the always-available TF-IDF model."""
    if name in ("finbert", "auto"):
        try:
            return FinBertSentiment()
        except Exception as exc:  # missing deps / weights / no internet
            if name == "finbert":
                raise RuntimeError(f"FinBERT unavailable: {exc}") from exc
    return TfidfSentiment()


# ----------------------------------------------------------------------------- ensemble
def ensemble_scores(texts: list[str], source_prior: np.ndarray, backend) -> pd.DataFrame:
    """Blend model, lexicon and source-prior sentiment. `source_prior` is NaN where no prior exists."""
    w = engine_config()["sentiment"]["ensemble_weights"]
    m_score, m_conf, _ = backend.predict(texts)
    lex = LexiconSentiment()
    l_score = np.array([lex.score(t) for t in texts])
    has_prior = ~np.isnan(source_prior)
    num = w["model"] * m_score + w["lexicon"] * l_score + w["source_prior"] * np.where(has_prior, np.nan_to_num(source_prior), 0.0)
    den = w["model"] + w["lexicon"] + w["source_prior"] * has_prior
    score = np.clip(num / den, -1, 1)
    # confidence: model certainty, boosted when independent components agree on direction
    agree = np.where(has_prior, (np.sign(m_score) == np.sign(np.nan_to_num(source_prior))) & (np.abs(m_score) > 0.05), np.sign(m_score) == np.sign(l_score))
    conf = np.clip(0.35 + 0.45 * m_conf + 0.20 * agree + 0.15 * (np.abs(score) > 0.3), 0.05, 0.99)
    return pd.DataFrame({"model_score": m_score, "lexicon_score": l_score, "source_prior": source_prior, "sentiment": score, "sentiment_confidence": conf})
