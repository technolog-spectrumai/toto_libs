from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

class SSOMasterConfig(AppConfig):
    name = "toto.sso_master"
    verbose_name = _("SSO Master (Provider)")
    default_auto_field = "django.db.models.BigAutoField"
