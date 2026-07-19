"""Minimal Gemini REST client — no SDK dependency, just `requests`.

Uses generateContent with responseMimeType=application/json so structured
calls return raw JSON, and disables thinking (not useful for extraction
tasks, and it spends output tokens).
"""
import json

import requests

from config import GEMINI_API_KEY, GEMINI_MODEL

API_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"


def generate(prompt, max_tokens=3000, json_response=True):
    if not GEMINI_API_KEY:
        raise RuntimeError("GEMINI_API_KEY not set")
    body = {
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {
            "maxOutputTokens": max_tokens,
            "thinkingConfig": {"thinkingBudget": 0},
        },
    }
    if json_response:
        body["generationConfig"]["responseMimeType"] = "application/json"
    r = requests.post(API_URL.format(model=GEMINI_MODEL),
                      headers={"x-goog-api-key": GEMINI_API_KEY},
                      json=body, timeout=120)
    if r.status_code != 200:
        raise RuntimeError(f"Gemini API {r.status_code}: {r.text[:300]}")
    data = r.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError) as e:
        raise RuntimeError(
            f"unexpected Gemini response shape: {json.dumps(data)[:300]}") from e
