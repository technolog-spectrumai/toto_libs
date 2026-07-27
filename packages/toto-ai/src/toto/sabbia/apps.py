from django.apps import AppConfig


class SabbiaConfig(AppConfig):
    """Headless agentic/chatbot backend.

    Hosts any number of chat agents that talk to a user over websockets. No UI —
    the floating chat widget lives in the separate ``toto.steven`` app.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.sabbia"
    verbose_name = "Sabbia"
