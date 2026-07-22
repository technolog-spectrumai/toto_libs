from django.apps import AppConfig



class VaultConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.vault'

    def ready(self):
        import toto.vault.signals  # noqa: F401 — registers signal handlers
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.vault_play_plugins")
        autodiscover_plugins("plugins.vault_editor_plugins")
