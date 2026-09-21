"""The one door every proof comes through, whatever channel it arrived by.

This is NOT request/response. Nobody is waiting for the AI:

    intake()        — store the file (dedup by hash), write a ShareEvent, queue it.
                      Milliseconds. This is all the phone / reconciler / mail see.
    process_proof() — the worker reads the file with the document AI (Ollama on
                      the box, however long it takes), decides payment / invoice,
                      finds an existing row or creates a new one, and links every
                      ShareEvent that was waiting on this proof.

`ingest()` = intake + process, kept for tests and DOC_AI_SYNC=1 laptop runs.
The worker is `manage.py process_queue`. Nothing here confirms anything —
that is a human's job in the review queue.
"""
import hashlib
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation

from django.conf import settings
from django.core.files.base import ContentFile
from django.db import transaction
from django.utils import timezone

from . import dedup
from .extraction import ExtractionError, extract
from .models import Cafe, Invoice, LedgerEntry, PayrollPeriod, Period, Person, Proof, ShareEvent

log = logging.getLogger(__name__)


@dataclass
class IngestResult:
    proof: Proof
    share: ShareEvent | None = None
    entry: LedgerEntry | None = None
    invoice: Invoice | None = None
    duplicate: bool = False
    match_method: str = "pending"
    doc_type: str = "pending"
    extraction_error: str = ""
    candidates: list = field(default_factory=list)

    @property
    def pending(self):
        return self.proof.state in (Proof.State.QUEUED, Proof.State.PROCESSING)

    @property
    def message(self):
        if self.pending:
            return "Saved. It is being read in the background — it will appear in your entries shortly."
        if self.extraction_error:
            return f"Saved the file, but could not read it: {self.extraction_error}"
        if self.doc_type == "payment" and self.entry:
            if self.duplicate:
                first = self.entry.shares.order_by("shared_at").first()
                who = (first.shared_by or first.shared_by_label or "someone") if first else "someone"
                when = f"{first.shared_at:%d %b}" if first else "earlier"
                return f"Already recorded on {when} by {who} — added your share to the trail."
            return "New payment recorded — please check the details."
        if self.doc_type == "invoice" and self.invoice:
            return ("Invoice already on file — added your share to the trail."
                    if self.duplicate else "New invoice recorded — please check the details.")
        return "Saved, but this doesn't look like a payment or an invoice. You can fix that below."


# ------------------------------------------------------------------ parsing

def _to_decimal(v):
    try:
        return Decimal(str(v)).quantize(Decimal("0.01")) if v not in (None, "") else None
    except (InvalidOperation, ValueError):
        return None


def parse_when(s: str):
    """'2026-09-14 15:02' | '2026-09-14' | '14/09/2026' -> (date, aware datetime|None)"""
    s = (s or "").strip()
    if not s:
        return None, None
    tz = timezone.get_current_timezone()
    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M", "%d/%m/%Y %H:%M",
                "%d-%m-%Y %H:%M", "%d %b %Y %H:%M", "%d %b %Y, %I:%M %p"):
        try:
            dt = datetime.strptime(s, fmt)
            return dt.date(), timezone.make_aware(dt, tz)
        except ValueError:
            pass
    for fmt in ("%Y-%m-%d", "%d/%m/%Y", "%d-%m-%Y", "%d %b %Y", "%d %B %Y"):
        try:
            return datetime.strptime(s, fmt).date(), None
        except ValueError:
            pass
    return None, None


# ------------------------------------------------------------------ intake (fast)

def store_proof(raw: bytes, filename: str, mime: str, origin: str, origin_ref: str = ""):
    """-> (proof, created). Same bytes = same Proof, whatever the filename."""
    sha = hashlib.sha256(raw).hexdigest()
    existing = Proof.objects.filter(sha256=sha).first()
    if existing:
        return existing, False
    proof = Proof(sha256=sha, original_name=(filename or "proof")[:200], mime=mime or "",
                  origin=origin, origin_ref=origin_ref[:200], state=Proof.State.QUEUED)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in (filename or "proof"))[-60:]
    proof.file.save(safe_name, ContentFile(raw), save=False)
    proof.save()
    return proof, True


