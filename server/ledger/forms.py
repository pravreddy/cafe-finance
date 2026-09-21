from decimal import Decimal

from django import forms

from .models import Cafe, Category, Invoice, LedgerEntry, PayrollPeriod, Person


class ReviewForm(forms.Form):
    """One form for the review screen. Which fields matter depends on doc_type;
    the view only reads the relevant ones."""

    doc_type = forms.ChoiceField(choices=[("payment", "Payment"), ("invoice", "Invoice"), ("other", "Not money")])
    cafe = forms.ModelChoiceField(queryset=Cafe.objects.filter(is_active=True), required=False)
    category = forms.ModelChoiceField(queryset=Category.objects.filter(is_active=True), required=False)
    description = forms.CharField(max_length=300, required=False)

    # payment
    amount = forms.DecimalField(max_digits=12, decimal_places=2, required=False)
    paid_on = forms.DateField(required=False)
    paid_by = forms.ModelChoiceField(queryset=Person.objects.all(), required=False)
    payee_name = forms.CharField(max_length=120, required=False)
    upi_app = forms.ChoiceField(choices=LedgerEntry.UpiApp.choices, required=False)
    utr = forms.CharField(max_length=40, required=False)
    invoice = forms.ModelChoiceField(queryset=Invoice.objects.exclude(status=Invoice.Status.REJECTED),
                                     required=False)
    # whose money: "" = same as paid_by, "cafe" = the cafe's own till, else Person pk
    funded_by = forms.CharField(required=False)
    payroll = forms.ModelChoiceField(queryset=PayrollPeriod.objects.exclude(status=PayrollPeriod.Status.PAID),
                                     required=False)
    retyped = forms.BooleanField(required=False)   # low-confidence guard (9.9)

    # invoice
    vendor_name = forms.CharField(max_length=120, required=False)
    invoice_number = forms.CharField(max_length=60, required=False)
    invoice_date = forms.DateField(required=False)
    due_date = forms.DateField(required=False)
    total = forms.DecimalField(max_digits=12, decimal_places=2, required=False)

    def clean(self):
        d = super().clean()
        if d.get("doc_type") == "payment":
            if not d.get("amount"):
                self.add_error("amount", "Amount is needed for a payment.")
            if not d.get("paid_on"):
                self.add_error("paid_on", "Payment date is needed.")
        if d.get("doc_type") == "invoice" and not d.get("total"):
            self.add_error("total", "Invoice total is needed.")
        return d


class UploadForm(forms.Form):
    files = forms.FileField(widget=forms.ClearableFileInput(attrs={"multiple": False}))
    note = forms.CharField(max_length=300, required=False)
    channel = forms.ChoiceField(choices=[("app_share", "I paid this"), ("wa_forward", "Forwarded from WhatsApp")],
                                initial="app_share")


class ManualEntryForm(forms.Form):
    """Type it in (9.1 cash, 9.4 settle, 9.6 income). Proof optional."""
    kind = forms.ChoiceField(choices=LedgerEntry.Kind.choices, initial="expense")
    cafe = forms.ModelChoiceField(queryset=Cafe.objects.filter(is_active=True))
    amount = forms.DecimalField(max_digits=12, decimal_places=2, min_value=Decimal("0.01"))
    paid_on = forms.DateField()
    category = forms.ModelChoiceField(queryset=Category.objects.filter(is_active=True), required=False)
    paid_by = forms.ModelChoiceField(queryset=Person.objects.all(), required=False)
    funded_by = forms.CharField(required=False)          # "" same as paid_by | "cafe" | pk
    counterparty = forms.ModelChoiceField(queryset=Person.objects.all(), required=False)
    income_source = forms.ChoiceField(choices=[("", "—")] + list(LedgerEntry.IncomeSource.choices), required=False)
    payee_name = forms.CharField(max_length=120, required=False)
    description = forms.CharField(max_length=300, required=False)
    upi_app = forms.ChoiceField(choices=LedgerEntry.UpiApp.choices, initial="cash", required=False)
    photo = forms.FileField(required=False)

    def clean(self):
        d = super().clean()
        k = d.get("kind")
        if k == "income" and not d.get("income_source"):
            self.add_error("income_source", "Where did the income come from?")
        if k == "transfer" and not (d.get("funded_by") or d.get("counterparty")):
            self.add_error("counterparty", "A transfer needs who gave and/or who received.")
        if k == "expense" and not d.get("category"):
            self.add_error("category", "Pick a category.")
        return d
