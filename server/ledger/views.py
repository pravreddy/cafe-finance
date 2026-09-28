"""Screens. Three that matter (docs/02): Receive/Review, My entries, Queue.
Plus the share-target endpoint the PWA manifest points at."""
import mimetypes
from pathlib import Path

from django.conf import settings
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db.models import Q, Sum
from django.http import FileResponse, Http404, HttpResponse, JsonResponse
from django.shortcuts import get_object_or_404, redirect, render
from django.utils import timezone
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import dedup, services
from .forms import ManualEntryForm, ReviewForm, UploadForm
from .models import Cafe, Category, GroupPost, Invoice, LedgerEntry, PayrollPeriod, Period, Person, Proof, ShareEvent


def current_person(request) -> Person:
    p = getattr(request.user, "person", None)
    if p is None:
        name = request.user.get_full_name() or request.user.get_username()
        p = Person.objects.filter(user__isnull=True, name__iexact=name).first() \
            or Person.objects.filter(user__isnull=True, name__iexact=request.user.get_username()).first()
        if p:
            p.user = request.user
            p.save(update_fields=["user"])
        else:
            p = Person.objects.create(user=request.user, name=name.title() if name.islower() else name,
                                      role=Person.Role.PARTNER, can_confirm=request.user.is_superuser)
    return p


# ------------------------------------------------------------------ home

@login_required
def home(request):
    me = current_person(request)
    qs = LedgerEntry.objects.select_related("cafe", "category", "paid_by")
    if not me.can_confirm:
        qs = qs.filter(Q(created_by=me) | Q(paid_by=me))
    entries = qs[:50]
    month = timezone.localdate().replace(day=1)
    my_month = (LedgerEntry.objects.filter(paid_by=me, status=LedgerEntry.Status.CONFIRMED, paid_on__gte=month)
                .aggregate(s=Sum("amount"))["s"] or 0)
    pending = LedgerEntry.objects.filter(status__in=(LedgerEntry.Status.PENDING, LedgerEntry.Status.IMPORTED)).count()
    reading = (Proof.objects.filter(state__in=(Proof.State.QUEUED, Proof.State.PROCESSING), shares__shared_by=me)
               .distinct().order_by("created_at"))
    failed = Proof.objects.filter(state=Proof.State.FAILED, shares__shared_by=me).distinct()[:10]
    return render(request, "ledger/home.html", {
        "me": me, "entries": entries, "my_month": my_month, "pending": pending,
        "reading": reading, "failed": failed, "form": UploadForm(),
    })


# ------------------------------------------------------------------ share target

@csrf_exempt
@login_required
def share(request):
    """Target of the PWA share_target (POST multipart) and the in-browser
    upload form. No CSRF token arrives from the Android share sheet, hence
    csrf_exempt; the session cookie still has to be there (login_required)."""
    if request.method != "POST":
        return render(request, "ledger/share.html", {"form": UploadForm()})
    me = current_person(request)
    note = (request.POST.get("text") or request.POST.get("note") or request.POST.get("title") or "").strip()
    channel = request.POST.get("channel") or ShareEvent.Channel.APP_SHARE
    if channel not in ShareEvent.Channel.values:
        channel = ShareEvent.Channel.APP_SHARE
    origin = Proof.Origin.WHATSAPP_FORWARD if channel == ShareEvent.Channel.WHATSAPP_FORWARD else Proof.Origin.APP_SHARE
    files = request.FILES.getlist("files") or request.FILES.getlist("file")
    if not files:
        messages.error(request, "No file arrived. Share an image or a PDF.")
        return redirect("ledger:home")
    results = []
    for f in files[:10]:
        raw = f.read()
        results.append(services.intake_or_ingest(
            raw, f.name, f.content_type or mimetypes.guess_type(f.name)[0] or "",
            origin=origin, channel=channel, shared_by=me, note=note,
        ))
    if len(results) == 1:
        messages.info(request, results[0].message)
        if results[0].pending:
            return redirect("ledger:home")          # nothing to review yet; the worker will read it
        return redirect("ledger:review", proof_pk=results[0].proof.pk)
    return render(request, "ledger/share_many.html", {"results": results})


# ------------------------------------------------------------------ review

