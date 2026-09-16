"""Document AI — turn a payment screenshot or an invoice into structured JSON.

Provider pattern copied from guhya-pos/api/menu_import.py: Gemini in the cloud
(fast, paise per document), Ollama locally (free, slow on CPU, explicit opt-in),
and a mock for tests and offline dev.

    from ledger.extraction import extract
    data = extract(raw_bytes, mime="image/jpeg", filename="receipt.jpg")

Returns a dict shaped like PROMPT's schema (see prompt.py). Never raises for a
bad document — returns {"doc_type": "other", ...}. Raises ExtractionError only
when the provider itself cannot be reached.
"""
import json
import re

from django.conf import settings


class ExtractionError(Exception):
    pass


def provider_name():
    p = (getattr(settings, "DOC_AI_PROVIDER", "auto") or "auto").lower()
    if p in ("gemini", "ollama", "mock"):
        return p
    return "gemini" if getattr(settings, "GEMINI_API_KEY", "") else "ollama"


def extract(raw: bytes, mime: str = "", filename: str = "") -> dict:
    name = provider_name()
    if name == "mock":
        from . import mock as impl
    elif name == "gemini":
        from . import gemini as impl
    else:
        from . import ollama as impl
    text = impl.generate(raw, mime or _sniff_mime(raw, filename), filename)
    return normalise(parse_json(text))


# ------------------------------------------------------------------ helpers

def _sniff_mime(raw: bytes, filename: str) -> str:
    if raw[:4] == b"%PDF":
        return "application/pdf"
    if raw[:3] == b"\xff\xd8\xff":
        return "image/jpeg"
    if raw[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if raw[:4] == b"RIFF" and raw[8:12] == b"WEBP":
        return "image/webp"
    if filename.lower().endswith(".pdf"):
        return "application/pdf"
    return "image/jpeg"


def parse_json(text: str) -> dict:
    """Models wrap JSON in prose or ``` fences; dig it out."""
    if not text:
        return {}
    text = text.strip()
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.S)
    if m:
        text = m.group(1)
    else:
        start, end = text.find("{"), text.rfind("}")
        if start >= 0 and end > start:
            text = text[start:end + 1]
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


_AMOUNT_RE = re.compile(r"[^\d.]")


def _dec(v):
    """'₹10,000.00' -> '10000.00' (string, Decimal-safe). None if unreadable."""
    if v is None or v == "":
        return None
    if isinstance(v, (int, float)):
        return str(v)
    s = _AMOUNT_RE.sub("", str(v))
    if s.count(".") > 1:
        s = s.replace(".", "", s.count(".") - 1)
    return s or None


def _digits(v):
    return re.sub(r"\D", "", str(v or ""))


def normalise(d: dict) -> dict:
    """Coerce whatever the model returned into one predictable shape."""
    out = {
        "doc_type": (d.get("doc_type") or "other").lower(),
        "confidence": float(d.get("confidence") or 0),
        "payment": {},
        "invoice": {},
        "raw": d,
    }
    if out["doc_type"] not in ("payment", "invoice", "other"):
        out["doc_type"] = "other"

    p = d.get("payment") or {}
    if p:
        out["payment"] = {
            "amount": _dec(p.get("amount")),
            "payee_name": (p.get("payee_name") or "").strip()[:120],
            "payee_phone_masked": (p.get("payee_phone_masked") or "").strip()[:30],
            "utr": _digits(p.get("utr"))[:40] or None,
            "txn_id": (p.get("txn_id") or "").strip()[:60],
            "upi_app": (p.get("upi_app") or "other").lower(),
            "paid_at": (p.get("paid_at") or "").strip(),
            "message": (p.get("message") or "").strip()[:300],
            "debited_from_last4": _digits(p.get("debited_from_last4"))[-4:],
            "status": (p.get("status") or "success").lower(),
        }
    inv = d.get("invoice") or {}
    if inv:
        out["invoice"] = {
            "vendor_name": (inv.get("vendor_name") or "").strip()[:120],
            "vendor_gstin": (inv.get("vendor_gstin") or "").strip()[:20],
            "invoice_number": (inv.get("invoice_number") or "").strip()[:60],
            "invoice_date": (inv.get("invoice_date") or "").strip(),
            "due_date": (inv.get("due_date") or "").strip(),
            "subtotal": _dec(inv.get("subtotal")),
            "tax": _dec(inv.get("tax")),
            "total": _dec(inv.get("total")),
            "description": (inv.get("description") or "").strip()[:300],
            "line_items": inv.get("line_items") or [],
        }
    return out