@transaction.atomic
def intake(raw: bytes, filename: str, mime: str, *, origin: str, channel: str,
           shared_by: Person | None = None, shared_by_label: str = "", note: str = "",
           shared_at=None, cafe: Cafe | None = None, origin_ref: str = "",
           imported: bool = False) -> IngestResult:
    """Store + queue. Returns immediately; the worker does the reading."""
    proof, created = store_proof(raw, filename, mime, origin, origin_ref)
    shared_at = shared_at or timezone.now()
    if cafe is None and shared_by and shared_by.default_cafe_id:
        cafe = shared_by.default_cafe
    ev = ShareEvent.objects.create(
        proof=proof, shared_by=shared_by, shared_by_label=shared_by_label[:120],
        shared_at=shared_at, channel=channel, note=(note or "")[:300],
        match_method="pending",
        context={"cafe_id": cafe.pk if cafe else None, "imported": bool(imported)},
    )
    result = IngestResult(proof=proof, share=ev)
    if proof.state == Proof.State.DONE:
        # Already read earlier (same bytes shared again): link now, no AI needed.
        _link_share(proof, ev, result)
    return result


# ------------------------------------------------------------------ worker (slow)

def run_extraction(proof: Proof, raw: bytes | None = None) -> dict:
    if raw is None:
        proof.file.open("rb")
        try:
            raw = proof.file.read()
        finally:
            proof.file.close()
    data = extract(raw, proof.mime, proof.original_name)   # may raise ExtractionError
    proof.extracted_json = data
    proof.extracted_at = timezone.now()
    proof.extraction_error = ""
    proof.kind = {
        "payment": Proof.Kind.PAYMENT_SCREENSHOT,
        "invoice": Proof.Kind.INVOICE_PDF if proof.is_pdf else Proof.Kind.INVOICE_IMAGE,
    }.get(data.get("doc_type"), Proof.Kind.OTHER)
    proof.save(update_fields=["extracted_json", "extracted_at", "extraction_error", "kind"])
    return data


def process_proof(proof: Proof) -> IngestResult:
    """Read one queued proof and link its waiting shares. Safe to call again."""
    result = IngestResult(proof=proof)
    Proof.objects.filter(pk=proof.pk).update(state=Proof.State.PROCESSING, attempts=proof.attempts + 1)
    proof.attempts += 1
    try:
        run_extraction(proof)
    except ExtractionError as e:
        log.warning("extraction failed for proof %s (attempt %s): %s", proof.pk, proof.attempts, e)
        proof.extraction_error = str(e)[:300]
        if proof.attempts >= settings.DOC_AI_MAX_ATTEMPTS:
            proof.state = Proof.State.FAILED
            proof.extracted_json = {"doc_type": "other", "confidence": 0, "payment": {}, "invoice": {}}
            proof.kind = Proof.Kind.OTHER
        else:
            proof.state = Proof.State.QUEUED
            proof.next_attempt_at = timezone.now() + timedelta(minutes=2 * proof.attempts)
        proof.save(update_fields=["state", "extraction_error", "next_attempt_at", "extracted_json", "kind"])
        result.extraction_error = proof.extraction_error
        if proof.state != Proof.State.FAILED:
            return result
    else:
        proof.state = Proof.State.DONE
        proof.save(update_fields=["state"])

    with transaction.atomic():
        for ev in proof.shares.filter(match_method="pending").order_by("shared_at", "pk"):
            r = IngestResult(proof=proof, share=ev)
            _link_share(proof, ev, r)
            if result.share is None or result.share.pk == ev.pk:
                result = r
        # the reconciler's GroupPost rows point at the proof; tell them the outcome
        from .models import GroupPost
        GroupPost.objects.filter(proof=proof).update(
            matched_entry=proof.entry, matched_invoice=proof.invoice,
            match_method=result.match_method or "")
    result.proof = proof
    return result


_SOURCE_FOR_CHANNEL = {
    ShareEvent.Channel.APP_SHARE: LedgerEntry.Source.APP,
    ShareEvent.Channel.WHATSAPP_FORWARD: LedgerEntry.Source.WHATSAPP,
    ShareEvent.Channel.WHATSAPP_GROUP: LedgerEntry.Source.WHATSAPP,
    ShareEvent.Channel.EMAIL: LedgerEntry.Source.EMAIL,
    ShareEvent.Channel.MANUAL: LedgerEntry.Source.MANUAL,
}


