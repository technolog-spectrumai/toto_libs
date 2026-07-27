from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class VodConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.vod"
    label = "vod"
    verbose_name = _("Video on Demand")
