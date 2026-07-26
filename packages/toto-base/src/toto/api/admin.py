import uuid

from django import forms
from django.contrib import admin, messages

from .models import Connector, EmailService


def make_connector_secret_form(model_cls):
    """Build a ModelForm for ``model_cls`` with a write-only ``new_secret_value``
    field. Declaring it on the form (not via fields=) is what lets the admin add a
    non-model field cleanly."""
    return type(
        f"{model_cls.__name__}SecretForm",
        (forms.ModelForm,),
        {
            "new_secret_value": forms.CharField(
                required=False,
                widget=forms.PasswordInput(render_value=False, attrs={"autocomplete": "new-password"}),
                label="Set / replace secret value",
                help_text="Leave blank to keep the current value. Stored encrypted in the "
                          "vault; never displayed.",
            ),
            "Meta": type("Meta", (), {"model": model_cls, "fields": "__all__"}),
        },
    )


class ApiConnectorSecretAdminMixin:
    """Admin mixin for any ``ApiConnector`` subclass: securely SET/REPLACE the
    connector's secret value (write-only) and RE-ENCRYPT it (rotate its data key).

    The plaintext is never displayed or stored in the form — it's written
    straight into the Gervazy vault via ``toto.sabbia.vault``. ``api_secret`` is
    read-only; manage it only through the write-only field + the action.
    """

    actions = ["reencrypt_api_secret"]

    def _new_secret_name(self, obj) -> str:
        base = getattr(obj, "slug", "") or getattr(obj, "name", "") or f"{obj._meta.model_name}-{obj.pk}"
        return f"{base}-{uuid.uuid4().hex[:8]}"

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        new_value = (form.cleaned_data.get("new_secret_value") or "").strip()
        if not new_value:
            return
        from toto.sabbia import vault

        try:
            old = obj.api_secret
            secret = vault.store_secret(
                new_value,
                name=self._new_secret_name(obj),
                purpose=f"{obj._meta.model_name}:{obj.pk} api secret",
            )
            obj.api_secret = secret
            obj.save(update_fields=["api_secret"])
            vault.retire_secret(old)
            vault.log_secret_event(
                request.user, "set_connector_secret", secret,
                reason=f"set via admin for {obj._meta.model_name} #{obj.pk}",
            )
            messages.success(request, "Secret value stored (encrypted) and the connector updated.")
        except vault.VaultUnavailable as exc:
            messages.error(request, f"Vault unavailable — secret NOT changed: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface, don't 500 (and never echo the value)
            messages.error(request, f"Could not store the secret: {exc}")

    @admin.action(description="🔒 Re-encrypt secret (rotate data key)")
    def reencrypt_api_secret(self, request, queryset):
        from toto.sabbia import vault

        rotated = skipped = failed = 0
        for obj in queryset:
            old = obj.api_secret
            if old is None:
                skipped += 1
                continue
            try:
                new = vault.reencrypt_secret(old, name=self._new_secret_name(obj))
                obj.api_secret = new
                obj.save(update_fields=["api_secret"])
                vault.retire_secret(old)
                vault.log_secret_event(
                    request.user, "reencrypt_connector_secret", new,
                    reason=f"rotated data key via admin for {obj._meta.model_name} #{obj.pk}",
                )
                rotated += 1
            except vault.VaultUnavailable as exc:
                messages.error(request, f"Vault unavailable: {exc}")
                return
            except Exception as exc:  # noqa: BLE001
                failed += 1
                messages.error(request, f"{obj}: re-encrypt failed: {exc}")
        if rotated:
            messages.success(request, f"Re-encrypted {rotated} secret(s) under a fresh data key.")
        if skipped:
            messages.warning(request, f"Skipped {skipped} connector(s) with no secret set.")

    @admin.display(description="Secret")
    def secret_status(self, obj):
        s = getattr(obj, "api_secret", None)
        if not s:
            return "— none"
        rotated = f" · rotated {s.rotated_at:%Y-%m-%d}" if s.rotated_at else ""
        return f"set · {s.state}{rotated}"


@admin.register(Connector)
class ConnectorAdmin(ApiConnectorSecretAdminMixin, admin.ModelAdmin):
    form = make_connector_secret_form(Connector)
    list_display = ("name", "provider", "auth_type", "secret_status", "signing_key", "is_active", "updated_at")
    list_filter = ("provider", "auth_type", "is_active", "created_at")
    search_fields = ("name", "slug", "base_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("api_secret", "secret_status", "created_at", "updated_at")
    autocomplete_fields = ("signing_key", "owner")
    fieldsets = (
        (None, {
            "fields": ("name", "slug", "provider", "base_url", "is_active", "owner"),
        }),
        ("Authentication", {
            "fields": ("auth_type", "secret_status", "new_secret_value", "signing_key", "auth_config"),
            "description": "Set the API secret via the write-only field; it is encrypted into the "
                           "Gervazy vault and never shown. Use the 'Re-encrypt secret' action to "
                           "rotate its data key.",
        }),
        ("Extra config", {
            "fields": ("extra",),
            "description": "Non-secret provider-specific configuration. Do not store secrets here.",
            "classes": ("collapse",),
        }),
        ("Timestamps", {
            "fields": ("created_at", "updated_at"),
            "classes": ("collapse",),
        }),
    )


@admin.register(EmailService)
class EmailServiceAdmin(admin.ModelAdmin):
    list_display = ("name", "email_address", "host", "port", "use_tls", "use_ssl", "created_at")
    search_fields = ("name", "email_address", "host")
    readonly_fields = ("id", "created_at")