def _initial_from(proof: Proof):
    d = proof.extracted_json or {}
    p, inv = d.get("payment") or {}, d.get("invoice") or {}
    entry, invoice = proof.entry, proof.invoice
    init = {"doc_type": d.get("doc_type") or "other"}
    if entry:
        init.update({
            "cafe": entry.cafe_id, "category": entry.category_id, "description": entry.description,
            "amount": entry.amount, "paid_on": entry.paid_on, "paid_by": entry.paid_by_id,
            "payee_name": entry.payee_name, "upi_app": entry.upi_app, "utr": entry.utr or "",
            "invoice": entry.invoice_id,
        })
    elif p:
        paid_on, _ = services.parse_when(p.get("paid_at"))
        init.update({"amount": p.get("amount"), "paid_on": paid_on, "payee_name": p.get("payee_name"),
                     "description": p.get("message"), "upi_app": p.get("upi_app") or "other",
                     "utr": p.get("utr") or ""})
    if invoice:
        init.update({
            "cafe": invoice.cafe_id, "category": invoice.category_id, "description": invoice.description,
            "vendor_name": invoice.vendor_name, "invoice_number": invoice.invoice_number,
            "invoice_date": invoice.invoice_date, "due_date": invoice.due_date, "total": invoice.total,
        })
    elif inv:
        inv_date, _ = services.parse_when(inv.get("invoice_date"))
        due, _ = services.parse_when(inv.get("due_date"))
        init.update({"vendor_name": inv.get("vendor_name"), "invoice_number": inv.get("invoice_number"),
                     "invoice_date": inv_date, "due_date": due, "total": inv.get("total"),
                     "description": inv.get("description")})
    return init


@login_required
def review(request, proof_pk):
    proof = get_object_or_404(Proof.objects.select_related("entry", "invoice"), pk=proof_pk)
    me = current_person(request)
    if proof.state in (Proof.State.QUEUED, Proof.State.PROCESSING):
        return render(request, "ledger/reading.html", {"proof": proof,
                      "waiting": Proof.objects.filter(state=Proof.State.QUEUED).count()})
    if request.method == "POST":
        form = ReviewForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            if d["doc_type"] == "payment":
                entry = proof.entry or LedgerEntry(source=LedgerEntry.Source.APP, created_by=me)
                if entry.status == LedgerEntry.Status.CONFIRMED:
                    messages.error(request, "This payment is already confirmed. Ask a confirmer to reverse it instead of editing.")
                    return redirect("ledger:done", entry_pk=entry.pk)
                entry.cafe = d["cafe"] or me.default_cafe
                entry.category = d["category"]
                entry.description = d["description"]
                entry.amount = d["amount"]
                entry.paid_on = d["paid_on"]
                entry.paid_by = d["paid_by"] or me
                entry.payee_name = d["payee_name"]
                entry.upi_app = d["upi_app"] or LedgerEntry.UpiApp.OTHER
                entry.utr = d["utr"] or None
                entry.invoice = d["invoice"]
                entry.payroll = d["payroll"]
                fb = d.get("funded_by") or ""
                entry.funded_by = None if fb == "cafe" else (Person.objects.filter(pk=fb).first() if fb else entry.paid_by)
                conf = float((proof.extracted_json or {}).get("confidence") or 0)
                if conf and conf < 0.7 and not d.get("retyped"):
                    messages.error(request, "This was read with low confidence — please retype the amount and date and tick 'I checked these'.")
                    form.add_error("retyped", "Tick to confirm you checked the amount and date.")
                    return _render_review(request, proof, form, me)
                if entry.utr and LedgerEntry.objects.filter(utr=entry.utr).exclude(pk=entry.pk).exists():
                    other = LedgerEntry.objects.get(utr=entry.utr)
                    messages.warning(request, f"That UTR is already on {other.cf_tag}. Linked your share there instead.")
                    proof.entry = other
                    proof.save(update_fields=["entry"])
                    return redirect("ledger:done", entry_pk=other.pk)
                entry.save()
                if entry.payroll_id:
                    entry.payroll.refresh_status()
                if proof.entry_id != entry.pk:
                    proof.entry, proof.invoice = entry, None
                    proof.kind = Proof.Kind.PAYMENT_SCREENSHOT
                    proof.save(update_fields=["entry", "invoice", "kind"])
                    proof.shares.filter(entry__isnull=True).update(entry=entry)
                if entry.invoice_id:
                    entry.invoice.refresh_status()
                return redirect("ledger:done", entry_pk=entry.pk)
            if d["doc_type"] == "invoice":
                invoice = proof.invoice or Invoice(created_by=me)
                invoice.cafe = d["cafe"] or me.default_cafe
                invoice.category = d["category"]
                invoice.description = d["description"]
                invoice.vendor_name = d["vendor_name"]
                invoice.invoice_number = d["invoice_number"]
                invoice.invoice_date = d["invoice_date"]
                invoice.due_date = d["due_date"]
                invoice.total = d["total"]
                if invoice.status == Invoice.Status.PENDING:
                    invoice.status = Invoice.Status.OPEN
                invoice.save()
                invoice.refresh_status()
                if proof.invoice_id != invoice.pk:
                    proof.invoice, proof.entry = invoice, None
                    proof.kind = Proof.Kind.INVOICE_PDF if proof.is_pdf else Proof.Kind.INVOICE_IMAGE
                    proof.save(update_fields=["entry", "invoice", "kind"])
                    proof.shares.filter(invoice__isnull=True).update(invoice=invoice)
                messages.success(request, f"Invoice saved: {invoice}")
                return redirect("ledger:invoice_detail", pk=invoice.pk)
            # not money
            proof.kind = Proof.Kind.OTHER
            proof.save(update_fields=["kind"])
            messages.info(request, "Marked as not a money document.")
            return redirect("ledger:home")
    else:
        init = _initial_from(proof)
        if proof.entry:
            init["funded_by"] = ("cafe" if proof.entry.funded_by_id is None and proof.entry.pk
                                 else (str(proof.entry.funded_by_id) if proof.entry.funded_by_id != proof.entry.paid_by_id else ""))
            init["payroll"] = proof.entry.payroll_id
        form = ReviewForm(initial=init)
    return _render_review(request, proof, form, me)


