import json
import urllib.request
from urllib.error import URLError

from django.contrib import admin
from django.http import HttpResponseRedirect
from django.utils import timezone

from .models import OllamaModel, OllamaServer


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _sync_server(server: OllamaServer) -> int:
    """Query /api/tags and upsert OllamaModel rows. Returns number of models found."""
    from toto.vicuna.chat import ollama_chat_model_choices

    host = server.host.rstrip("/")
    with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as resp:
        data = json.loads(resp.read())

    allowed = set(ollama_chat_model_choices())
    pulled = {m["name"]: m for m in (data.get("models") or [])}

    for name, meta in pulled.items():
        if name in allowed:
            OllamaModel.objects.update_or_create(
                server=server,
                name=name,
                defaults={"size_bytes": meta.get("size")},
            )

    server.models.exclude(name__in=pulled).delete()
    server.last_synced = timezone.now()
    server.save(update_fields=["last_synced", "updated_at"])
    return len([n for n in pulled if n in allowed])


def _pull_model(host: str, model: str, timeout: int = 300) -> None:
    payload = json.dumps({"model": model, "stream": False}).encode()
    req = urllib.request.Request(
        f"{host}/api/pull",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        resp.read()


# ---------------------------------------------------------------------------
# Inlines
# ---------------------------------------------------------------------------

class OllamaModelInline(admin.TabularInline):
    model = OllamaModel
    fields = ("name", "size_display", "last_seen")
    readonly_fields = ("name", "size_display", "last_seen")
    extra = 0
    can_delete = False

    @admin.display(description="Size")
    def size_display(self, obj):
        if obj.size_bytes is None:
            return "—"
        gb = obj.size_bytes / 1_073_741_824
        return f"{gb:.1f} GB"


# ---------------------------------------------------------------------------
# OllamaServer admin
# ---------------------------------------------------------------------------

@admin.register(OllamaServer)
class OllamaServerAdmin(admin.ModelAdmin):
    change_form_template = "vicuna/admin/ollamaserver_change_form.html"
    list_display = ("name", "host", "uses_gpu", "is_active", "model_count", "last_synced")
    list_filter = ("is_active", "uses_gpu")
    search_fields = ("name", "host")
    readonly_fields = ("last_synced", "created_at", "updated_at")
    inlines = [OllamaModelInline]
    actions = ["action_sync", "action_pull_and_sync"]

    @admin.display(description="Models")
    def model_count(self, obj):
        return obj.models.count()

    def response_change(self, request, obj):
        if "_test_server" in request.POST:
            host = obj.host.rstrip("/")
            try:
                with urllib.request.urlopen(f"{host}/api/tags", timeout=5) as resp:
                    data = json.loads(resp.read())
                models = data.get("models") or []
                pulled = [m["name"] for m in models]
                detail = f"{len(pulled)} model(s) available" + (
                    f": {', '.join(pulled)}" if pulled else ""
                )
                self.message_user(request, f"✓ {obj.name} ({host}) is reachable — {detail}.")
            except URLError as exc:
                self.message_user(
                    request,
                    f"✗ Cannot reach {obj.name} ({host}): {exc.reason}",
                    level="error",
                )
            except Exception as exc:
                self.message_user(request, f"✗ Test failed: {exc}", level="error")
            return HttpResponseRedirect(request.path)
        return super().response_change(request, obj)

    @admin.action(description="Sync model list from Ollama (/api/tags)")
    def action_sync(self, request, queryset):
        ok, errors = 0, []
        for server in queryset.filter(is_active=True):
            try:
                count = _sync_server(server)
                ok += 1
                self.message_user(request, f"{server.name}: {count} model(s) synced.")
            except URLError as exc:
                errors.append(f"{server.name}: cannot reach {server.host} — {exc.reason}")
            except Exception as exc:
                errors.append(f"{server.name}: {exc}")
        for err in errors:
            self.message_user(request, err, level="error")

    @admin.action(description="Pull allowed models then sync (may be slow)")
    def action_pull_and_sync(self, request, queryset):
        from toto.vicuna.chat import ollama_chat_model_choices
        allowed = ollama_chat_model_choices()

        for server in queryset.filter(is_active=True):
            host = server.host.rstrip("/")
            for model in allowed:
                try:
                    _pull_model(host, model)
                except Exception as exc:
                    self.message_user(
                        request,
                        f"{server.name} / {model}: pull failed — {exc}",
                        level="warning",
                    )
            try:
                count = _sync_server(server)
                self.message_user(request, f"{server.name}: pulled and synced {count} model(s).")
            except Exception as exc:
                self.message_user(request, f"{server.name}: sync after pull failed — {exc}", level="error")


@admin.register(OllamaModel)
class OllamaModelAdmin(admin.ModelAdmin):
    list_display = ("name", "server", "size_display", "last_seen")
    list_filter = ("server",)
    search_fields = ("name", "server__name")
    readonly_fields = ("last_seen",)

    @admin.display(description="Size")
    def size_display(self, obj):
        if obj.size_bytes is None:
            return "—"
        return f"{obj.size_bytes / 1_073_741_824:.1f} GB"
