from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class ConnectorsConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.connectors"
    verbose_name = "Data Connectors"

    def ready(self):
        from django.apps import apps as django_apps

        # Connectors ETL external API records into Bento-validated graph patches
        # (ingestor proposals) and apply them through bento.graph_service. They
        # never touch Neo4j directly, so the whole graph stack must be present,
        # plus toto.api for the credentialed HTTP client.
        for dep in ("toto.ravioli", "toto.bento", "toto.ingestor", "toto.api"):
            if not django_apps.is_installed(dep):
                raise ImproperlyConfigured(
                    f"toto.connectors requires {dep} to be installed. Add it to "
                    "INSTALLED_APPS, or remove 'toto.connectors'."
                )

        from . import signals  # noqa: F401 — mirror proposal outcomes onto runs
