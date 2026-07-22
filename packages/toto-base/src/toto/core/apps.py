from django.apps import AppConfig


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.core'
    url_name = "toto.core"

    def ready(self):
        from django.core.exceptions import ImproperlyConfigured
        from django.db.backends.signals import connection_created

        from toto.versioning import TotoVersionError, check_runtime_coherence

        # toto.core is installed by every host, so this is the one place that
        # sees the whole suite. Refuse to boot a half-upgraded installation
        # rather than fail later in some unrelated import.
        try:
            check_runtime_coherence()
        except TotoVersionError as exc:
            raise ImproperlyConfigured(str(exc)) from exc

        def _set_sqlite_wal(sender, connection, **kwargs):
            if connection.vendor == "sqlite":
                connection.cursor().execute("PRAGMA journal_mode=WAL;")
                connection.cursor().execute("PRAGMA synchronous=NORMAL;")

        connection_created.connect(_set_sqlite_wal)

