from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class GeographyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.geography"
    label = "geography"
    verbose_name = _("Geography")

    def ready(self):
        from . import receivers  # noqa: F401 - the link rows' post_delete
