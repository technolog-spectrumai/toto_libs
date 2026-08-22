from django.apps import AppConfig


class ForumConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.forum'

    def ready(self):
        # Membership changes must reach sockets that are already connected.
        from . import signals  # noqa: F401

        # A room's polls are answered by the room. Registered here rather
        # than discovered: one line, no machinery, and inert on a host that
        # ships forum without polls.
        try:
            from . import audience
        except ImportError:                     # no toto.polls in this build
            return
        audience.install()
