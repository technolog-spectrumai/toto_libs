from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class PrimulaConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.primula"
    verbose_name = _("Primula Sheets")
