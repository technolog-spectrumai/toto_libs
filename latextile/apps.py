from django.apps import AppConfig


class LatextileConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'latextile'
    verbose_name = 'latex'

    def ready(self):
        import latextile.signals  # noqa