from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class ClearingConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.clearing"
    verbose_name = _("Clearing (cross-platform ledger federation)")
