from django.contrib import admin

from .models import GiteaAccount, GiteaForgeSample


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
    # `unattributed_bytes` counted what the levy could not bill, because it
    # could not map an owner to an account. With the levy gone it is simply
    # org-owned and hand-made-account storage — still worth seeing, since it
    # counts toward the disk, but no longer "unbilled".
    list_display = ("sampled_at", "total_bytes", "unattributed_bytes")

