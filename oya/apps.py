from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
from .log import BufferedFileLogSink



class OyaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'oya'
    verbose_name = _("Root")
    url_name = "root"

    def ready(self):
        BufferedFileLogSink.create("toto")