def _render_review(request, proof, form, me):
    ctx = {"proof": proof, "form": form, "me": me,
           "cafes": Cafe.objects.filter(is_active=True),
           "categories": Category.objects.filter(is_active=True),
           "people": Person.objects.all(),
           "partners": Person.objects.filter(role=Person.Role.PARTNER),
           "low_confidence": 0 < float((proof.extracted_json or {}).get("confidence") or 0) < 0.7,
           "failed_payment": ((proof.extracted_json or {}).get("payment") or {}).get("status") in ("failed", "pending")}
    if proof.entry:
        ctx["trail"] = proof.entry.shares.select_related("shared_by")
        ctx["invoice_suggestions"] = dedup.suggest_invoices(proof.entry)
        ctx["payroll_suggestions"] = services.suggest_payroll(proof.entry)
    elif proof.invoice:
        ctx["trail"] = proof.invoice.shares.select_related("shared_by")
    return render(request, "ledger/review.html", ctx)


@login_required
def done(request, entry_pk):
    entry = get_object_or_404(LedgerEntry.objects.select_related("cafe", "category"), pk=entry_pk)
    proof = entry.proofs.first()
    return render(request, "ledger/done.html", {"entry": entry, "proof": proof,
                                                "caption": entry.whatsapp_caption()})


# ------------------------------------------------------------------ queue (confirmers)

@login_required
def queue(request):
    me = current_person(request)
    if not me.can_confirm:
        messages.error(request, "Only a confirmer can see the review queue.")
        return redirect("ledger:home")
    pending = (LedgerEntry.objects.filter(status__in=(LedgerEntry.Status.PENDING, LedgerEntry.Status.IMPORTED))
               .select_related("cafe", "category", "paid_by").order_by("paid_on"))
    invoices = Invoice.objects.filter(status=Invoice.Status.PENDING)
    open_invoices = Invoice.objects.filter(status__in=(Invoice.Status.OPEN, Invoice.Status.PART_PAID))
    unmatched = (GroupPost.objects.filter(matched_entry__isnull=True, matched_invoice__isnull=True, dismissed=False)
                 .exclude(media_path="").exclude(proof__state__in=(Proof.State.QUEUED, Proof.State.PROCESSING))[:100])
    reading = Proof.objects.filter(state__in=(Proof.State.QUEUED, Proof.State.PROCESSING)).count()
    failed = Proof.objects.filter(state=Proof.State.FAILED).order_by("-created_at")[:20]
    return render(request, "ledger/queue.html", {
        "me": me, "pending": pending, "invoices": invoices, "open_invoices": open_invoices,
        "unmatched": unmatched, "reading": reading, "failed": failed,
    })


