from django.apps import AppConfig


class StevenConfig(AppConfig):
    """Steven: the floating chat-widget UI.

    A thin UI shell — no models, urls, or views. It injects a hovering 'Ask AI'
    window site-wide that chats (over websockets) with the platform's configured
    agent, served by the headless ``toto.sabbia`` backend.
    """

    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.steven"
    verbose_name = "Steven"

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.floating_plugins")
