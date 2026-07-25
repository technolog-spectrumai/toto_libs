from django.contrib import admin

from .models import SocialIdentity


@admin.register(SocialIdentity)
class SocialIdentityAdmin(admin.ModelAdmin):
    list_display = ("provider", "subject", "user", "email", "created_at", "last_used_at")
    list_filter = ("provider",)
    search_fields = ("subject", "email", "user__username")
    readonly_fields = ("created_at", "last_used_at")
    raw_id_fields = ("user",)
