from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
from .log import BufferedFileLogSink



class OyaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'oya'
    verbose_name = _("Nest")
    url_name = "nest"

    def ready(self):
        BufferedFileLogSink.create("toto")