def _link_share(proof: Proof, ev: ShareEvent, result: IngestResult):
    """Given an already-read proof, attach this ShareEvent to the right row
    (existing or new). Pure DB work — never calls the AI."""
    data = proof.extracted_json or {}
    ctx = ev.context or {}
    cafe = Cafe.objects.filter(pk=ctx.get("cafe_id")).first() if ctx.get("cafe_id") else None
    shared_by, shared_at, note = ev.shared_by, ev.shared_at, ev.note
    result.doc_type = data.get("doc_type", "other")
    result.extraction_error = proof.extraction_error
    result.share = ev

    if result.doc_type == "payment" and data.get("payment"):
        p = dict(data["payment"])
        p["amount"] = _to_decimal(p.get("amount"))
        p["_paid_on"], p["_paid_at"] = parse_when(p.get("paid_at"))
        entry, method = dedup.find_entry(p, proof.sha256, note)
        if entry is None and p["amount"]:
            entry = LedgerEntry.objects.create(
                cafe=cafe,
                paid_on=p["_paid_on"] or shared_at.date(),
                paid_at=p["_paid_at"],
                amount=p["amount"],
                paid_by=shared_by if shared_by and shared_by.role != Person.Role.OTHER else None,
                # whose money: default to the payer's own; the review screen can change it
                funded_by=shared_by if shared_by and shared_by.role == Person.Role.PARTNER else None,
                payee_name=p.get("payee_name") or "",
                payee_phone_masked=p.get("payee_phone_masked") or "",
                description=p.get("message") or "",
                source=_SOURCE_FOR_CHANNEL.get(ev.channel, LedgerEntry.Source.APP),
                status=LedgerEntry.Status.IMPORTED if ctx.get("imported") else LedgerEntry.Status.PENDING,
                utr=p.get("utr"),
                txn_id=p.get("txn_id") or "",
                upi_app=p.get("upi_app") if p.get("upi_app") in LedgerEntry.UpiApp.values else LedgerEntry.UpiApp.OTHER,
                debited_from_last4=p.get("debited_from_last4") or "",
                confidence=float(data.get("confidence") or 0),
                created_by=shared_by,
            )
            method = "new"
            result.candidates = list(dedup.weak_candidates(p, exclude_pk=entry.pk))
        if entry is not None:
            result.entry, result.match_method = entry, method
            result.duplicate = method != "new"
            ev.entry, ev.was_duplicate, ev.match_method = entry, result.duplicate, method
            if proof.entry_id is None:
                proof.entry = entry
                proof.save(update_fields=["entry"])
        else:
            ev.match_method = "none"

    elif result.doc_type == "invoice" and data.get("invoice"):
        inv = data["invoice"]
        invoice, method = dedup.find_invoice(inv, proof.sha256)
        if invoice is None:
            inv_date, _ = parse_when(inv.get("invoice_date"))
            due, _ = parse_when(inv.get("due_date"))
            invoice = Invoice.objects.create(
                cafe=cafe,
                vendor_name=inv.get("vendor_name") or "",
                vendor_gstin=inv.get("vendor_gstin") or "",
                invoice_number=inv.get("invoice_number") or "",
                invoice_date=inv_date,
                due_date=due,
                subtotal=_to_decimal(inv.get("subtotal")),
                tax=_to_decimal(inv.get("tax")),
                total=_to_decimal(inv.get("total")),
                description=inv.get("description") or "",
                extracted_json=inv,
                created_by=shared_by,
            )
            method = "new"
        result.invoice, result.match_method = invoice, method
        result.duplicate = method != "new"
        ev.invoice, ev.was_duplicate, ev.match_method = invoice, result.duplicate, method
        if proof.invoice_id is None:
            proof.invoice = invoice
            proof.save(update_fields=["invoice"])
    else:
        ev.match_method = "none"
        result.match_method = "none"

    ev.save(update_fields=["entry", "invoice", "was_duplicate", "match_method"])
    return result


def ingest(raw: bytes, filename: str, mime: str, **kw) -> IngestResult:
    """intake + process in one go. Used by tests and DOC_AI_SYNC=1."""
    r = intake(raw, filename, mime, **kw)
    if r.pending:
        pr = process_proof(r.proof)
        # report the outcome for THIS share, not the first waiting one
        ev = ShareEvent.objects.get(pk=r.share.pk)
        pr.share, pr.entry, pr.invoice = ev, ev.entry, ev.invoice
        pr.duplicate, pr.match_method = ev.was_duplicate, ev.match_method
        return pr
    return r


def intake_or_ingest(raw, filename, mime, **kw) -> IngestResult:
    """What the views call: inline when DOC_AI_SYNC=1, queued otherwise."""
    return ingest(raw, filename, mime, **kw) if settings.DOC_AI_SYNC else intake(raw, filename, mime, **kw)


