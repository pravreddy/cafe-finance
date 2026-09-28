"""Settings — the admin page for this app (design rule, 2026-09-09: "Settings IS
the admin page"). Everything a partner needs to run the ledger lives here in
plain language: people and their logins, cafes and their WhatsApp groups,
categories. Only superusers see it. Django's /admin/ stays for emergency repair
and is never linked from the app or mentioned in any instruction.
"""
from functools import wraps

from django import forms
from django.contrib import messages
from django.contrib.auth import get_user_model
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.shortcuts import get_object_or_404, redirect, render
from django.utils.text import slugify

from .models import Cafe, Category, LedgerEntry, Person, Proof

User = get_user_model()


def superuser_required(view):
    @wraps(view)
    @login_required
    def inner(request, *a, **kw):
        if not request.user.is_superuser:
            messages.error(request, "Settings are only for the owner.")
            return redirect("ledger:home")
        return view(request, *a, **kw)
    return inner


# ------------------------------------------------------------------ forms

class PersonForm(forms.Form):
    name = forms.CharField(max_length=80)
    role = forms.ChoiceField(choices=Person.Role.choices)
    phone = forms.CharField(max_length=20, required=False)
    whatsapp_jid = forms.CharField(max_length=60, required=False,
                                   help_text="e.g. 919876543210@s.whatsapp.net — lets the WhatsApp reader credit this person")
    default_cafe = forms.ModelChoiceField(queryset=Cafe.objects.all(), required=False)
    can_confirm = forms.BooleanField(required=False)
    # login
    username = forms.CharField(max_length=150, required=False, help_text="leave blank for no login (e.g. staff who never use the app)",
                               widget=forms.TextInput(attrs={"autocomplete": "off", "autocapitalize": "none"}))
    # autocomplete=new-password: stops Chrome filling in the OWNER's saved password,
    # which would silently set someone else's password to yours on Save.
    password = forms.CharField(required=False, widget=forms.PasswordInput(render_value=False,
                               attrs={"autocomplete": "new-password"}),
                               help_text="type to set or change; leave blank to keep")
    can_login = forms.BooleanField(required=False, initial=True)
    is_owner = forms.BooleanField(required=False, help_text="can open Settings")

    def clean(self):
        d = super().clean()
        if d.get("password") and not d.get("username"):
            self.add_error("username", "A password needs a username.")
        if d.get("password") and len(d["password"]) < 8:
            self.add_error("password", "At least 8 characters.")
        j = (d.get("whatsapp_jid") or "").strip()
        if j and "@" not in j:
            d["whatsapp_jid"] = f"{''.join(c for c in j if c.isdigit())}@s.whatsapp.net"
        return d


class CafeForm(forms.Form):
    name = forms.CharField(max_length=80)
    is_active = forms.BooleanField(required=False, help_text="only active cafes appear in forms, reports and the WhatsApp reader")
    whatsapp_groups = forms.CharField(required=False, widget=forms.Textarea(attrs={"rows": 3}),
                                      help_text="one group name per line, exactly as in WhatsApp")
    fastpos_tenant = forms.CharField(max_length=60, required=False)
    fastpos_token = forms.CharField(max_length=80, required=False, widget=forms.PasswordInput(render_value=True),
                                    help_text="owner token from FastPOS — pulls daily POS sales as income")


class CategoryForm(forms.Form):
    name = forms.CharField(max_length=60)
    kind = forms.ChoiceField(choices=Category.Kind.choices)
    sort = forms.IntegerField(required=False, initial=100)


# ------------------------------------------------------------------ overview

@superuser_required
def home(request):
    return render(request, "ledger/settings/home.html", {
        "people": Person.objects.select_related("user", "default_cafe").order_by("role", "name"),
        "cafes": Cafe.objects.all().order_by("-is_active", "name"),
        "categories": Category.objects.all(),
        "stats": {
            "entries": LedgerEntry.objects.count(),
            "pending": LedgerEntry.objects.filter(status__in=("pending", "imported")).count(),
            "proofs": Proof.objects.count(),
            "reading": Proof.objects.filter(state__in=("queued", "processing")).count(),
            "failed": Proof.objects.filter(state="failed").count(),
        },
    })


# ------------------------------------------------------------------ people

@superuser_required
def person_edit(request, pk=None):
    person = get_object_or_404(Person.objects.select_related("user"), pk=pk) if pk else None
    u = person.user if person else None
    if request.method == "POST":
        form = PersonForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            try:
                with transaction.atomic():
                    p = person or Person()
                    p.name, p.role, p.phone = d["name"], d["role"], d["phone"]
                    p.whatsapp_jid, p.default_cafe, p.can_confirm = d["whatsapp_jid"], d["default_cafe"], d["can_confirm"]
                    uname = (d["username"] or "").strip().lower()
                    if uname:
                        user = p.user if p.user_id else None
                        if user is None:
                            user = User.objects.filter(username__iexact=uname).first() or User(username=uname)
                            if getattr(user, "person", None) and user.person.pk != p.pk:
                                raise IntegrityError(f"login '{uname}' already belongs to {user.person}")
                        clash = User.objects.filter(username__iexact=uname).exclude(pk=user.pk).first()
                        if clash:
                            raise IntegrityError(f"login '{uname}' is already used by {getattr(clash, 'person', None) or clash.username}")
                        user.username = uname
                        user.first_name = d["name"][:150]
                        user.is_active = d["can_login"]
                        if user.pk == request.user.pk and not d["is_owner"]:
                            messages.warning(request, "You can't remove your own Settings access — kept it.")
                        else:
                            user.is_superuser = user.is_staff = d["is_owner"]
                        if d["password"]:
                            user.set_password(d["password"])
                        elif not user.pk:
                            user.set_unusable_password()
                        user.save()
                        p.user = user
                    elif p.user_id:
                        if p.user_id == request.user.pk:
                            raise IntegrityError("you can't remove your own login")
                        old = p.user; p.user = None; p.save(); old.is_active = False; old.save()
                    p.save()
                messages.success(request, f"Saved {p.name}." + (" New password set." if d["password"] else ""))
                return redirect("ledger:settings")
            except IntegrityError as e:
                messages.error(request, f"Couldn't save: {e}")
    else:
        form = PersonForm(initial={
            "name": person.name if person else "", "role": person.role if person else "partner",
            "phone": person.phone if person else "", "whatsapp_jid": person.whatsapp_jid if person else "",
            "default_cafe": person.default_cafe_id if person else Cafe.objects.filter(is_active=True).first(),
            "can_confirm": person.can_confirm if person else False,
            "username": u.username if u else "", "can_login": u.is_active if u else True,
            "is_owner": u.is_superuser if u else False,
        })
    return render(request, "ledger/settings/person.html", {"form": form, "person": person, "has_login": bool(u),
                  "has_password": bool(u and u.has_usable_password())})


