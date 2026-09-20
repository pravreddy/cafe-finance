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
from .forms import ReviewForm, UploadForm
from .models import Cafe, Category, GroupPost, Invoice, LedgerEntry, Person, Proof, ShareEvent


def current_person(request) -> Person:
    p = getattr(request.user, "person", None)
    if p is None:
        p, _ = Person.objects.get_or_create(user=request.user, defaults={
            "name": request.user.get_full_name() or request.user.get_username(),
        })
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
                if entry.utr and LedgerEntry.objects.filter(utr=entry.utr).exclude(pk=entry.pk).exists():
                    other = LedgerEntry.objects.get(utr=entry.utr)
                    messages.warning(request, f"That UTR is already on {other.cf_tag}. Linked your share there instead.")
                    proof.entry = other
                    proof.save(update_fields=["entry"])
                    return redirect("ledger:done", entry_pk=other.pk)
                entry.save()
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
        form = ReviewForm(initial=_initial_from(proof))

    ctx = {"proof": proof, "form": form, "me": me,
           "cafes": Cafe.objects.filter(is_active=True),
           "categories": Category.objects.filter(is_active=True),
           "people": Person.objects.all()}
    if proof.entry:
        ctx["trail"] = proof.entry.shares.select_related("shared_by")
        ctx["invoice_suggestions"] = dedup.suggest_invoices(proof.entry)
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
    if action == "confirm":
        services.confirm_entry(entry, me)
        messages.success(request, f"Confirmed {entry.cf_tag}.")
    elif action == "reject":
        entry.status = LedgerEntry.Status.REJECTED
        entry.save(update_fields=["status"])
        messages.info(request, f"Rejected {entry.cf_tag}.")
    elif action == "reverse":
        rev = services.reverse_entry(entry, me, request.POST.get("reason", "")[:200])
        messages.success(request, f"Reversed {entry.cf_tag} with {rev.cf_tag}.")
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
