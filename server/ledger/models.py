"""cafe-finance ledger.

Principle (README): we cannot make WhatsApp work as per our requirement; we
make our app work as per what we want. So every rule lives here.

Two kinds of document reach us, from anywhere (app share, WhatsApp forward,
email, reconciler):

  * a PAYMENT proof  (UPI screenshot)          -> LedgerEntry  (money moved)
  * an INVOICE       (PDF from mail, JPG photo)-> Invoice      (money owed)

They are different things and are stored separately. An Invoice is settled by
one or more LedgerEntries (installments — "2nd installment for painting").

Shared many times, counted once: every arrival is a ShareEvent; all of them
hang off ONE entry / invoice, matched by payment id, file hash, #CF tag, or
amount+date+comment (see dedup.py). A duplicate is never an error.
"""
from decimal import Decimal

from django.conf import settings
from django.db import models
from django.db.models import Sum
from django.utils import timezone


class Cafe(models.Model):
    name = models.CharField(max_length=80, unique=True)
    slug = models.SlugField(max_length=40, unique=True)
    # FastPOS tenant slug, for pulling daily sales into the income side.
    fastpos_tenant = models.CharField(max_length=60, blank=True)
    # WhatsApp group names whose posts belong to this cafe (reconciler).
    whatsapp_groups = models.JSONField(default=list, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Person(models.Model):
    """Someone who pays for the cafe and shares proofs. Partners and staff.
    Linked to a login when they use the app; reconciler/mail rows may carry a
    Person with no login (matched by phone / WhatsApp sender)."""

    class Role(models.TextChoices):
        PARTNER = "partner", "Partner"
        STAFF = "staff", "Staff"
        OTHER = "other", "Other"

    user = models.OneToOneField(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="person")
    name = models.CharField(max_length=80)
    phone = models.CharField(max_length=20, blank=True)       # +91…
    whatsapp_jid = models.CharField(max_length=60, blank=True)  # 9198…@s.whatsapp.net
    role = models.CharField(max_length=10, choices=Role.choices, default=Role.PARTNER)
    default_cafe = models.ForeignKey(Cafe, null=True, blank=True, on_delete=models.SET_NULL)
    # Confirmers decide what reaches the reports.
    can_confirm = models.BooleanField(default=False)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Category(models.Model):
    class Kind(models.TextChoices):
        EXPENSE = "expense", "Expense"
        INCOME = "income", "Income"
        TRANSFER = "transfer", "Transfer (partner money in / out)"

    name = models.CharField(max_length=60, unique=True)
    slug = models.SlugField(max_length=40, unique=True)
    kind = models.CharField(max_length=10, choices=Kind.choices, default=Kind.EXPENSE)
    parent = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                               related_name="children")
    sort = models.PositiveSmallIntegerField(default=100)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort", "name"]
        verbose_name_plural = "categories"

    def __str__(self):
        return self.name


class Invoice(models.Model):
    """Money OWED to a vendor. Arrives as a PDF (email) or a photo (WhatsApp).
    Paid off by LedgerEntries; nothing here is a payment."""

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        OPEN = "open", "Open (unpaid)"
        PART_PAID = "part_paid", "Part paid"
        PAID = "paid", "Paid"
        REJECTED = "rejected", "Rejected"

    cafe = models.ForeignKey(Cafe, null=True, blank=True, on_delete=models.PROTECT)
    vendor_name = models.CharField(max_length=120, blank=True)
    vendor_gstin = models.CharField(max_length=20, blank=True)
    invoice_number = models.CharField(max_length=60, blank=True)
    invoice_date = models.DateField(null=True, blank=True)
    due_date = models.DateField(null=True, blank=True)
    subtotal = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    tax = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    total = models.DecimalField(max_digits=12, decimal_places=2, null=True, blank=True)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    description = models.CharField(max_length=300, blank=True)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    extracted_json = models.JSONField(default=dict, blank=True)
    created_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="invoices_created")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-invoice_date", "-created_at"]
        constraints = [
            # Same vendor + same invoice number = same invoice. Blank numbers
            # are allowed to repeat (a photo of a handwritten bill).
            models.UniqueConstraint(
                fields=["vendor_name", "invoice_number"],
                condition=~models.Q(invoice_number=""),
                name="uniq_vendor_invoice_number",
            ),
        ]

    def __str__(self):
        return f"{self.vendor_name or '?'} {self.invoice_number or ''} ₹{self.total or '?'}"

    @property
    def paid_total(self):
        return (self.payments.filter(status=LedgerEntry.Status.CONFIRMED)
                .aggregate(s=Sum("amount"))["s"] or Decimal("0"))

    @property
    def outstanding(self):
        if self.total is None:
            return None
        return self.total - self.paid_total

    def refresh_status(self, save=True):
        if self.status in (self.Status.PENDING, self.Status.REJECTED):
            return self.status
        paid = self.paid_total
        if self.total is not None and paid >= self.total:
            self.status = self.Status.PAID
        elif paid > 0:
            self.status = self.Status.PART_PAID
        else:
            self.status = self.Status.OPEN
        if save:
            self.save(update_fields=["status"])
        return self.status


