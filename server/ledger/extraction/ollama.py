"""Local Ollama vision model. Free, private, slow on CPU — opt-in only
(DOC_AI_PROVIDER=ollama). PDFs are rendered to a PNG of page 1 first because
Ollama only takes images."""
import base64

import requests
from django.conf import settings

from . import ExtractionError
from .prompt import PROMPT


def _pdf_first_page_png(raw: bytes) -> bytes:
    try:
        import fitz  # PyMuPDF
    except ImportError:
        raise ExtractionError("PyMuPDF is needed to read PDFs with Ollama (pip install PyMuPDF).")
    doc = fitz.open(stream=raw, filetype="pdf")
    page = doc[0]
    pix = page.get_pixmap(dpi=150)
    return pix.tobytes("png")


def generate(raw: bytes, mime: str, filename: str = "") -> str:
    if mime == "application/pdf":
        raw = _pdf_first_page_png(raw)
    url = settings.OLLAMA_BASE_URL.rstrip("/") + "/api/generate"
    payload = {
        "model": settings.OLLAMA_VISION_MODEL,
        "prompt": PROMPT,
        "images": [base64.b64encode(raw).decode()],
        "stream": False,
        "format": "json",
        "options": {"temperature": 0.1, "num_predict": 2048},
    }
    try:
        resp = requests.post(url, json=payload, timeout=settings.OLLAMA_TIMEOUT)
        resp.raise_for_status()
    except requests.RequestException as e:
        raise ExtractionError(
            f"Couldn't reach Ollama at {settings.OLLAMA_BASE_URL}. Is it running and is "
            f"{settings.OLLAMA_VISION_MODEL} pulled? ({e})"
        )
    return resp.json().get("response", "")