@login_required
@require_POST
def entry_action(request, entry_pk, action):
    me = current_person(request)
    entry = get_object_or_404(LedgerEntry, pk=entry_pk)
    if not me.can_confirm:
        return HttpResponse(status=403)
    if action in ("confirm", "confirm_late"):
        pay = ((entry.proofs.first().extracted_json if entry.proofs.exists() else {}) or {}).get("payment") or {}
        if pay.get("status") in ("failed", "pending"):
            messages.error(request, f"{entry.cf_tag} is a {pay['status']} payment — attach a success screenshot first.")
            return redirect(request.POST.get("next") or "ledger:queue")
        try:
            services.confirm_entry(entry, me, late_ok=(action == "confirm_late"))
            messages.success(request, f"Confirmed {entry.cf_tag}" + (" (booked late, today)." if entry.late_for_id else "."))
        except services.PeriodClosed as e:
            messages.error(request, f"{e}. Use 'Confirm as late' to book it in the current month.")
    elif action == "reject":
        entry.status = LedgerEntry.Status.REJECTED
        entry.save(update_fields=["status"])
        messages.info(request, f"Rejected {entry.cf_tag}.")
    elif action == "reverse":
        try:
            rev = services.reverse_entry(entry, me, request.POST.get("reason", "")[:200])
            messages.success(request, f"Reversed {entry.cf_tag} with {rev.cf_tag}.")
        except services.PeriodClosed as e:
            messages.error(request, str(e))
    else:
        raise Http404
    return redirect(request.POST.get("next") or "ledger:queue")


@login_required
@require_POST
def group_post_dismiss(request, pk):
    me = current_person(request)
    if not me.can_confirm:
        return HttpResponse(status=403)
    GroupPost.objects.filter(pk=pk).update(dismissed=True)
    return redirect("ledger:queue")


# ------------------------------------------------------------------ invoices

@login_required
def invoices(request):
    qs = Invoice.objects.select_related("cafe", "category").exclude(status=Invoice.Status.REJECTED)
    return render(request, "ledger/invoices.html", {"invoices": qs[:200]})


@login_required
def invoice_detail(request, pk):
    inv = get_object_or_404(Invoice.objects.select_related("cafe", "category"), pk=pk)
    return render(request, "ledger/invoice_detail.html", {
        "invoice": inv,
        "payments": inv.payments.select_related("paid_by").order_by("paid_on"),
        "proofs": inv.proofs.all(),
        "trail": inv.shares.select_related("shared_by"),
    })


# ------------------------------------------------------------------ files

@login_required
def proof_file(request, pk):
    proof = get_object_or_404(Proof, pk=pk)
    path = Path(proof.file.path)
    if not path.exists():
        raise Http404
    ctype = proof.mime or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    resp = FileResponse(open(path, "rb"), content_type=ctype)
    resp["Content-Disposition"] = f'inline; filename="{path.name}"'
    return resp


# ------------------------------------------------------------------ PWA plumbing

def service_worker(request):
    """sw.js must be served from '/' so its scope covers the whole app."""
    path = Path(__file__).resolve().parent / "static" / "ledger" / "sw.js"
    resp = HttpResponse(path.read_text(), content_type="application/javascript")
    resp["Service-Worker-Allowed"] = "/"
    resp["Cache-Control"] = "no-cache"
    return resp


def manifest(request):
    path = Path(__file__).resolve().parent / "static" / "ledger" / "manifest.json"
    return HttpResponse(path.read_text(), content_type="application/manifest+json")


def offline(request):
    return render(request, "ledger/offline.html")


# ------------------------------------------------------------------ typed-in entries (9.1 / 9.4 / 9.6)

