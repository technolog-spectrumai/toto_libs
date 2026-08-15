from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class SocialLoginConfig(AppConfig):
    name = "toto.social_login"
    verbose_name = _("Social Login")
    default_auto_field = "django.db.models.BigAutoField"