class LedgerEntry(models.Model):
    """Money MOVED. One row per real payment, however many times its proof was
    shared. Reports only ever read status=confirmed rows."""

    class Source(models.TextChoices):
        APP = "app", "Cafe Finance app"
        WHATSAPP = "whatsapp", "WhatsApp (forward / reconciler)"
        EMAIL = "email", "Email"
        MANUAL = "manual", "Typed in"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending review"
        CONFIRMED = "confirmed", "Confirmed"
        REJECTED = "rejected", "Rejected"
        IMPORTED = "imported", "Imported from history — needs review"

    class UpiApp(models.TextChoices):
        PHONEPE = "phonepe", "PhonePe"
        GPAY = "gpay", "Google Pay"
        PAYTM = "paytm", "Paytm"
        BHIM = "bhim", "BHIM / bank app"
        BANK = "bank", "Bank transfer (NEFT/IMPS)"
        CASH = "cash", "Cash"
        OTHER = "other", "Other"

    cafe = models.ForeignKey(Cafe, null=True, blank=True, on_delete=models.PROTECT)
    # Date the money moved (from the receipt). Share dates live on ShareEvent.
    paid_on = models.DateField()
    paid_at = models.DateTimeField(null=True, blank=True)
    amount = models.DecimalField(max_digits=12, decimal_places=2)
    category = models.ForeignKey(Category, null=True, blank=True, on_delete=models.SET_NULL)
    paid_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="payments_made")
    payee_name = models.CharField(max_length=120, blank=True)
    payee_phone_masked = models.CharField(max_length=30, blank=True)
    description = models.CharField(max_length=300, blank=True)   # the comment
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="payments")

    source = models.CharField(max_length=10, choices=Source.choices, default=Source.APP)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)

    # Payment identity — the dedup keys. UTR is unique across Indian banking.
    utr = models.CharField(max_length=40, blank=True, null=True, unique=True)
    txn_id = models.CharField(max_length=60, blank=True)          # app-level id (T2609…)
    upi_app = models.CharField(max_length=10, choices=UpiApp.choices, default=UpiApp.OTHER)
    debited_from_last4 = models.CharField(max_length=8, blank=True)
    confidence = models.FloatField(default=0)

    # #CF-000123 — goes into the WhatsApp caption; reconciler matches on it.
    cf_tag = models.CharField(max_length=12, unique=True, blank=True, null=True)

    # Corrections: never edit a confirmed row; add a reversal + a new row.
    reversal_of = models.ForeignKey("self", null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name="reversals")

    created_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL,
                                   related_name="entries_created")
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL,
                                     related_name="entries_confirmed")
    confirmed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-paid_on", "-created_at"]
        verbose_name_plural = "ledger entries"
        indexes = [
            models.Index(fields=["txn_id"]),
            models.Index(fields=["paid_on", "amount"]),
        ]

    def __str__(self):
        return f"{self.paid_on} ₹{self.amount} {self.payee_name} ({self.get_status_display()})"

    def save(self, *args, **kwargs):
        # Normalise empties so the unique constraints behave (NULL != NULL).
        self.utr = (self.utr or "").strip() or None
        self.cf_tag = (self.cf_tag or "").strip() or None
        super().save(*args, **kwargs)
        if not self.cf_tag:
            self.cf_tag = f"#CF-{self.pk:06d}"
            super().save(update_fields=["cf_tag"])

    @property
    def is_counted(self):
        return self.status == self.Status.CONFIRMED

    def confirm(self, by: "Person | None"):
        self.status = self.Status.CONFIRMED
        self.confirmed_by = by
        self.confirmed_at = timezone.now()
        self.save(update_fields=["status", "confirmed_by", "confirmed_at"])
        if self.invoice_id:
            self.invoice.refresh_status()

    def whatsapp_caption(self):
        from django.conf import settings as s
        return s.WHATSAPP_CAPTION_TEMPLATE.format(
            amount=f"{self.amount:,.0f}" if self.amount == self.amount.to_integral() else f"{self.amount:,.2f}",
            category=self.category.name if self.category else "Uncategorised",
            description=self.description or self.payee_name or "",
            tag=self.cf_tag or "",
        )


def proof_upload_to(instance, filename):
    d = timezone.now()
    return f"proofs/{d:%Y/%m}/{instance.sha256[:2]}/{instance.sha256}_{filename[-60:]}"