@login_required
def add_entry(request):
    me = current_person(request)
    if request.method == "POST":
        form = ManualEntryForm(request.POST, request.FILES)
        if form.is_valid():
            d = form.cleaned_data
            fb = d.get("funded_by") or ""
            paid_by = d["paid_by"] or me
            entry = LedgerEntry(
                kind=d["kind"], cafe=d["cafe"], amount=d["amount"], paid_on=d["paid_on"],
                category=d["category"], paid_by=paid_by,
                funded_by=None if fb == "cafe" else (Person.objects.filter(pk=fb).first() if fb else (paid_by if d["kind"] != "income" else None)),
                counterparty=d["counterparty"], income_source=d["income_source"] or "",
                payee_name=d["payee_name"], description=d["description"],
                upi_app=d["upi_app"] or LedgerEntry.UpiApp.CASH,
                source=LedgerEntry.Source.MANUAL, status=LedgerEntry.Status.PENDING, created_by=me,
            )
            if d["kind"] == "transfer" and fb == "" and d["counterparty"] and d["counterparty"].pk == paid_by.pk:
                entry.funded_by = None   # cafe paying a partner back = settlement
            entry.save()
            photo = d.get("photo")
            if photo:
                proof, _ = services.store_proof(photo.read(), photo.name, photo.content_type or "", Proof.Origin.UPLOAD)
                proof.entry, proof.kind, proof.state = entry, Proof.Kind.PAYMENT_SCREENSHOT, Proof.State.DONE
                proof.save(update_fields=["entry", "kind", "state"])
                ShareEvent.objects.create(proof=proof, entry=entry, shared_by=me, channel=ShareEvent.Channel.MANUAL,
                                          match_method="new", note=d["description"])
            messages.success(request, f"Saved {entry.cf_tag} — a confirmer will check it.")
            return redirect("ledger:done", entry_pk=entry.pk)
    else:
        init = {"paid_on": timezone.localdate(), "cafe": me.default_cafe_id, "paid_by": me.pk}
        if request.GET.get("settle"):
            p = Person.objects.filter(pk=request.GET["settle"]).first()
            init.update({"kind": "transfer", "counterparty": p.pk if p else None, "funded_by": "cafe",
                         "category": Category.objects.filter(slug="partner-out").first(),
                         "description": f"Settling up with {p.name}" if p else "", "upi_app": "bank"})
        form = ManualEntryForm(initial=init)
    return render(request, "ledger/add.html", {"form": form, "me": me,
                  "cafes": Cafe.objects.filter(is_active=True), "categories": Category.objects.filter(is_active=True),
                  "people": Person.objects.all(), "partners": Person.objects.filter(role=Person.Role.PARTNER)})


# ------------------------------------------------------------------ balance (9.2 / 9.4) and P&L

@login_required
def balance(request):
    me = current_person(request)
    if me.role != Person.Role.PARTNER:
        messages.error(request, "Balances are for partners.")
        return redirect("ledger:home")
    cafe = Cafe.objects.filter(pk=request.GET.get("cafe")).first()
    rows = services.partner_balance(cafe)
    return render(request, "ledger/balance.html", {"rows": rows, "cafe": cafe,
                  "cafes": Cafe.objects.filter(is_active=True), "me": me})


@login_required
def pnl(request):
    me = current_person(request)
    if me.role != Person.Role.PARTNER:
        return redirect("ledger:home")
    cafes = Cafe.objects.filter(is_active=True)
    cafe = Cafe.objects.filter(pk=request.GET.get("cafe")).first() or cafes.first()
    try:
        month = timezone.datetime.strptime(request.GET.get("month", ""), "%Y-%m").date()
    except ValueError:
        month = timezone.localdate().replace(day=1)
    data = services.monthly_pnl(cafe, month) if cafe else None
    return render(request, "ledger/pnl.html", {"cafe": cafe, "cafes": cafes, "month": month, "data": data, "me": me})


# ------------------------------------------------------------------ periods (9.8)

