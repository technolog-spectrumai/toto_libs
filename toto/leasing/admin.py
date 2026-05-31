from django.contrib import admin

from .models import Lease


@admin.register(Lease)
class LeaseAdmin(admin.ModelAdmin):
    list_display = [
        "pk", "leased_object", "lessor", "lessee",
        "status", "billing_period", "starts_at", "next_billing_at", "created_at",
    ]
    list_filter = ["status", "billing_period"]
    search_fields = ["leased_object__name", "lessor__display_name", "lessee__display_name"]
    readonly_fields = ["created_at", "updated_at", "activated_at", "cancelled_at"]
    raw_id_fields = ["leased_object", "lessor", "lessee", "contract",
                     "lessor_account", "lessee_account", "payment_asset"]
