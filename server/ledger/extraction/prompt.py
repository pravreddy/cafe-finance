"""The one prompt every provider uses. Output must be JSON only."""

PROMPT = """You are reading a document for a small cafe's expense ledger in India.
Classify it and extract fields. Reply with JSON only, no prose.

doc_type is one of:
  "payment" - a UPI / bank payment confirmation screenshot (PhonePe, Google Pay, Paytm, BHIM, bank app, NEFT/IMPS)
  "invoice" - a bill or invoice from a vendor (GST invoice PDF, shop bill, handwritten bill photo)
  "other"   - anything else (a menu, a chat screenshot without a payment, a photo)

Schema:
{
  "doc_type": "payment" | "invoice" | "other",
  "confidence": 0.0-1.0,
  "payment": {
    "amount": number,                       // rupees, e.g. 10000 or 1250.50
    "payee_name": string,                   // "Painter Raja"
    "payee_phone_masked": string,           // "+91 ••••• •7882" as shown
    "utr": string,                          // 12-digit bank UTR / RRN if visible
    "txn_id": string,                       // app transaction id, e.g. "T2609151932227961636438"
    "upi_app": "phonepe"|"gpay"|"paytm"|"bhim"|"bank"|"cash"|"other",
    "paid_at": "YYYY-MM-DD HH:MM",          // as printed; date only if no time
    "message": string,                      // the note typed by the payer, e.g. "2nd installment for painting"
    "debited_from_last4": string,           // last digits of the debited account
    "status": "success"|"failed"|"pending"
  },
  "invoice": {
    "vendor_name": string,
    "vendor_gstin": string,
    "invoice_number": string,
    "invoice_date": "YYYY-MM-DD",
    "due_date": "YYYY-MM-DD",
    "subtotal": number,
    "tax": number,
    "total": number,
    "description": string,                  // one line: what was bought
    "line_items": [{"desc": string, "qty": number, "rate": number, "amount": number}]
  }
}
Fill only the object that applies; leave the other as {}. Use null for anything not visible.
Amounts are numbers without currency symbols or commas. A failed/declined payment is still doc_type "payment" with status "failed".
"""
