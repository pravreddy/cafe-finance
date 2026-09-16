from django.contrib import admin

from .models import Cafe, Category, GroupPost, Invoice, LedgerEntry, Person, Proof, ShareEvent


@admin.register(Cafe)
class CafeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "fastpos_tenant", "is_active")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(Person)
class PersonAdmin(admin.ModelAdmin):
    list_display = ("name", "role", "phone", "default_cafe", "can_confirm", "user")
    list_filter = ("role", "can_confirm")


@admin.register(Category)
class CategoryAdmin(admin.ModelAdmin):
    list_display = ("name", "kind", "parent", "sort", "is_active")
    list_filter = ("kind",)
    prepopulated_fields = {"slug": ("name",)}


class ProofInline(admin.TabularInline):
    model = Proof
    extra = 0
    fields = ("kind", "origin", "origin_ref", "sha256", "created_at")
    readonly_fields = fields


class ShareInline(admin.TabularInline):
    model = ShareEvent
    extra = 0
    fields = ("shared_at", "channel", "shared_by", "shared_by_label", "was_duplicate", "match_method", "note")
    readonly_fields = fields


@admin.register(LedgerEntry)
class LedgerEntryAdmin(admin.ModelAdmin):
    list_display = ("cf_tag", "paid_on", "amount", "payee_name", "category", "cafe", "paid_by", "status", "utr")
    list_filter = ("status", "cafe", "category", "source", "upi_app")
    search_fields = ("cf_tag", "utr", "txn_id", "payee_name", "description")
    date_hierarchy = "paid_on"
    inlines = [ProofInline, ShareInline]
    readonly_fields = ("cf_tag", "confirmed_by", "confirmed_at", "created_at")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("vendor_name", "invoice_number", "invoice_date", "total", "status", "cafe")
    list_filter = ("status", "cafe")
    search_fields = ("vendor_name", "invoice_number", "vendor_gstin")
    inlines = [ProofInline, ShareInline]


@admin.register(Proof)
class ProofAdmin(admin.ModelAdmin):
    list_display = ("id", "kind", "origin", "entry", "invoice", "created_at")
    list_filter = ("kind", "origin")
    readonly_fields = ("sha256", "extracted_json", "extracted_at", "extraction_error")


@admin.register(ShareEvent)
class ShareEventAdmin(admin.ModelAdmin):
    list_display = ("shared_at", "channel", "shared_by", "shared_by_label", "entry", "invoice", "was_duplicate", "match_method")
    list_filter = ("channel", "was_duplicate")


@admin.register(GroupPost)
class GroupPostAdmin(admin.ModelAdmin):
    list_display = ("group_name", "sender", "sent_at", "caption", "matched_entry", "match_method", "dismissed")
    list_filter = ("group_name", "dismissed")
    search_fields = ("caption", "sender")
