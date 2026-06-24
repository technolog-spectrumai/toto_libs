from toto.core.plugin import FloatingPlugin


@FloatingPlugin.plugin(
    key="steven_chat",
    order=10,
)
class StevenChatPlugin(FloatingPlugin):
    """Floating 'Ask AI' button that opens an inline chat panel powered by Steven."""

    template_name = "steven/plugins/floating_chat.html"

    def get_context(self, **kwargs):
        from django.conf import settings
        from django.db import OperationalError

        from toto.steven.models import AgentProfile

        context = super().get_context(**kwargs)
        context["steven_debug"] = settings.DEBUG
        context["steven_stub_reason"] = None

        try:
            agent = AgentProfile.objects.select_related("connector").filter(is_active=True).first()
            context["agent"] = agent
            if agent is None:
                pass
            elif agent.connector_id is None:
                context["steven_stub_reason"] = "No connector attached — running in stub mode."
        except OperationalError as exc:
            context["agent"] = None
            context["steven_stub_reason"] = f"DB not ready: {exc}"

        return context
