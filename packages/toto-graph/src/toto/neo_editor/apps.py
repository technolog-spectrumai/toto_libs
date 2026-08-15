from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _


class NeoEditorConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.neo_editor"
    verbose_name = _("NeoJSON Editor")
