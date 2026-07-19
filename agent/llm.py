"""Minimal Gemini REST client — no SDK dependency, just `requests`.

Uses generateContent with responseMimeType=application/json so structured
calls return raw JSON. If the configured model 404s (Google periodically
retires/renames models), the client lists the models available to this key,
picks the best flash-tier one, and retries once — so the unattended daily
pipeline survives model deprecations without a code change.
"""
import json

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL
from util import log

BASE = "https://generativelanguage.googleapis.com/v1beta"

_resolved_model = None  # set after a successful 404 fallback


def _headers():
    return {"x-goog-api-key": GEMINI_API_KEY}


def _discover_model():
    """Best generateContent-capable flash model available to this key."""
    r = requests.get(f"{BASE}/models", headers=_headers(),
                     params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    flash = [n for n in names if "flash" in n
             and not any(x in n for x in ("image", "live", "tts", "audio", "8b"))]
    if "gemini-flash-latest" in flash:
        return "gemini-flash-latest"
    # Reverse lexicographic puts newer major versions first (3 > 2.5)
    pool = sorted(flash, reverse=True) or sorted(names, reverse=True)
    if not pool:
        raise RuntimeError("no generateContent-capable models available to this key")
    return pool[0]


def _call(model, prompt, max_tokens, json_response):
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens},
    }
    if model.startswith("gemini-2.5"):
        # Extraction tasks don't need thinking and it spends output tokens;
        # only 2.5-series models accept an explicit budget of 0.
        body["generationConfig"]["thinkingConfig"] = {"thinkingBudget": 0}
    if json_response:
        body["generationConfig"]["responseMimeType"] = "application/json"
    return requests.post(f"{BASE}/models/{model}:generateContent",
                         headers=_headers(), json=body, timeout=120)


def generate(prompt, max_tokens=3000, json_response=True):
    global _resolved_model
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = _resolved_model or GEMINI_MODEL
    r = _call(model, prompt, max_tokens, json_response)
    if r.status_code == 404 and _resolved_model is None:
        fallback = _discover_model()
        log.warning("model %s not available (404); falling back to %s", model, fallback)
        _resolved_model = fallback
        r = _call(fallback, prompt, max_tokens, json_response)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini API {r.status_code}: {' '.join(r.text.split())[:300]}")
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"unexpected Gemini response shape: {json.dumps(data)[:300]}") from e
