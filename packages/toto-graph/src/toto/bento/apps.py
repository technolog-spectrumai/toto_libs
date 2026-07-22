from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured


class BentoConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.bento"

    def ready(self):
        from django.apps import apps as django_apps

        # Bento is a first-class Neo4j editor and depends on ravioli (the sole
        # Neo4j boundary) for both the Neo4jClient and the neomodel connection.
        # Refuse to start if bento is installed without ravioli.
        if not django_apps.is_installed("toto.ravioli"):
            raise ImproperlyConfigured(
                "toto.bento requires toto.ravioli (the Neo4j boundary) to be "
                "installed. Add 'toto.ravioli' to INSTALLED_APPS, or remove "
                "'toto.bento'."
            )

        # Rebuild the dynamic neomodel class registry whenever a template
        # changes so every worker stays consistent with the DB.
        from . import signals  # noqa: F401
