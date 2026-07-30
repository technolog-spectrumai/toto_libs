"""Jess in the admin: providers are editable, the outbox is not.

The secret half follows ``ApiConnectorSecretAdminMixin`` (``api/admin.py:29-108``) —
a write-only ``PasswordInput``, ``store_secret`` then ``retire_secret`` then
``log_secret_event``, and ``VaultUnavailable`` caught into a message that says the secret
was **not** changed. Same shape, against Jess's own strongbox instead of the sabbia one.

The plaintext is never rendered, never stored on the form, and never recoverable: it goes
straight into the vault and only the ciphertext comes back.
"""
from __future__ import annotations

from django import forms
from django.contrib import admin, messages
from django.urls import NoReverseMatch, reverse
from django.utils.html import format_html

from . import status as jess_status
from . import vault
from .models import EmailProvider, MailMessage


class EmailProviderForm(forms.ModelForm):
    """The provider form, plus one field that is not on the model.

    Declared on the form rather than added via ``fields=`` — the same reason
    ``make_connector_secret_form`` does it that way.
    """

    new_password = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
        label="Set / replace SMTP password",
        help_text="Leave blank to keep the current one. Stored encrypted in Jess's "
                  "vault and never displayed again — not even here.",
    )

    class Meta:
        model = EmailProvider
        fields = "__all__"