class Proof(models.Model):
    """A file that reached us. Deduplicated by sha256 — the same screenshot
    forwarded five times is one Proof with five ShareEvents."""

    class Kind(models.TextChoices):
        PAYMENT_SCREENSHOT = "payment", "Payment screenshot"
        INVOICE_PDF = "invoice_pdf", "Invoice (PDF)"
        INVOICE_IMAGE = "invoice_img", "Invoice (photo)"
        OTHER = "other", "Other / unknown"

    class Origin(models.TextChoices):
        APP_SHARE = "app_share", "Shared to the app"
        WHATSAPP_FORWARD = "wa_forward", "Forwarded from WhatsApp to the app"
        WHATSAPP_GROUP = "wa_group", "Read from the WhatsApp group (reconciler)"
        EMAIL = "email", "Email"
        UPLOAD = "upload", "Uploaded in the browser"

    file = models.FileField(upload_to=proof_upload_to)
    original_name = models.CharField(max_length=200, blank=True)
    mime = models.CharField(max_length=80, blank=True)
    sha256 = models.CharField(max_length=64, unique=True)
    kind = models.CharField(max_length=12, choices=Kind.choices, default=Kind.OTHER)
    origin = models.CharField(max_length=12, choices=Origin.choices, default=Origin.APP_SHARE)
    origin_ref = models.CharField(max_length=200, blank=True)  # wa message pk / mail Message-ID
    entry = models.ForeignKey(LedgerEntry, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="proofs")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="proofs")
    extracted_json = models.JSONField(default=dict, blank=True)
    extracted_at = models.DateTimeField(null=True, blank=True)
    extraction_error = models.CharField(max_length=300, blank=True)

    # Queue state. Intake stores the file and sets QUEUED; the worker
    # (manage.py process_queue) reads it with the document AI, however long
    # that takes, then links every waiting ShareEvent. Nothing blocks on it.
    class State(models.TextChoices):
        QUEUED = "queued", "Waiting to be read"
        PROCESSING = "processing", "Being read"
        DONE = "done", "Read"
        FAILED = "failed", "Could not be read"

    state = models.CharField(max_length=10, choices=State.choices, default=State.QUEUED, db_index=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    next_attempt_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.get_kind_display()} {self.sha256[:8]} ({self.state})"

    @property
    def is_pdf(self):
        return self.mime == "application/pdf" or self.original_name.lower().endswith(".pdf")


class ShareEvent(models.Model):
    """One row per time a proof reached us — who, from where, when. Never
    merged, never counted. This is the 'shared on this day by this person'
    trail the partners asked for."""

    class Channel(models.TextChoices):
        APP_SHARE = "app_share", "Pay → share to app"
        WHATSAPP_FORWARD = "wa_forward", "Forwarded from WhatsApp"
        WHATSAPP_GROUP = "wa_group", "Seen in WhatsApp group"
        EMAIL = "email", "Email"
        MANUAL = "manual", "Manual"

    proof = models.ForeignKey(Proof, on_delete=models.CASCADE, related_name="shares")
    entry = models.ForeignKey(LedgerEntry, null=True, blank=True, on_delete=models.SET_NULL,
                              related_name="shares")
    invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL,
                                related_name="shares")
    shared_by = models.ForeignKey(Person, null=True, blank=True, on_delete=models.SET_NULL)
    shared_by_label = models.CharField(max_length=120, blank=True)  # raw sender when no Person
    shared_at = models.DateTimeField(default=timezone.now)
    channel = models.CharField(max_length=12, choices=Channel.choices)
    note = models.CharField(max_length=300, blank=True)   # caption / text that came with it
    was_duplicate = models.BooleanField(default=False)
    match_method = models.CharField(max_length=20, blank=True)  # utr / txn_id / sha256 / cf_tag / amount_date_comment / new / pending
    # What intake knew that the worker needs later: {"cafe_id": 1, "imported": true}
    context = models.JSONField(default=dict, blank=True)

    class Meta:
        ordering = ["-shared_at"]

    def __str__(self):
        return f"{self.get_channel_display()} by {self.shared_by or self.shared_by_label} at {self.shared_at:%Y-%m-%d %H:%M}"


class GroupPost(models.Model):
    """A message the reconciler saw in a WhatsApp group. Matched to a ledger
    row when it can be; otherwise it sits in the queue as 'seen in group, not
    in ledger'."""

    group_name = models.CharField(max_length=120)
    wa_message_pk = models.CharField(max_length=60)   # ZWAMESSAGE.Z_PK (or stable id)
    sender = models.CharField(max_length=120, blank=True)
    sent_at = models.DateTimeField()
    caption = models.TextField(blank=True)
    media_path = models.CharField(max_length=400, blank=True)
    proof = models.ForeignKey(Proof, null=True, blank=True, on_delete=models.SET_NULL)
    matched_entry = models.ForeignKey(LedgerEntry, null=True, blank=True, on_delete=models.SET_NULL,
                                      related_name="group_posts")
    matched_invoice = models.ForeignKey(Invoice, null=True, blank=True, on_delete=models.SET_NULL,
                                        related_name="group_posts")
    match_method = models.CharField(max_length=20, blank=True)
    dismissed = models.BooleanField(default=False)   # a human said "not money"
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-sent_at"]
        constraints = [
            models.UniqueConstraint(fields=["group_name", "wa_message_pk"], name="uniq_group_message"),
        ]

    def __str__(self):
        return f"[{self.group_name}] {self.sender} {self.sent_at:%d %b %H:%M}"
