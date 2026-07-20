"""Minimal Gemini REST client — no SDK dependency, just `requests`.

Uses Interactions API with response_format so structured calls return raw JSON.
If the configured model 404s, the client lists the models available to this key,
picks the best flash-tier one, and retries once.
"""
import json

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL
from util import log

BASE = "https://generativelanguage.googleapis.com/v1beta"

_resolved_model = None  # set after a successful 404 fallback


def _headers():
    return {
        "x-goog-api-key": GEMINI_API_KEY,
        "Api-Revision": "2026-05-20"
    }


def _discover_model():
    """Best Interactions-capable flash model available to this key."""
    r = requests.get(f"{BASE}/models", headers=_headers(),
                     params={"pageSize": 200}, timeout=30)
    r.raise_for_status()
    names = [m["name"].split("/")[-1] for m in r.json().get("models", [])
             if "generateContent" in m.get("supportedGenerationMethods", [])]
    log.info("available models: %s", names[:20])
    flash = [n for n in names if "flash" in n
             and not any(x in n for x in ("image", "live", "tts", "audio", "8b"))]
    log.info("flash candidates: %s", flash)
    # Prefer cheapest flash-lite variants; fall back to standard flash if absent
    for preferred in ("gemini-3.1-flash-lite", "gemini-3.1-flash-lite-preview",
                      "gemini-3.5-flash", "gemini-3-flash-preview",
                      "gemini-2.5-flash-lite", "gemini-2.5-flash"):
        if preferred in flash:
            return preferred
    # Reverse lexicographic puts newer major versions first
    pool = sorted(flash, reverse=True) or sorted(names, reverse=True)
    if not pool:
        raise RuntimeError("no generateContent-capable models available to this key")
    return pool[0]


def _call(model, prompt, max_tokens, json_response):
    body = {
        "model": model,
        "input": prompt,
        "generation_config": {
            "max_output_tokens": max_tokens
        }
    }
    if model.startswith("gemini-3.5") or model.startswith("gemini-3.1-pro"):
        # Disable/minimize thinking budgets for extraction/scoring tasks
        body["generation_config"]["thinking_level"] = "minimal"
    elif model.startswith("gemini-2.5"):
        # Legacy thinking config
        body["generation_config"]["thinkingConfig"] = {"thinkingBudget": 0}

    if json_response:
        body["response_format"] = {
            "type": "text",
            "mime_type": "application/json"
        }
    return requests.post(f"{BASE}/interactions",
                         headers=_headers(), json=body, timeout=120)


def _extract_text(data):
    """Extract text from an Interactions API response, with diagnostics."""
    status = data.get("status")
    if status == "failed":
        error = data.get("error", {})
        raise RuntimeError(f"Interaction failed: {json.dumps(error)}")

    steps = data.get("steps", [])
    if not steps:
        raise RuntimeError(
            f"Gemini returned no steps. Full response: "
            f"{json.dumps(data)[:500]}")

    # Find the last model_output step
    model_outputs = [s for s in steps if s.get("type") == "model_output"]
    if not model_outputs:
        raise RuntimeError(
            f"No model_output step found. Full response: "
            f"{json.dumps(data)[:500]}")

    last_output = model_outputs[-1]
    contents = last_output.get("content", [])
    if not contents:
        raise RuntimeError(
            f"model_output step has no content. Step details: "
            f"{json.dumps(last_output)[:500]}")

    text_parts = [c["text"] for c in contents if c.get("type") == "text" and "text" in c]
    if not text_parts:
        raise RuntimeError(
            f"No text parts found in model_output. Step details: "
            f"{json.dumps(last_output)[:500]}")

    text = "".join(text_parts)
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