@admin.register(EmailProvider)
class EmailProviderAdmin(admin.ModelAdmin):
    form = EmailProviderForm
    list_display = ["label", "backend", "host", "active", "secret_status", "delivery_check"]
    list_filter = ["backend", "active"]
    search_fields = ["label", "host", "username", "from_address"]
    # `secret` is managed only through the write-only field and the rotate action; an
    # editable FK would let someone point a provider at another app's secret, which the
    # AEAD would refuse at read time in a confusing way.
    readonly_fields = ["secret", "created_at", "updated_at", "secret_status"]
    actions = ["reencrypt_password", "send_test_message"]

    fieldsets = (
        (None, {"fields": ("label", "backend", "active")}),
        ("SMTP", {
            "fields": ("host", "port", "use_tls", "use_ssl", "timeout", "username",
                       "new_password", "secret", "secret_status"),
            "description": "Only used when the backend is SMTP. The timeout matters: "
                           "Django's own default is no limit, which would let a worker "
                           "hang on an unresponsive server.",
        }),
        ("Identity", {"fields": ("from_address", "reply_to")}),
        ("Bookkeeping", {"fields": ("created_at", "updated_at")}),
    )

    def _new_secret_name(self, obj) -> str:
        return vault.unique_secret_name(f"jess-{obj.pk or 'new'}")

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        new_value = (form.cleaned_data.get("new_password") or "").strip()
        if not new_value:
            return
        try:
            old = obj.secret
            secret = vault.store_secret(
                new_value,
                name=self._new_secret_name(obj),
                purpose=vault.PURPOSE_SMTP_PASSWORD,
            )
            obj.secret = secret
            obj.save(update_fields=["secret"])
            vault.retire_secret(old)
            vault.log_secret_event(
                request.user, "set_email_password", secret,
                reason=f"set via admin for email provider #{obj.pk}",
            )
            messages.success(request, "SMTP password stored, encrypted.")
        except vault.VaultUnavailable as exc:
            messages.error(request, f"Vault unavailable — the password was NOT changed: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface it, never echo the value
            messages.error(request, f"Could not store the password: {exc}")

    @admin.action(description="🔒 Re-encrypt password (rotate its data key)")
    def reencrypt_password(self, request, queryset):
        rotated = skipped = 0
        for provider in queryset:
            old = provider.secret
            if old is None:
                skipped += 1
                continue
            try:
                new = vault.reencrypt_secret(old, name=self._new_secret_name(provider))
                provider.secret = new
                provider.save(update_fields=["secret"])
                vault.retire_secret(old)
                vault.log_secret_event(
                    request.user, "reencrypt_email_password", new,
                    reason=f"rotated data key via admin for email provider #{provider.pk}",
                )
                rotated += 1
            except vault.VaultUnavailable as exc:
                messages.error(request, f"Vault unavailable: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                messages.error(request, f"{provider}: re-encrypt failed: {exc}")
        if rotated:
            messages.success(request, f"Re-encrypted {rotated} password(s) under a fresh data key.")
        if skipped:
            messages.warning(request, f"Skipped {skipped} provider(s) with no password set.")

    @admin.action(description="✉ Send a test message to my own address")
    def send_test_message(self, request, queryset):
        """Queue a test through the selected provider, explicitly.

        Names the provider on the message rather than relying on whichever is active, so
        a provider can be proven BEFORE it is switched on — which is the sequence an
        operator actually wants.
        """
        if queryset.count() != 1:
            messages.error(request, "Select exactly one provider.")
            return
        provider = queryset.first()
        address = (request.user.email or "").strip()
        if not address:
            messages.error(request, "Your account has no email address to send to.")
            return

        row = MailMessage.objects.create(
            to=[address],
            subject=f"Jess test — {provider.label}",
            body=(
                "This is a test message from Jess.\n\n"
                f"Provider: {provider.label} ({provider.get_backend_display()})\n"
                f"Requested by: {request.user.get_username()}\n"
            ),
            provider=provider,
            provider_label=provider.label,
            purpose=MailMessage.PURPOSE_TEST,
            created_by=request.user,
        )
        from .backend import JessEmailBackend

        JessEmailBackend()._dispatch(row)

        # Reversed rather than written as "/jess/messages/<pk>/": the mount prefix is a
        # per-host decision in each urls.py `_advanced` tuple, so a literal path would be
        # right on both current hosts and wrong on the next one. Guarded because a host
        # can install the app without mounting its urls, and a broken admin action is a
        # worse outcome than a message with no link in it.
        try:
            url = reverse("jess:message_detail", args=[row.pk])
        except NoReverseMatch:
            messages.success(
                request,
                f"Queued a test to {address}. Its outbox row is #{row.pk} "
                "(Jess's pages are not mounted on this host).",
            )
            return
        messages.success(
            request,
            format_html('Queued a test to {}. <a href="{}">Watch it</a>.', address, url),
        )

    @admin.display(description="Password")
    def secret_status(self, obj):
        secret = getattr(obj, "secret", None)
        if not secret:
            return "— none"
        rotated = f" · rotated {secret.rotated_at:%Y-%m-%d}" if secret.rotated_at else ""
        return f"set · {secret.state}{rotated}"

    @admin.display(description="Can deliver?")
    def delivery_check(self, obj):
        """Whether THIS provider looks able to deliver, independent of `active`."""
        if not obj.delivers:
            return "no — backend does not send"
        if obj.needs_secret and not obj.secret_id:
            return "no — username with no password"
        return "yes"

    def changelist_view(self, request, extra_context=None):
        # One honest line about what the platform will actually do right now, on the page
        # where somebody is about to change it.
        messages.info(request, jess_status.describe())
        return super().changelist_view(request, extra_context=extra_context)


@admin.register(MailMessage)
class MailMessageAdmin(admin.ModelAdmin):
    """The outbox, read-only.

    It is an audit trail: editing a delivery record would make it worthless, and adding
    one by hand would not send anything. Same treatment as ``ConnectorRun``
    (``connectors/admin.py:23-45``).
    """

    list_display = ["queued_at", "status", "subject", "recipient_summary", "purpose",
                    "provider_label", "attempts"]
    list_filter = ["status", "purpose"]
    search_fields = ["subject", "to", "error", "provider_label"]
    date_hierarchy = "queued_at"

    def get_readonly_fields(self, request, obj=None):
        return [f.name for f in self.model._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    @admin.display(description="To")
    def recipient_summary(self, obj):
        return ", ".join(obj.to or []) or "—"
