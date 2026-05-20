from django.contrib import admin

from .models import AssetExchangeRequest


@admin.register(AssetExchangeRequest)
class AssetExchangeRequestAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "requester",
        "counterparty",
        "offer_asset",
        "offer_amount_display",
        "request_asset",
        "request_amount_display",
        "status",
        "created_at",
    )
    list_filter = ("status", "offer_asset", "request_asset", "created_at")
    search_fields = ("requester__username", "counterparty__username", "note", "response_note")
    readonly_fields = (
        "created_at",
        "updated_at",
        "responded_at",
        "offer_tx_reference",
        "request_tx_reference",
        "commission_tx_reference",
        "offer_amount_display",
        "request_amount_display",
        "commission_amount_display",
    )
    raw_id_fields = (
        "requester",
        "counterparty",
        "requester_account",
        "counterparty_account",
        "offer_asset",
        "request_asset",
    )
