"""Configuring the assistant, and never showing its key.

The provider form carries a write-only key field that is NOT a model field, so
nothing round-trips; ``secret`` is read-only so nobody can point a provider at
another app's secret (which the AEAD would refuse at read time, confusingly);
and the "test this" action names its row explicitly so a provider can be proven
BEFORE it is switched on. All three come from ``jess/admin.py``, which is the
only place in the tree that had already solved this.
"""

from django import forms
from django.contrib import admin, messages
from django.utils.translation import gettext, gettext_lazy as _

from . import services
from .models import AiAgent, AiProvider, AiRun, StevenQuotaPolicy
from .vault import VaultUnavailable


class AiProviderForm(forms.ModelForm):
    """The provider form, plus one field that is not on the model."""

    new_api_key = forms.CharField(
        required=False,
        widget=forms.PasswordInput(render_value=False,
                                   attrs={"autocomplete": "new-password"}),
        label=_("Set / replace API key"),
        help_text=_("Leave blank to keep the current one. Stored encrypted in "
                    "Steven's vault and never displayed again — not even here."),
    )

    class Meta:
        model = AiProvider
        fields = ("label", "base_url", "model", "temperature",
                  "max_output_tokens", "timeout", "active")


@admin.register(AiProvider)
class AiProviderAdmin(admin.ModelAdmin):
    form = AiProviderForm
    list_display = ("label", "model", "base_url", "active", "secret_status",
                    "updated_at")
    list_filter = ("active",)
    search_fields = ("label", "model", "base_url")
    # `secret` is managed only through the write-only field: an editable FK would
    # let somebody point a provider at another app's secret, which fails at read
    # time as an opaque cryptographic error.
    readonly_fields = ("secret", "secret_status", "created_at", "updated_at")
    actions = ["test_provider"]

    def save_model(self, request, obj, form, change):
        # Row first, then key: store_api_key needs the pk. The sequence itself
        # (store → repoint → retire → audit) lives in services.store_api_key,
        # shared with the settings page so the two doors cannot drift.
        super().save_model(request, obj, form, change)
        new_value = (form.cleaned_data.get("new_api_key") or "").strip()
        if not new_value:
            return
        try:
            services.store_api_key(obj, new_value, actor=request.user)
            messages.success(request, gettext("API key stored, encrypted."))
        except VaultUnavailable as exc:
            messages.error(request, f"Vault unavailable — the key was NOT changed: {exc}")
        except Exception as exc:  # noqa: BLE001 — surface it, never echo the value
            messages.error(request, f"Could not store the key: {exc}")

    @admin.display(description=_("API key"))
    def secret_status(self, obj):
        secret = getattr(obj, "secret", None)
        if not secret:
            return "— none"
        rotated = f" · rotated {secret.rotated_at:%Y-%m-%d}" if secret.rotated_at else ""
        return f"set · {secret.state}{rotated}"

    @admin.action(description=_("Test this provider"))
    def test_provider(self, request, queryset):
        """Prove a provider works BEFORE switching it on.

        Names the selected row explicitly rather than using whichever is active,
        because proving-then-switching is the sequence an operator actually
        wants. Synchronous on purpose: the point is an immediate pass or fail,
        independent of whether a worker is running.
        """
        if queryset.count() != 1:
            messages.error(request, gettext("Select exactly one provider."))
            return
        provider = queryset.first()

        from .client import ProviderError

        try:
            probe = services.probe_provider(provider)
        except services.NotConfigured as exc:
            messages.error(request, str(exc))
            return
        except VaultUnavailable as exc:
            messages.error(request, f"Vault: {exc}")
            return
        except ProviderError as exc:
            messages.error(request, f"{provider.label} did not answer: {exc}")
            return

        messages.success(
            request,
            f"{provider.label} answered \"{probe['text']}\" "
            f"as {probe['model']} ({probe['tokens']} tokens).")


@admin.register(AiAgent)
class AiAgentAdmin(admin.ModelAdmin):
    """Registered so a second persona can be drafted beside the live one.

    The DAY-TO-DAY surface is ``steven:manage``, which shows the assembled
    system message beside the boxes that build it — this page cannot, so it is
    for the one thing it does better: having more than one row.
    """

    list_display = ("name", "active", "language", "updated_at")
    list_filter = ("active",)
    search_fields = ("name", "persona", "house_rules")
    fieldsets = (
        (_("Identity"), {"fields": ("name", "icon", "tagline", "description",
                                    "active")}),
        (_("Prompt"), {"fields": ("persona", "language", "house_rules",
                                  "kind_notes")}),
    )
    readonly_fields = ("created_at", "updated_at")


@admin.register(AiRun)
class AiRunAdmin(admin.ModelAdmin):
    list_display = ("owner", "surface", "action", "status", "total_tokens",
                    "duration_ms", "agent_label", "model_used", "created_at")
    list_filter = ("status", "surface", "action")
    search_fields = ("owner__username", "surface", "action")
    # Everything: a run is a record of what happened, and an editable one is not
    # a record. Charges are keyed on its pk, so an edited row would misprice.
    readonly_fields = [f.name for f in AiRun._meta.fields]

    def has_add_permission(self, request):
        return False


@admin.register(StevenQuotaPolicy)
class StevenQuotaPolicyAdmin(admin.ModelAdmin):
    list_display = ("metric_code", "limit", "period", "mode", "active")
