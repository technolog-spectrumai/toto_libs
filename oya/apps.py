from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class OyaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'oya'
    verbose_name = _("Nest")