def claim_next() -> Proof | None:
    """Pick one queued proof for this worker; others skip it (select_for_update
    with skip_locked on Postgres; plain on sqlite)."""
    now = timezone.now()
    with transaction.atomic():
        qs = (Proof.objects.filter(state=Proof.State.QUEUED, next_attempt_at__lte=now)
              .order_by("next_attempt_at", "pk"))
        if "postgresql" in settings.DATABASES["default"]["ENGINE"]:
            qs = qs.select_for_update(skip_locked=True)
        proof = qs.first()
        if proof is None:
            return None
        proof.state = Proof.State.PROCESSING
        proof.save(update_fields=["state"])
        return proof


# ------------------------------------------------------------------ review

class PeriodClosed(Exception):
    """The month this would land in is closed. Book it late instead."""


def confirm_entry(entry: LedgerEntry, by: Person, *, late_ok: bool = False):
    """Confirm = it counts. Refused when the paid_on month is closed for that
    cafe, unless late_ok: then it is booked TODAY and tagged late_for that
    month (9.8). Confidence below 0.7 must have been retyped (views enforce)."""
    if not by.can_confirm:
        raise PermissionError("not a confirmer")
    if entry.status == LedgerEntry.Status.CONFIRMED:
        return entry
    if Period.is_month_closed(entry.cafe, entry.paid_on):
        if not late_ok:
            raise PeriodClosed(f"{entry.paid_on:%B %Y} is closed for {entry.cafe}")
        entry.late_for = Period.objects.get(cafe=entry.cafe, month=Period.month_of(entry.paid_on))
        entry.booked_on = timezone.localdate()
        if Period.is_month_closed(entry.cafe, entry.booked_on):
            raise PeriodClosed("the current month is closed too")
        entry.save(update_fields=["late_for", "booked_on"])
    entry.confirm(by)
    return entry


def reverse_entry(entry: LedgerEntry, by: Person, reason: str) -> LedgerEntry:
    """Never edit confirmed money. Add an equal-and-opposite row, booked today
    (so a closed month stays closed; the reversal shows in the current one)."""
    today = timezone.localdate()
    if Period.is_month_closed(entry.cafe, today):
        raise PeriodClosed("the current month is closed")
    rev = LedgerEntry.objects.create(
        kind=entry.kind, cafe=entry.cafe, paid_on=today, booked_on=today, amount=-entry.amount,
        category=entry.category, paid_by=entry.paid_by, funded_by=entry.funded_by,
        counterparty=entry.counterparty, payee_name=entry.payee_name, invoice=entry.invoice,
        payroll=entry.payroll, income_source=entry.income_source,
        description=f"REVERSAL of {entry.cf_tag}: {reason}"[:300],
        source=LedgerEntry.Source.MANUAL, status=LedgerEntry.Status.CONFIRMED,
        reversal_of=entry, created_by=by, confirmed_by=by, confirmed_at=timezone.now(),
    )
    if entry.invoice_id:
        entry.invoice.refresh_status()
    if entry.payroll_id:
        entry.payroll.refresh_status()
    return rev


# ------------------------------------------------------------------ month close (9.8)

def request_close(cafe: Cafe, month, by: Person) -> Period:
    """First confirmer asks; a DIFFERENT confirmer closes. Both need can_confirm.
    Refused while anything in that month is still pending review."""
    if not by.can_confirm:
        raise PermissionError("not a confirmer")
    month = Period.month_of(month)
    period, _ = Period.objects.get_or_create(cafe=cafe, month=month)
    if period.is_closed:
        return period
    nxt = (month.replace(year=month.year + 1, month=1) if month.month == 12
           else month.replace(month=month.month + 1))
    waiting = LedgerEntry.objects.filter(
        cafe=cafe, paid_on__gte=month, paid_on__lt=nxt,
        status__in=(LedgerEntry.Status.PENDING, LedgerEntry.Status.IMPORTED)).count()
    if waiting:
        raise ValueError(f"{waiting} entr{'y is' if waiting == 1 else 'ies are'} still waiting for review in {month:%B %Y}")
    if period.close_requested_by_id is None:
        period.close_requested_by, period.close_requested_at = by, timezone.now()
        period.save(update_fields=["close_requested_by", "close_requested_at"])
    elif period.close_requested_by_id != by.pk:
        period.closed_by, period.closed_at = by, timezone.now()
        period.save(update_fields=["closed_by", "closed_at"])
    return period


# ------------------------------------------------------------------ partner balance (9.2 / 9.4)

