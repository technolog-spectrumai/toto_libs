from django.contrib import admin

from .models import TransactionFee, TransactionFeePolicy


@admin.register(TransactionFeePolicy)
class TransactionFeePolicyAdmin(admin.ModelAdmin):
    list_display = ("name", "asset", "fee_rate_bps", "fixed_amount_base_units", "scope", "active")
    list_filter = ("active", "scope", "asset")
    search_fields = ("name", "description", "asset__unit_name")
    raw_id_fields = ("asset", "receiving_account")


@admin.register(TransactionFee)
class TransactionFeeAdmin(admin.ModelAdmin):
    list_display = ("policy", "asset", "amount_base_units", "transaction_amount_base_units", "created_at")
    list_filter = ("asset", "policy")
    search_fields = ("asset__unit_name", "policy__name")
    raw_id_fields = ("policy", "transaction", "asset")
    readonly_fields = ("created_at",)
