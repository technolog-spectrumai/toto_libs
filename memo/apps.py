from django.apps import AppConfig


class MemoConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'memo'

    def ready(self):
        import memo.signals  # noqa