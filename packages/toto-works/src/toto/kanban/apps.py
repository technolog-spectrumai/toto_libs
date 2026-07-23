from django.apps import AppConfig
from django.utils.translation import gettext_lazy as _



class KanbanConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.kanban'

    def ready(self):
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("plugins.kanban_plugins")
