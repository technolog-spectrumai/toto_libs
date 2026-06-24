from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(key="steven_chat", order=10)
class StevenChatPlugin(FloatingPlugin):
    """Floating 'Ask AI' window that chats with the platform's sabbia agent over websockets."""

    template_name = "steven/plugins/floating_chat.html"

    def visible_for_request(self, request):
        # Chat requires an authenticated websocket; hide the widget for anonymous visitors.
        return bool(getattr(getattr(request, "user", None), "is_authenticated", False))

    def get_context(self, **kwargs):
        context = super().get_context(**kwargs)
        context["agent"] = None
        try:
            from django.db import OperationalError
            from toto.core.models import Platform
            from toto.sabbia.models import PlatformChatbot
        except ImportError:
            # sabbia backend not installed — render nothing.
            return context

        try:
            platform = Platform.objects.first()
            link = (
                PlatformChatbot.objects.select_related("agent")
                .filter(platform=platform, is_enabled=True, agent__is_active=True)
                .first()
            )
            if link:
                context["agent"] = link.agent
        except OperationalError:
            pass
        return context
