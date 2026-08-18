from django.apps import AppConfig



class VaultConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'toto.vault'

    def ready(self):
        import toto.vault.signals  # noqa: F401 — registers signal handlers
        from toto.core.plugin_autodiscover import autodiscover_plugins
        autodiscover_plugins("plugins.vault_play_plugins")
        autodiscover_plugins("plugins.vault_editor_plugins")
        # Who may write a borrowed file (see VaultAccessPlugin). Discovered
        # unconditionally and separately from the editor buttons above: those
        # are gated on whether a host shows an editor, and access must not be.
        autodiscover_plugins("plugins.vault_access_plugins")
        # File services — discovered HERE rather than in FileservicesConfig, so
        # a host without toto-media-ops still collects the plugins that do not
        # need its run substrate (the builder-backed ones).
        autodiscover_plugins("plugins.file_service_plugins")
