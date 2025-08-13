from django.contrib import admin, messages
from django.utils.html import format_html
from .models import TLSCertificate, ExternalCertificate


@admin.register(TLSCertificate)
class TLSCertificateAdmin(admin.ModelAdmin):
    list_display = (
        'common_name',
        'organization_name',
        'country_name',
        'state_or_province_name',
        'locality_name',
        'issued_at',
        'expires_at',
        'certificate_status',
        'qr_code_preview',
    )
    search_fields = ('common_name', 'organization_name', 'locality_name')
    list_filter = ('country_name', 'state_or_province_name', 'expires_at')
    ordering = ('organization_name',)
    readonly_fields = ('certificate', 'qr_code_preview', 'qr_code', 'issued_at', 'expires_at')
    exclude = ('private_key',)

    fieldsets = (
        ("Certificate Details", {
            "fields": (
                "common_name",
                "organization_name",
                "certificate",
                "qr_code_preview",
                "qr_code",
            ),
        }),
        ("Location Information", {
            "fields": (
                "country_name",
                "state_or_province_name",
                "locality_name",
            ),
        }),
        ("Validity", {
            "fields": ("issued_at", "expires_at"),
        }),
    )

    def qr_code_preview(self, obj):
        if obj.qr_code:
            return format_html(
                '<img src="{}" width="128" height="128" style="border:1px solid #ccc;" alt="QR Code" />',
                obj.qr_code.url
            )
        return "No QR Code Available"

    qr_code_preview.short_description = "QR Code Preview"

    def certificate_status(self, obj):
        if obj.certificate:
            return format_html('<span style="color:green;">Present</span>')
        return format_html('<span style="color:red;">Missing</span>')

    certificate_status.short_description = "Certificate Status"


@admin.register(ExternalCertificate)
class ExternalCertificateAdmin(admin.ModelAdmin):
    list_display = (
        'domain',
        'email',
        'auth_url',
        'issued_at',
        'expires_at',
        'success',
        'certificate_status',
        'debug_mode',
    )
    search_fields = ('domain', 'email')
    list_filter = ('success', 'expires_at', 'debug')
    ordering = ('domain',)
    readonly_fields = ('issued_at', 'expires_at', 'cert_path', 'key_path', 'success')

    fieldsets = (
        ("Domain Info", {
            "fields": ("domain", "email", "auth_url", "debug"),
        }),
        ("Certificate Status", {
            "fields": ("success", "cert_path", "key_path"),
        }),
        ("Validity", {
            "fields": ("issued_at", "expires_at"),
        }),
    )

    actions = ['issue_certificate']

    def certificate_status(self, obj):
        if obj.success:
            return format_html('<span style="color:green;">Issued</span>')
        return format_html('<span style="color:red;">Failed</span>')
    certificate_status.short_description = "Certificate Status"

    def debug_mode(self, obj):
        if obj.debug:
            return format_html('<span style="color:orange;">Dry Run</span>')
        return format_html('<span style="color:gray;">Live</span>')
    debug_mode.short_description = "Mode"

    def issue_certificate(self, request, queryset):
        for cert in queryset:
            cert.issue_certificate()
            if cert.success:
                mode = "dry run" if cert.debug else "real"
                self.message_user(request, f"Certificate ({mode}) issued for {cert.domain}", messages.SUCCESS)
            else:
                self.message_user(request, f"Failed to issue certificate for {cert.domain}", messages.ERROR)

    issue_certificate.short_description = "Issue certificate for selected domains"




