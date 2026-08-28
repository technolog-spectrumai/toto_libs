from django.apps import AppConfig


class ForumConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.forum'

    def ready(self):
        # Membership changes must reach sockets that are already connected.
        from . import signals  # noqa: F401

        # The audience registration that used to sit here is gone with the
        # engine it talked to: a room's polls are this app's own data now, so
        # there is no registry between a room and its own question.
