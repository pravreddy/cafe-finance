"""Shared many times, counted once.

Match order, strongest first (docs/04-data-model.md):

  1. UTR                      — bank-level id, unique across India. Not a heuristic.
  2. app transaction id       — PhonePe/GPay id printed on the receipt.
  3. file hash (sha256)       — the same screenshot forwarded again.
  4. #CF-000123 tag           — our own tag in the WhatsApp caption.
  5. amount + paid_on + comment (exact, case-insensitive)
  6. weak: amount + paid_on ± 1 day + payee — returned as CANDIDATES for a
     human, never auto-merged. Two ₹500 vegetable payments on the same day is
     a normal Tuesday, not a duplicate.

Same philosophy as guhya-pos/finance/matching.py: attach evidence, let a
person confirm; never guess where guessing costs money.
"""
import re
from datetime import timedelta

from .models import Invoice, LedgerEntry, Proof

CF_TAG_RE = re.compile(r"#CF-(\d{6})", re.I)


def find_entry(payment: dict, sha256: str = "", caption: str = ""):
    """-> (entry | None, method)"""
    utr = payment.get("utr")
    if utr:
        e = LedgerEntry.objects.filter(utr=utr).first()
        if e:
            return e, "utr"
    txn = (payment.get("txn_id") or "").strip()
    if txn:
        e = LedgerEntry.objects.filter(txn_id=txn).first()
        if e:
            return e, "txn_id"
    if sha256:
        p = Proof.objects.filter(sha256=sha256, entry__isnull=False).select_related("entry").first()
        if p:
            return p.entry, "sha256"
    m = CF_TAG_RE.search(caption or "")
    if m:
        e = LedgerEntry.objects.filter(cf_tag=f"#CF-{m.group(1)}").first()
        if e:
            return e, "cf_tag"
    # Rule 5 is for receipts whose ids could not be read. A receipt that DOES
    # carry a UTR / txn id and matched nothing above is a different payment,
    # however alike the amount, date and comment look (two ₹10,000 painter
    # installments a day apart with the same note is exactly the trap).
    if utr or txn:
        return None, "new"
    amount, paid_on, msg = payment.get("amount"), payment.get("_paid_on"), (payment.get("message") or "").strip()
    if amount and paid_on and msg:
        e = (LedgerEntry.objects
             .filter(amount=amount, paid_on=paid_on, description__iexact=msg)
             .first())
        if e:
            return e, "amount_date_comment"
    return None, "new"


def weak_candidates(payment: dict, exclude_pk=None):
    """Possible duplicates for a human to look at. Never auto-merged."""
    amount, paid_on = payment.get("amount"), payment.get("_paid_on")
    if not (amount and paid_on):
        return LedgerEntry.objects.none()
    qs = LedgerEntry.objects.filter(
        amount=amount,
        paid_on__range=(paid_on - timedelta(days=1), paid_on + timedelta(days=1)),
    ).exclude(status=LedgerEntry.Status.REJECTED)
    payee = (payment.get("payee_name") or "").strip()
    if payee:
        first = payee.split()[0]
        qs = qs.filter(payee_name__icontains=first)
    if exclude_pk:
        qs = qs.exclude(pk=exclude_pk)
    return qs[:5]


def find_invoice(inv: dict, sha256: str = ""):
    """-> (invoice | None, method)"""
    vendor, number = (inv.get("vendor_name") or "").strip(), (inv.get("invoice_number") or "").strip()
    if vendor and number:
        i = Invoice.objects.filter(vendor_name__iexact=vendor, invoice_number__iexact=number).first()
        if i:
            return i, "vendor_number"
    if sha256:
        p = Proof.objects.filter(sha256=sha256, invoice__isnull=False).select_related("invoice").first()
        if p:
            return p.invoice, "sha256"
    return None, "new"


def suggest_invoices(entry: LedgerEntry):
    """Open invoices this payment might be settling (installments welcome).
    Vendor name overlap first, then 'amount equals what is still owed'."""
    qs = Invoice.objects.filter(status__in=(Invoice.Status.OPEN, Invoice.Status.PART_PAID))
    if entry.cafe_id:
        qs = qs.filter(cafe=entry.cafe) | qs.filter(cafe__isnull=True)
    out, seen = [], set()
    payee_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", entry.payee_name or "")}
    for inv in qs.order_by("-invoice_date")[:50]:
        vendor_words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", inv.vendor_name or "")}
        name_hit = bool(payee_words & vendor_words)
        amt_hit = inv.outstanding is not None and inv.outstanding == entry.amount
        if (name_hit or amt_hit) and inv.pk not in seen:
            seen.add(inv.pk)
            out.append((inv, "vendor" if name_hit else "amount"))
    return out[:5]
