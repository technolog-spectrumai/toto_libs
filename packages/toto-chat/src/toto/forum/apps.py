from django.apps import AppConfig


class ForumConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.forum'

    def ready(self):
        # Membership changes must reach sockets that are already connected.
        from . import signals  # noqa: F401
