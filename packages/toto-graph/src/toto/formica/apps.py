from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class FormicaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.formica"
    verbose_name = "Formica Colony"

    def ready(self):
        from django.apps import apps as django_apps

        # Formica curates the ravioli/bento graph: direct marker writes go
        # through ravioli's Neo4jClient (graph_ops is the only module doing
        # so), structural mutations through bento.graph_service. Both stacks
        # must be present.
        for dep in ("toto.ravioli", "toto.bento", "toto.sql_neo4j_sync"):
            if not django_apps.is_installed(dep):
                raise ImproperlyConfigured(
                    f"toto.formica requires {dep} to be installed. Add it to "
                    "INSTALLED_APPS, or remove 'toto.formica'."
                )

        from .colony import castes  # noqa: F401 — populate CASTE_REGISTRY
