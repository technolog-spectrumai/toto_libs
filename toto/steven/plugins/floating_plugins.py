from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(
    key="steven_chat",
    order=10,
)
class StevenChatPlugin(FloatingPlugin):
    """Floating 'Ask AI' button that opens an inline chat panel powered by Steven."""

    template_name = "steven/plugins/floating_chat.html"

    def get_context(self, **kwargs):
        from toto.steven.models import AgentProfile
        from django.db import OperationalError

        context = super().get_context(**kwargs)
        try:
            context["agent"] = AgentProfile.objects.filter(is_active=True).first()
        except OperationalError:
            context["agent"] = None
        return context
