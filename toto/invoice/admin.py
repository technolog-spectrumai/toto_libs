from django.contrib import admin
from django.utils import timezone

from .models import BillingCycle, Invoice, InvoiceStatus, PaymentSettlement


class PaymentSettlementInline(admin.TabularInline):
    model = PaymentSettlement
    extra = 0
    readonly_fields = ("created_at",)


@admin.register(BillingCycle)
class BillingCycleAdmin(admin.ModelAdmin):
    list_display = ("name", "frequency", "active", "created_at")
    list_filter = ("frequency", "active")
    search_fields = ("name", "description")


@admin.register(Invoice)
class InvoiceAdmin(admin.ModelAdmin):
    list_display = ("pk", "issued_to", "title", "amount", "currency_label", "status", "bucket", "billing_cycle", "due_date", "paid_at", "created_at")
    list_filter = ("status", "currency_label")
    search_fields = ("issued_to__username", "title", "description", "notes", "obligation_reference")
    raw_id_fields = ("issued_to", "issued_by")
    readonly_fields = ("created_at", "updated_at", "paid_at")
    inlines = [PaymentSettlementInline]
    actions = ["mark_paid", "mark_cancelled"]

    @admin.action(description="Mark selected invoices as Paid")
    def mark_paid(self, request, queryset):
        now = timezone.now()
        updated = queryset.exclude(status="paid").update(status=InvoiceStatus.PAID, paid_at=now)
        self.message_user(request, f"{updated} invoice(s) marked as paid.")

    @admin.action(description="Mark selected invoices as Cancelled")
    def mark_cancelled(self, request, queryset):
        updated = queryset.exclude(status="cancelled").update(status=InvoiceStatus.CANCELLED)
        self.message_user(request, f"{updated} invoice(s) cancelled.")


@admin.register(PaymentSettlement)
class PaymentSettlementAdmin(admin.ModelAdmin):
    list_display = ("pk", "invoice", "amount", "currency_label", "method", "settled_at", "created_at")
    search_fields = ("reference", "notes", "invoice__title")
    readonly_fields = ("created_at",)