@login_required
def periods(request):
    me = current_person(request)
    if not me.can_confirm:
        return redirect("ledger:home")
    cafes = Cafe.objects.filter(is_active=True)
    today = timezone.localdate().replace(day=1)
    months = []
    m = today
    for _ in range(12):
        months.append(m)
        m = (m.replace(year=m.year - 1, month=12) if m.month == 1 else m.replace(month=m.month - 1))
    existing = {(p.cafe_id, p.month): p for p in Period.objects.filter(cafe__in=cafes)}
    grid = [{"cafe": c, "months": [{"month": mm, "period": existing.get((c.pk, mm))} for mm in months]} for c in cafes]
    return render(request, "ledger/periods.html", {"grid": grid, "me": me})


@login_required
@require_POST
def period_close(request, cafe_pk, ym):
    me = current_person(request)
    cafe = get_object_or_404(Cafe, pk=cafe_pk)
    month = timezone.datetime.strptime(ym, "%Y-%m").date()
    try:
        p = services.request_close(cafe, month, me)
        if p.is_closed:
            messages.success(request, f"{cafe} {month:%B %Y} is closed.")
        else:
            messages.info(request, f"Close requested by {p.close_requested_by}. A second confirmer must close it.")
    except (PermissionError, ValueError) as e:
        messages.error(request, str(e) or "Not allowed.")
    return redirect("ledger:periods")


# ------------------------------------------------------------------ payroll (9.3)

@login_required
def payroll(request):
    me = current_person(request)
    if me.role != Person.Role.PARTNER:
        return redirect("ledger:home")
    if request.method == "POST" and me.can_confirm:
        try:
            staff = Person.objects.get(pk=request.POST.get("staff"))
            cafe = Cafe.objects.get(pk=request.POST.get("cafe"))
            month = timezone.datetime.strptime(request.POST.get("month", ""), "%Y-%m").date()
            pp, created = PayrollPeriod.objects.update_or_create(
                cafe=cafe, staff=staff, month=month,
                defaults={"amount_owed": request.POST.get("amount_owed") or 0,
                          "advances": request.POST.get("advances") or 0,
                          "notes": request.POST.get("notes", "")[:300]})
            pp.refresh_status()
            messages.success(request, f"{'Added' if created else 'Updated'} {pp}.")
        except (Person.DoesNotExist, Cafe.DoesNotExist, ValueError) as e:
            messages.error(request, f"Could not save: {e}")
        return redirect("ledger:payroll")
    rows = PayrollPeriod.objects.select_related("staff", "cafe").all()[:100]
    return render(request, "ledger/payroll.html", {"rows": rows, "me": me,
                  "staff": Person.objects.filter(role=Person.Role.STAFF), "cafes": Cafe.objects.filter(is_active=True),
                  "this_month": timezone.localdate().strftime("%Y-%m")})


# ------------------------------------------------------------------ reports (analysis)

def _month_param(request):
    try:
        return timezone.datetime.strptime(request.GET.get("month", ""), "%Y-%m").date()
    except ValueError:
        return timezone.localdate().replace(day=1)


@login_required
def reports(request):
    from . import reports as R
    me = current_person(request)
    if me.role != Person.Role.PARTNER:
        messages.error(request, "Reports are for partners.")
        return redirect("ledger:home")
    cafe = Cafe.objects.filter(pk=request.GET.get("cafe")).first()
    month = _month_param(request)
    d = R.dashboard(cafe, month)
    entries = R.entries_for(cafe, month)[:300]
    return render(request, "ledger/reports.html", {"d": d, "cafe": cafe, "entries": entries,
                  "cafes": Cafe.objects.filter(is_active=True), "me": me})


@login_required
def export(request):
    from . import reports as R
    me = current_person(request)
    if me.role != Person.Role.PARTNER:
        return HttpResponse(status=403)
    cafe = Cafe.objects.filter(pk=request.GET.get("cafe")).first()
    month = _month_param(request)
    name = f"cafe-finance_{cafe.slug if cafe else 'all'}_{month:%Y-%m}"
    if request.GET.get("zip"):
        resp = HttpResponse(R.export_zip(cafe, month), content_type="application/zip")
        resp["Content-Disposition"] = f'attachment; filename="{name}.zip"'
    else:
        resp = HttpResponse(R.export_csv(cafe, month), content_type="text/csv; charset=utf-8")
        resp["Content-Disposition"] = f'attachment; filename="{name}.csv"'
    return resp
