from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class IngestorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.ingestor"
    verbose_name = "Graph Ingestor"

    def ready(self):
        from django.apps import apps as django_apps

        # The Ingestor turns pasted text into a Bento-validated graph patch and
        # applies it through bento.graph_service. It never touches Neo4j directly,
        # so it requires both ravioli (the Neo4j boundary) and bento (templates +
        # validated CRUD) to be installed.
        for dep in ("toto.ravioli", "toto.bento"):
            if not django_apps.is_installed(dep):
                raise ImproperlyConfigured(
                    f"toto.ingestor requires {dep} to be installed. Add it to "
                    "INSTALLED_APPS, or remove 'toto.ingestor'."
                )
