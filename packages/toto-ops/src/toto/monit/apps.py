from django.apps import AppConfig


class MonitConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.monit"
    verbose_name = "Monitoring"

    def ready(self):
        # Every run of a scheduled task is recorded by Celery's own signals
        # (2026-10-01, toto.monit.heartbeats). Connected in every process;
        # they fire only in the worker that runs tasks and the beat that
        # schedules them.
        from . import heartbeats

        heartbeats.connect()
