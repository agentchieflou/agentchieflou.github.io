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
    log.info("available models: %s", names[:20])
    flash = [n for n in names if "flash" in n
             and not any(x in n for x in ("image", "live", "tts", "audio", "8b"))]
    log.info("flash candidates: %s", flash)
    # Prefer gemini-2.0-flash or gemini-2.0-flash-001 over the alias
    for preferred in ("gemini-2.0-flash", "gemini-2.0-flash-001"):
        if preferred in flash:
            return preferred
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


def _extract_text(data):
    """Extract text from a generateContent response, with diagnostics."""
    # Check for prompt-level block (safety filters, etc.)
    block_reason = data.get("promptFeedback", {}).get("blockReason")
    if block_reason:
        raise RuntimeError(f"Gemini blocked the prompt: {block_reason}")

    candidates = data.get("candidates", [])
    if not candidates:
        raise RuntimeError(
            f"Gemini returned no candidates. Full response: "
            f"{json.dumps(data)[:500]}")

    candidate = candidates[0]
    finish_reason = candidate.get("finishReason", "UNKNOWN")
    # STOP is normal; MAX_TOKENS means truncated; SAFETY means blocked
    if finish_reason not in ("STOP", "MAX_TOKENS"):
        safety_ratings = candidate.get("safetyRatings", [])
        raise RuntimeError(
            f"Gemini finishReason={finish_reason}, "
            f"safetyRatings={json.dumps(safety_ratings)[:300]}")

    try:
        text = candidate["content"]["parts"][0]["text"]
    except (KeyError, IndexError, TypeError) as e:
        raise RuntimeError(
            f"unexpected Gemini response shape: "
            f"{json.dumps(candidate)[:500]}") from e

    if finish_reason == "MAX_TOKENS":
        log.warning("Gemini output was truncated (MAX_TOKENS); response "
                     "length=%d chars", len(text))

    if not text.strip():
        raise RuntimeError("Gemini returned empty text content")

    return text


def generate(prompt, max_tokens=3000, json_response=True):
    global _resolved_model
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    model = _resolved_model or GEMINI_MODEL
    log.info("calling Gemini model=%s, max_tokens=%d, json_mode=%s",
             model, max_tokens, json_response)
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
        text = _extract_text(data)
    except RuntimeError:
        # If JSON mode failed, retry once without it — some models/keys
        # don't support responseMimeType and return empty candidates.
        if json_response:
            log.warning("JSON mode response extraction failed; retrying "
                        "without responseMimeType")
            r2 = _call(_resolved_model or model, prompt, max_tokens,
                        json_response=False)
            if r2.status_code == 200:
                text = _extract_text(r2.json())
            else:
                raise
        else:
            raise
    log.info("Gemini response OK, %d chars", len(text))
    return text
