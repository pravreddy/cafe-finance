"""Mock provider for tests and offline dev (DOC_AI_PROVIDER=mock).

Looks at the filename to decide what to pretend it saw:
  *invoice*  -> an invoice
  *other*    -> not a money document
  anything else -> the painter's ₹10,000 PhonePe receipt from the design docs,
                   with the UTR taken from the filename if it has 12 digits in it
                   (so tests can make distinct payments: "pay_083414546430.jpg").
"""
import json
import re


def generate(raw: bytes, mime: str, filename: str = "") -> str:
    name = (filename or "").lower()
    if "invoice" in name:
        return json.dumps({
            "doc_type": "invoice",
            "confidence": 0.9,
            "invoice": {
                "vendor_name": "Sri Balaji Paints",
                "vendor_gstin": "29ABCDE1234F1Z5",
                "invoice_number": "SBP/2026/0912",
                "invoice_date": "2026-09-10",
                "due_date": "2026-09-30",
                "subtotal": 25423.73,
                "tax": 4576.27,
                "total": 30000,
                "description": "Interior painting — 2 floors, Asian Paints emulsion",
                "line_items": [{"desc": "Painting labour + material", "qty": 1, "rate": 25423.73, "amount": 25423.73}],
            },
            "payment": {},
        })
    if "other" in name:
        return json.dumps({"doc_type": "other", "confidence": 0.8, "payment": {}, "invoice": {}})
    m = re.search(r"(\d{12})", name)
    utr = m.group(1) if m else "083414546430"
    amt_m = re.search(r"amt(\d+)", name)
    amount = int(amt_m.group(1)) if amt_m else 10000
    return json.dumps({
        "doc_type": "payment",
        "confidence": 0.95,
        "payment": {
            "amount": amount,
            "payee_name": "Painter Raja",
            "payee_phone_masked": "+91 ••••• •7882",
            "utr": utr,
            "txn_id": f"T2609{utr}7961636438",   # distinct per UTR, like real receipts
            "upi_app": "phonepe",
            "paid_at": "2026-09-14 15:02",
            "message": "2nd installment for painting",
            "debited_from_last4": "96",
            "status": "success",
        },
        "invoice": {},
    })
