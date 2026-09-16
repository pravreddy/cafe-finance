"""Machine endpoints for the two scripts we own: the Mac reconciler and the
mail puller. Auth is one shared token in X-Api-Token (settings.API_TOKEN)."""
import json
from datetime import datetime
from functools import wraps

from django.conf import settings
from django.http import JsonResponse
from django.utils import timezone
from django.utils.dateparse import parse_datetime
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.http import require_POST

from . import dedup, services
from .models import Cafe, GroupPost, Person, Proof, ShareEvent


def token_required(view):
    @wraps(view)
    def inner(request, *a, **kw):
        want = settings.API_TOKEN
        got = request.headers.get("X-Api-Token", "")
        if not want or got != want:
            return JsonResponse({"error": "bad token"}, status=401)
        return view(request, *a, **kw)
    return inner


def _when(s):
    if not s:
        return timezone.now()
    dt = parse_datetime(s)
    if dt is None:
        try:
            dt = datetime.fromtimestamp(float(s))
        except (TypeError, ValueError):
            return timezone.now()
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, timezone.get_current_timezone())
    return dt


def _person_for(sender: str, jid: str = ""):
    if jid:
        p = Person.objects.filter(whatsapp_jid=jid).first()
        if p:
            return p
    if sender:
        return Person.objects.filter(name__iexact=sender.strip()).first()
    return None


def _cafe_for_group(group_name: str):
    for c in Cafe.objects.filter(is_active=True):
        if group_name in (c.whatsapp_groups or []):
            return c
    return None


def _result_json(r):
    return {
        "proof_id": r.proof.pk, "doc_type": r.doc_type, "duplicate": r.duplicate,
        "match_method": r.match_method,
        "entry_id": r.entry.pk if r.entry else None,
        "cf_tag": r.entry.cf_tag if r.entry else None,
        "invoice_id": r.invoice.pk if r.invoice else None,
        "extraction_error": r.extraction_error, "message": r.message,
    }


@csrf_exempt
@require_POST
@token_required
def upload(request):
    """Mail puller: multipart 'file' + note/sender/shared_at/origin_ref."""
    f = request.FILES.get("file")
    if not f:
        return JsonResponse({"error": "file missing"}, status=400)
    cafe = Cafe.objects.filter(slug=request.POST.get("cafe", "")).first()
    r = services.ingest(
        f.read(), f.name, f.content_type or "",
        origin=Proof.Origin.EMAIL, channel=ShareEvent.Channel.EMAIL,
        shared_by=_person_for(request.POST.get("sender", "")),
        shared_by_label=request.POST.get("sender", ""),
        note=request.POST.get("note", ""), shared_at=_when(request.POST.get("shared_at")),
        cafe=cafe, origin_ref=request.POST.get("origin_ref", ""),
    )
    return JsonResponse(_result_json(r))


@csrf_exempt
@require_POST
@token_required
def reconcile(request):
    """Mac reconciler: one WhatsApp group message per call.
    multipart: group_name, wa_message_pk, sender, sender_jid, sent_at, caption,
               media_path, imported (0/1), and optionally 'file' (the media)."""
    P = request.POST
    group, pk = P.get("group_name", ""), P.get("wa_message_pk", "")
    if not (group and pk):
        return JsonResponse({"error": "group_name and wa_message_pk required"}, status=400)
    post, created = GroupPost.objects.get_or_create(
        group_name=group, wa_message_pk=pk,
        defaults={"sender": P.get("sender", "")[:120], "sent_at": _when(P.get("sent_at")),
                  "caption": P.get("caption", ""), "media_path": P.get("media_path", "")[:400]},
    )
    out = {"group_post_id": post.pk, "created": created}
    f = request.FILES.get("file")
    if f and post.proof_id is None:
        r = services.ingest(
            f.read(), f.name, f.content_type or "",
            origin=Proof.Origin.WHATSAPP_GROUP, channel=ShareEvent.Channel.WHATSAPP_GROUP,
            shared_by=_person_for(post.sender, P.get("sender_jid", "")),
            shared_by_label=post.sender, note=post.caption, shared_at=post.sent_at,
            cafe=_cafe_for_group(group), origin_ref=f"{group}:{pk}",
            imported=P.get("imported") in ("1", "true", "yes"),
        )
        post.proof = r.proof
        post.matched_entry, post.matched_invoice = r.entry, r.invoice
        post.match_method = r.match_method
        post.save(update_fields=["proof", "matched_entry", "matched_invoice", "match_method"])
        out.update(_result_json(r))
    elif not f and post.caption and post.matched_entry_id is None:
        # text-only post: still try the #CF tag so "sent" confirmations match
        m = dedup.CF_TAG_RE.search(post.caption)
        if m:
            from .models import LedgerEntry
            e = LedgerEntry.objects.filter(cf_tag=f"#CF-{m.group(1)}").first()
            if e:
                post.matched_entry, post.match_method = e, "cf_tag"
                post.save(update_fields=["matched_entry", "match_method"])
                out["entry_id"] = e.pk
    return JsonResponse(out)


@csrf_exempt
@token_required
def reconcile_cursor(request):
    """Reconciler asks where it left off per group: {group_name: last_wa_message_pk}."""
    data = {}
    for row in GroupPost.objects.values("group_name").distinct():
        g = row["group_name"]
        last = GroupPost.objects.filter(group_name=g).order_by("-sent_at").first()
        data[g] = {"last_pk": last.wa_message_pk, "last_sent_at": last.sent_at.isoformat()}
    return JsonResponse(data)
