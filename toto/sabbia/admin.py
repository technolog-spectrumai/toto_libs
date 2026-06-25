from django.contrib import admin

from toto.api.admin import ApiConnectorSecretAdminMixin, make_connector_secret_form

from .models import Agent, AgentConnector, ChatMessage, Conversation, PlatformChatbot


@admin.register(AgentConnector)
class AgentConnectorAdmin(ApiConnectorSecretAdminMixin, admin.ModelAdmin):
    form = make_connector_secret_form(AgentConnector)
    list_display = ("name", "provider", "auth_type", "secret_status", "is_active", "updated_at")
    list_filter = ("provider", "auth_type", "is_active")
    search_fields = ("name", "slug", "base_url")
    readonly_fields = ("api_secret", "secret_status")


@admin.register(Agent)
class AgentAdmin(admin.ModelAdmin):
    list_display = (
        "name", "slug", "endpoint_type", "model_name", "connector", "is_active", "updated_at",
    )
    list_filter = ("endpoint_type", "is_active")
    search_fields = ("name", "slug", "description", "system_prompt")
    prepopulated_fields = {"slug": ("name",)}


@admin.register(PlatformChatbot)
class PlatformChatbotAdmin(admin.ModelAdmin):
    list_display = ("platform", "agent", "is_enabled")
    list_filter = ("is_enabled",)


class ChatMessageInline(admin.TabularInline):
    model = ChatMessage
    extra = 0
    readonly_fields = ("role", "content", "created_at")
    can_delete = False


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ("id", "agent", "user", "title", "updated_at")
    list_filter = ("agent",)
    search_fields = ("title",)
    inlines = [ChatMessageInline]
