from django.contrib import admin

from .models import EmailService


@admin.register(EmailService)
class EmailServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "email_address", "host", "port", "use_tls", "use_ssl", "created_at")
    search_fields = ("name", "email_address", "host")
    readonly_fields = ("id", "created_at")
