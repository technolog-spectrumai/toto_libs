from django.apps import AppConfig


class GitopsConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'gitops'

    def ready(self):
        import gitops.signals
