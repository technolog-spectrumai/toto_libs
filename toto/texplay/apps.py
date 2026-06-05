from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class TexPlayConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.texplay"
    label = "texplay"
    verbose_name = _("TeX Play")

    def ready(self):
        from . import predefined_tasks  # noqa: F401 — registers texplay_compile