def partner_balance(cafe: Cafe | None = None, month=None):
    """Per person: what they put in, what was settled back, what they are owed.

    put_in   = confirmed EXPENSES paid AND funded by the person (own pocket, directly)
             + confirmed TRANSFERS funded by the person INTO the cafe / another partner
    settled  = confirmed TRANSFERS where the person is the counterparty and the
               money came from the cafe (funded_by is None) — i.e. paid back
    holding  = money the person received from others (transfers to them) minus
               expenses they paid with that money (paid_by=them, funded_by=someone else)
    owed     = put_in − settled
    Expenses paid from someone ELSE's money count for nobody here: the funder
    already got credit when the transfer was recorded. That is the whole point
    of funded_by.
    """
    from django.db.models import Q
    qs = LedgerEntry.objects.filter(status=LedgerEntry.Status.CONFIRMED)
    if cafe is not None:
        qs = qs.filter(cafe=cafe)
    if month is not None:
        m = Period.month_of(month)
        nxt = (m.replace(year=m.year + 1, month=1) if m.month == 12 else m.replace(month=m.month + 1))
        qs = qs.filter(booked_on__gte=m, booked_on__lt=nxt)
    rows = {}
    def r(p):
        return rows.setdefault(p.pk, {"person": p, "put_in": Decimal("0"), "settled": Decimal("0"),
                                      "holding": Decimal("0"), "spent_for_cafe": Decimal("0")})
    for e in qs.select_related("paid_by", "funded_by", "counterparty"):
        if e.kind == LedgerEntry.Kind.EXPENSE:
            if e.funded_by_id and e.funded_by_id == e.paid_by_id:
                r(e.funded_by)["put_in"] += e.amount
                r(e.funded_by)["spent_for_cafe"] += e.amount
            elif e.funded_by_id and e.paid_by_id:
                r(e.paid_by)["holding"] -= e.amount
                r(e.paid_by)["spent_for_cafe"] += e.amount
        elif e.kind == LedgerEntry.Kind.TRANSFER:
            if e.funded_by_id:                      # a partner put money in
                r(e.funded_by)["put_in"] += e.amount
                if e.counterparty_id:               # …and handed it to another partner
                    r(e.counterparty)["holding"] += e.amount
            elif e.counterparty_id:                 # cafe paid a partner back
                r(e.counterparty)["settled"] += e.amount
    out = []
    for v in rows.values():
        v["owed"] = v["put_in"] - v["settled"]
        out.append(v)
    out.sort(key=lambda v: v["person"].name)
    return out


def monthly_pnl(cafe: Cafe, month):
    """Income − expenses by category for one cafe-month, confirmed rows only,
    on booked_on so late entries land where they were booked."""
    from django.db.models import Sum
    m = Period.month_of(month)
    nxt = (m.replace(year=m.year + 1, month=1) if m.month == 12 else m.replace(month=m.month + 1))
    qs = LedgerEntry.objects.filter(cafe=cafe, status=LedgerEntry.Status.CONFIRMED,
                                    booked_on__gte=m, booked_on__lt=nxt)
    income = qs.filter(kind=LedgerEntry.Kind.INCOME).values("income_source").annotate(s=Sum("amount")).order_by("income_source")
    expenses = qs.filter(kind=LedgerEntry.Kind.EXPENSE).values("category__name").annotate(s=Sum("amount")).order_by("-s")
    ti = sum((row["s"] for row in income), Decimal("0"))
    te = sum((row["s"] for row in expenses), Decimal("0"))
    unproven = qs.filter(kind=LedgerEntry.Kind.EXPENSE, proofs__isnull=True).count()
    return {"month": m, "income": list(income), "expenses": list(expenses),
            "total_income": ti, "total_expense": te, "net": ti - te, "unproven": unproven,
            "closed": Period.is_month_closed(cafe, m)}


def suggest_payroll(entry: LedgerEntry):
    """Open salary periods this payment might be settling: staff name in payee,
    or category = salary and amount ≤ outstanding."""
    import re
    qs = PayrollPeriod.objects.exclude(status=PayrollPeriod.Status.PAID).select_related("staff")
    if entry.cafe_id:
        qs = qs.filter(cafe=entry.cafe)
    words = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", (entry.payee_name or "") + " " + (entry.description or ""))}
    out = []
    for pp in qs[:50]:
        sw = {w.lower() for w in re.findall(r"[A-Za-z]{3,}", pp.staff.name)}
        if words & sw or (entry.category and entry.category.slug == "salary" and pp.outstanding >= entry.amount):
            out.append(pp)
    return out[:5]
