from django.apps import AppConfig


class MissionEconomyConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.mission_economy"
    verbose_name = "Mission Economy"

    def ready(self):
        import toto.mission_economy.plugins.mission_tab_plugins  # noqa: F401 — registers MissionTabPlugin
