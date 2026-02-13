from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _
import logging


class OyaConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'oya'
    verbose_name = _("Root")
    url_name = "nest"

    def ready(self):
        logging.getLogger("neo4j").setLevel(logging.WARNING)
        logging.getLogger("neo4j.io").setLevel(logging.WARNING)

