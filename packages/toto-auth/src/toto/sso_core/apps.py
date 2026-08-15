from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _

class SSOCoreConfig(AppConfig):
    name = "toto.sso_core"
    verbose_name = _("SSO Core")
