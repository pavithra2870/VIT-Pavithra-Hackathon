"""Optional local LLM (Ollama) client. Everything here degrades gracefully: the core app never depends on it."""
from __future__ import annotations

import os
import time

import httpx

OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
PREFERRED = ["phi4-mini", "phi4", "qwen2.5", "qwen3", "llama3.2", "llama3.1", "llama3", "phi3", "mistral", "gemma3", "gemma2", "gemma", "qwen"]
NOT_CHAT = ("nuextract", "embed", "bge-", "nomic", "minilm", "rerank", "whisper")   # extraction / embedding models cannot hold a conversation
_cache: dict = {"t": 0.0, "status": None}

SYSTEM_PROMPT = (
    "You are the explanation layer of a financial Risk Intelligence product. You receive a JSON object of facts that were ALREADY computed by deterministic "
    "backend services (risk signals, impact scores, portfolio stress tests, rebalancing weights). Rules: (1) use only numbers present in the JSON - never "
    "compute, estimate or invent financial figures; (2) if the JSON does not contain the answer, say what is missing; (3) be concise (max ~170 words), "
    "plain-English, no hype; (4) mention that stress results come from a simplified prototype model when relevant."
)


def status(force: bool = False) -> dict:
    """Availability check (cached ~10 s). Returns {available, model, models, base_url, reason}."""
    now = time.time()
    if not force and _cache["status"] and now - _cache["t"] < 10:
        return _cache["status"]
    out = {"available": False, "model": None, "models": [], "base_url": OLLAMA_URL, "reason": None}
    try:
        r = httpx.get(f"{OLLAMA_URL}/api/tags", timeout=1.5)
        r.raise_for_status()
        models = [m["name"] for m in r.json().get("models", [])]
        out["models"] = models
        wanted = os.environ.get("OLLAMA_MODEL")
        chat_models = [m for m in models if not any(x in m.lower() for x in NOT_CHAT)]
        chosen = wanted if wanted in models else next((m for p in PREFERRED for m in chat_models if m.lower().startswith(p)), chat_models[0] if chat_models else None)
        out["model"] = chosen
        out["available"] = chosen is not None
        if not chosen:
            out["reason"] = "Ollama is running but no chat model is installed (try `ollama pull phi4-mini`)"
    except Exception as exc:
        out["reason"] = f"Ollama not reachable at {OLLAMA_URL} ({type(exc).__name__})"
    _cache.update(t=now, status=out)
    return out


def chat(question: str, facts_json: str, model: str, timeout: float = 90.0) -> str:
    payload = {
        "model": model, "stream": False, "options": {"temperature": 0.2, "num_predict": 320},
        "messages": [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": f"FACTS (JSON):\n{facts_json}\n\nQUESTION: {question}"}],
    }
    r = httpx.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=timeout)
    r.raise_for_status()
    return r.json()["message"]["content"].strip()
