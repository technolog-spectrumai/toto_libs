from django.contrib import admin

from toto.quota.admin import QuotaPolicyAdminBase, UsageEventAdminBase

from .models import (GiteaAccount, GiteaForgeSample, GiteaQuotaPolicy,
                     GiteaUsageEvent)


@admin.register(GiteaAccount)
class GiteaAccountAdmin(admin.ModelAdmin):
    list_display = ("user", "username", "storage_bytes",
                    "storage_cap_gb", "repo_creation_blocked",
                    "storage_sampled_at")
    list_filter = ("repo_creation_blocked",)
    search_fields = ("user__username", "username")
    # The token is Fernet ciphertext; showing or editing it helps nobody.
    exclude = ("token_encrypted",)
    readonly_fields = ("storage_bytes", "storage_sampled_at",
                       "repo_creation_blocked")


@admin.register(GiteaForgeSample)
class GiteaForgeSampleAdmin(admin.ModelAdmin):
    list_display = ("sampled_at", "total_bytes", "unattributed_bytes")


@admin.register(GiteaQuotaPolicy)
class GiteaQuotaPolicyAdmin(QuotaPolicyAdminBase):
    pass


@admin.register(GiteaUsageEvent)
class GiteaUsageEventAdmin(UsageEventAdminBase):
    pass