# ------------------------------------------------------------------ cafes

@superuser_required
def cafe_edit(request, pk=None):
    cafe = get_object_or_404(Cafe, pk=pk) if pk else None
    if request.method == "POST":
        form = CafeForm(request.POST)
        if form.is_valid():
            d = form.cleaned_data
            c = cafe or Cafe()
            c.name, c.is_active = d["name"], d["is_active"]
            c.whatsapp_groups = [g.strip() for g in d["whatsapp_groups"].splitlines() if g.strip()]
            c.fastpos_tenant, c.fastpos_token = d["fastpos_tenant"].strip(), d["fastpos_token"].strip()
            if not c.slug:
                base = slugify(d["name"])[:36] or "cafe"
                c.slug, n = base, 2
                while Cafe.objects.filter(slug=c.slug).exists():
                    c.slug = f"{base}-{n}"; n += 1
            # a WhatsApp group can belong to one cafe only
            clash = [g for g in c.whatsapp_groups
                     for o in Cafe.objects.exclude(pk=c.pk) if g in (o.whatsapp_groups or [])]
            if clash:
                messages.error(request, f"Group '{clash[0]}' already belongs to another cafe — remove it there first.")
            else:
                try:
                    c.save()
                    messages.success(request, f"Saved {c.name}.")
                    return redirect("ledger:settings")
                except IntegrityError:
                    messages.error(request, "Another cafe already has that name.")
    else:
        form = CafeForm(initial={
            "name": cafe.name if cafe else "", "is_active": cafe.is_active if cafe else True,
            "whatsapp_groups": "\n".join(cafe.whatsapp_groups or []) if cafe else "",
            "fastpos_tenant": cafe.fastpos_tenant if cafe else "", "fastpos_token": cafe.fastpos_token if cafe else "",
        })
    return render(request, "ledger/settings/cafe.html", {"form": form, "cafe": cafe})


# ------------------------------------------------------------------ categories

@superuser_required
def categories(request):
    if request.method == "POST":
        action = request.POST.get("action")
        if action == "add":
            form = CategoryForm(request.POST)
            if form.is_valid():
                d = form.cleaned_data
                base = slugify(d["name"])[:36] or "cat"
                slug, n = base, 2
                while Category.objects.filter(slug=slug).exists():
                    slug = f"{base}-{n}"; n += 1
                try:
                    Category.objects.create(name=d["name"], slug=slug, kind=d["kind"], sort=d["sort"] or 100)
                    messages.success(request, f"Added {d['name']}.")
                except IntegrityError:
                    messages.error(request, "A category with that name exists.")
        elif action in ("rename", "toggle"):
            c = get_object_or_404(Category, pk=request.POST.get("pk"))
            if action == "toggle":
                c.is_active = not c.is_active
                c.save(update_fields=["is_active"])
                messages.info(request, f"{c.name} {'shown' if c.is_active else 'hidden'}.")
            else:
                new = (request.POST.get("name") or "").strip()
                if new:
                    c.name = new
                    try:
                        c.save(update_fields=["name"]); messages.success(request, "Renamed.")
                    except IntegrityError:
                        messages.error(request, "A category with that name exists.")
        return redirect("ledger:settings_categories")
    return render(request, "ledger/settings/categories.html", {
        "categories": Category.objects.all(), "form": CategoryForm(),
        "used": set(LedgerEntry.objects.values_list("category_id", flat=True).distinct()),
    })


@superuser_required
def person_remove(request, pk):
    """Remove a person record that was made by mistake (duplicate, typo). Only
    allowed when it has no login and nothing in the books points at it — money
    history is never deleted."""
    from django.db.models import Q
    from .models import Invoice, PayrollPeriod, ShareEvent
    p = get_object_or_404(Person, pk=pk)
    if request.method != "POST":
        return redirect("ledger:settings_person", pk=pk)
    used = (LedgerEntry.objects.filter(Q(paid_by=p) | Q(funded_by=p) | Q(counterparty=p) | Q(created_by=p) | Q(confirmed_by=p)).exists()
            or ShareEvent.objects.filter(shared_by=p).exists()
            or PayrollPeriod.objects.filter(staff=p).exists()
            or Invoice.objects.filter(created_by=p).exists())
    if p.user_id:
        messages.error(request, f"{p.name} has a login — clear the username first, or keep them.")
    elif used:
        messages.error(request, f"{p.name} appears in the books, so can't be removed. Untick 'Can sign in' instead.")
    else:
        name = p.name; p.delete()
        messages.success(request, f"Removed {name}.")
        return redirect("ledger:settings")
    return redirect("ledger:settings_person", pk=pk)
