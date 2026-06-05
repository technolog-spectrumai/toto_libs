from django import forms
from django.contrib import admin

from .models import AgentConnector, AgentProfile, AgentRun, AgentTool


class AgentProfileForm(forms.ModelForm):
    class Meta:
        model = AgentProfile
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from toto.vicuna.chat import installed_ollama_chat_models, ollama_chat_model_choices

        installed = installed_ollama_chat_models()
        current = self.instance.model_name if self.instance.pk else None

        choices = [(m, m) for m in installed]
        if current and current not in installed:
            choices.insert(0, (current, f"{current} — not pulled"))

        self.fields["model_name"].widget = forms.Select(choices=choices)
        missing = len(ollama_chat_model_choices()) - len(installed)
        self.fields["model_name"].help_text = (
            f"Showing {len(installed)} installed model(s). "
            + (f"Run 'manage.py vicuna_pull_chat' to pull {missing} more." if missing else "")
        ).strip()

    def clean(self):
        cleaned = super().clean()
        connector = cleaned.get("connector")
        model_name = (cleaned.get("model_name") or "").strip()
        if connector and getattr(connector, "provider", None) == "ollama" and model_name:
            from toto.vicuna.chat import installed_ollama_chat_models
            installed = installed_ollama_chat_models()
            if model_name not in installed:
                self.add_error(
                    "model_name",
                    f"{model_name!r} is not installed. "
                    f"Installed: {', '.join(installed) or 'none'}. "
                    "Run 'manage.py vicuna_pull_chat' to pull it.",
                )
        return cleaned


class AgentToolInline(admin.TabularInline):
    model = AgentTool
    extra = 1


@admin.register(AgentProfile)
class AgentProfileAdmin(admin.ModelAdmin):
    form = AgentProfileForm
    list_display = (
        "name",
        "slug",
        "user",
        "model_name",
        "connector",
        "temperature",
        "is_active",
        "updated_at",
    )
    list_filter = (
        "is_active",
        "model_name",
        "connector",
    )
    search_fields = ("name", "slug", "user__username", "description", "system_prompt")
    prepopulated_fields = {"slug": ("name",)}
    autocomplete_fields = ("connector", "user")
    fieldsets = (
        (None, {
            "fields": ("name", "slug", "user", "avatar", "description", "is_active"),
        }),
        ("Runtime", {
            "fields": ("model_name", "connector", "temperature", "system_prompt"),
        }),
    )
    inlines = [AgentToolInline]


@admin.register(AgentConnector)
class AgentConnectorAdmin(admin.ModelAdmin):
    list_display = ("name", "provider", "auth_type", "api_secret", "signing_key", "is_active", "is_slow", "updated_at")
    list_filter = ("provider", "auth_type", "is_active", "is_slow", "created_at")
    search_fields = ("name", "slug", "base_url")
    prepopulated_fields = {"slug": ("name",)}
    readonly_fields = ("created_at", "updated_at")
    autocomplete_fields = ("api_secret", "signing_key", "owner")
    fieldsets = (
        (None, {
            "fields": ("name", "slug", "provider", "base_url", "is_active", "is_slow", "owner"),
        }),
        ("Authentication", {
            "fields": ("auth_type", "api_secret", "signing_key", "auth_config"),
            "description": "Link secrets to a Gervazy EncryptedSecret. Decryption requires a vault session.",
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


@admin.register(AgentRun)
class AgentRunAdmin(admin.ModelAdmin):
    list_display = ("id", "agent", "status", "created_at", "started_at", "finished_at")
    list_filter = ("status", "agent")
    search_fields = ("user_prompt", "result", "error")
    readonly_fields = ("created_at", "started_at", "finished_at")


@admin.register(AgentTool)
class AgentToolAdmin(admin.ModelAdmin):
    list_display = ("agent", "key", "enabled")
    list_filter = ("enabled", "key")
