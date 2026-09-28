"""Analysis — the questions the partners actually ask, answered from confirmed
rows only. Used by /reports/ (web and phone) and /export/."""
import csv
import io
from collections import defaultdict
from datetime import timedelta
from decimal import Decimal

from django.db.models import Count, Q, Sum

from .models import Cafe, GroupPost, Invoice, LedgerEntry, PayrollPeriod, Period, Proof


def month_bounds(m):
    m = Period.month_of(m)
    nxt = m.replace(year=m.year + 1, month=1) if m.month == 12 else m.replace(month=m.month + 1)
    return m, nxt


def months_back(m, n):
    out, cur = [], Period.month_of(m)
    for _ in range(n):
        out.append(cur)
        cur = cur.replace(year=cur.year - 1, month=12) if cur.month == 1 else cur.replace(month=cur.month - 1)
    return list(reversed(out))


def _confirmed(cafe=None):
    qs = LedgerEntry.objects.filter(status=LedgerEntry.Status.CONFIRMED)
    return qs.filter(cafe=cafe) if cafe else qs


def dashboard(cafe, month):
    """Everything the /reports/ page shows for one cafe-month (cafe=None → all)."""
    m, nxt = month_bounds(month)
    qs = _confirmed(cafe).filter(booked_on__gte=m, booked_on__lt=nxt)
    exp = qs.filter(kind=LedgerEntry.Kind.EXPENSE)
    inc = qs.filter(kind=LedgerEntry.Kind.INCOME)
    total_exp = exp.aggregate(s=Sum("amount"))["s"] or Decimal("0")
    total_inc = inc.aggregate(s=Sum("amount"))["s"] or Decimal("0")

    by_cat = list(exp.values("category__name").annotate(s=Sum("amount"), n=Count("id")).order_by("-s"))
    by_person = list(exp.values("paid_by__name", "funded_by__name").annotate(s=Sum("amount")).order_by("-s"))
    by_payee = list(exp.exclude(payee_name="").values("payee_name").annotate(s=Sum("amount"), n=Count("id")).order_by("-s")[:10])
    by_income = list(inc.values("income_source").annotate(s=Sum("amount")).order_by("-s"))

    # daily spend for the month (sparkline-ish bar strip)
    daily = defaultdict(Decimal)
    for row in exp.values("booked_on").annotate(s=Sum("amount")):
        daily[row["booked_on"]] += row["s"]
    days = []
    d = m
    while d < nxt:
        days.append({"day": d, "s": daily.get(d, Decimal("0"))})
        d += timedelta(days=1)

    # 6-month trend, same cafe filter
    trend = []
    for mm in months_back(month, 6):
        a, b = month_bounds(mm)
        t = _confirmed(cafe).filter(booked_on__gte=a, booked_on__lt=b)
        trend.append({
            "month": mm,
            "expense": t.filter(kind=LedgerEntry.Kind.EXPENSE).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
            "income": t.filter(kind=LedgerEntry.Kind.INCOME).aggregate(s=Sum("amount"))["s"] or Decimal("0"),
        })

    pending_qs = LedgerEntry.objects.filter(status__in=(LedgerEntry.Status.PENDING, LedgerEntry.Status.IMPORTED))
    open_inv = Invoice.objects.filter(status__in=(Invoice.Status.OPEN, Invoice.Status.PART_PAID))
    open_pay = PayrollPeriod.objects.exclude(status=PayrollPeriod.Status.PAID)
    unmatched = GroupPost.objects.filter(matched_entry__isnull=True, matched_invoice__isnull=True, dismissed=False).exclude(media_path="")
    if cafe:
        pending_qs, open_inv, open_pay = pending_qs.filter(cafe=cafe), open_inv.filter(cafe=cafe), open_pay.filter(cafe=cafe)

    return {
        "month": m, "cafe": cafe,
        "total_income": total_inc, "total_expense": total_exp, "net": total_inc - total_exp,
        "count_expense": exp.count(), "count_income": inc.count(),
        "unproven": exp.filter(proofs__isnull=True).count(),
        "late": qs.filter(late_for__isnull=False).count(),
        "by_cat": by_cat, "by_person": by_person, "by_payee": by_payee, "by_income": by_income,
        "days": days, "max_day": max((x["s"] for x in days), default=Decimal("0")),
        "trend": trend, "max_trend": max((max(t["expense"], t["income"]) for t in trend), default=Decimal("0")),
        "pending": pending_qs.count(), "open_invoices": open_inv.count(),
        "owed_invoices": sum((i.outstanding or Decimal("0")) for i in open_inv),
        "open_payroll": open_pay.count(), "owed_payroll": sum((p.outstanding for p in open_pay), Decimal("0")),
        "unmatched": unmatched.count(),
        "reading": Proof.objects.filter(state__in=(Proof.State.QUEUED, Proof.State.PROCESSING)).count(),
        "closed": Period.is_month_closed(cafe, m) if cafe else None,
    }


def entries_for(cafe, month, kind=None, category=None, person=None, status="confirmed"):
    m, nxt = month_bounds(month)
    qs = LedgerEntry.objects.filter(booked_on__gte=m, booked_on__lt=nxt).select_related(
        "cafe", "category", "paid_by", "funded_by", "invoice", "payroll")
    if status:
        qs = qs.filter(status=status)
    if cafe:
        qs = qs.filter(cafe=cafe)
    if kind:
        qs = qs.filter(kind=kind)
    if category:
        qs = qs.filter(category__slug=category)
    if person:
        qs = qs.filter(Q(paid_by__pk=person) | Q(funded_by__pk=person))
    return qs.order_by("booked_on", "pk")


def export_csv(cafe, month):
    """The accountant's file: one row per confirmed entry, proofs listed by id."""
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["cf_tag", "cafe", "kind", "paid_on", "booked_on", "late_for", "amount", "category",
                "paid_by", "funded_by", "counterparty", "payee", "description", "upi_app", "utr", "txn_id",
                "invoice", "payroll", "income_source", "source", "confirmed_by", "confirmed_at", "proof_ids"])
    for e in entries_for(cafe, month):
        w.writerow([e.cf_tag, e.cafe, e.kind, e.paid_on, e.booked_on,
                    f"{e.late_for.month:%Y-%m}" if e.late_for_id else "", e.amount,
                    e.category.name if e.category else "", e.paid_by or "", e.funded_by or "", e.counterparty or "",
                    e.payee_name, e.description, e.upi_app, e.utr or "", e.txn_id,
                    str(e.invoice) if e.invoice_id else "", str(e.payroll) if e.payroll_id else "",
                    e.income_source, e.source, e.confirmed_by or "",
                    e.confirmed_at.isoformat() if e.confirmed_at else "",
                    " ".join(str(p.pk) for p in e.proofs.all())])
    return buf.getvalue()


def export_zip(cafe, month):
    """CSV + every proof file for the month, for the accountant."""
    import zipfile
    from pathlib import Path
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("entries.csv", export_csv(cafe, month))
        for e in entries_for(cafe, month):
            for p in e.proofs.all():
                path = Path(p.file.path)
                if path.exists():
                    z.write(path, f"proofs/{e.cf_tag.strip('#')}_{p.pk}{path.suffix.lower()}")
    return buf.getvalue()
