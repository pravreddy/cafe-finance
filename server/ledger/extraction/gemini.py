"""Gemini generateContent with an inline image or PDF part.
Same call shape as guhya-pos/api/menu_import.py::_gemini_generate."""
import base64

import requests
from django.conf import settings

from . import ExtractionError
from .prompt import PROMPT

GEMINI_API_URL = "https://generativelanguage.googleapis.com/v1beta/models"


def generate(raw: bytes, mime: str, filename: str = "") -> str:
    key = getattr(settings, "GEMINI_API_KEY", "")
    if not key:
        raise ExtractionError("Gemini API key not configured (GEMINI_API_KEY).")
    url = f"{GEMINI_API_URL}/{settings.GEMINI_MODEL}:generateContent?key={key}"
    parts = [
        {"text": PROMPT},
        {"inline_data": {"mime_type": mime, "data": base64.b64encode(raw).decode()}},
    ]
    resp = None
    try:
        resp = requests.post(
            url,
            headers={"Content-Type": "application/json"},
            json={
                "contents": [{"parts": parts}],
                "generationConfig": {
                    "temperature": 0,
                    "maxOutputTokens": 4096,
                    "responseMimeType": "application/json",
                },
            },
            timeout=settings.GEMINI_TIMEOUT,
        )
        resp.raise_for_status()
    except requests.RequestException as e:
        snippet = ""
        if resp is not None:
            try:
                snippet = " " + resp.text[:200]
            except Exception:
                pass
        raise ExtractionError(f"Gemini request failed: {e}.{snippet}")
    data = resp.json()
    try:
        return data["candidates"][0]["content"]["parts"][0]["text"]
    except (KeyError, IndexError):
        raise ExtractionError("Gemini returned nothing readable.")
