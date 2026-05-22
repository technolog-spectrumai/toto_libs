from django.apps import AppConfig


class AssemblyConfig(AppConfig):
    name = "toto.assembly"
    label = "assembly"
    verbose_name = "Community Assembly"

    def ready(self):
        # Register the community detail plugin (socialhub shows an Assembly card)
        from . import community_plugin  # noqa: F401
        # Register the assets fee plugin
        from . import fee_plugin  # noqa: F401
