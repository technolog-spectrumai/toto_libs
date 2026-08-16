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
        self._assert_credentials_cannot_be_backed_up()

    def _assert_credentials_cannot_be_backed_up(self):
        """Keep bucket-peering credentials out of backup archives, structurally.

        ``backup_engine.is_backup_model()`` selects a model for a backup ZIP
        purely by asking whether it has a field literally named ``uid`` —
        within an app named in ``settings.APPS_TO_SYNC``, and "vault" is on
        that list everywhere. A grant's magic token or a peer's api key in a
        signed, pullable archive would hand the puller the bucket link.

        So no vault model may carry a field called ``uid``, which makes the
        selector structurally unable to pick one up. Identity columns are
        ``grant_uid`` / ``peer_uid`` instead. (The rule, and the incident
        class it prevents, came from the parked datalink app — see
        limbo/datalink/PARKED.md.) A test asserts this independently of the
        app loading.
        """
        offenders = []
        for model in self.get_models():
            names = {f.name for f in model._meta.get_fields()
                     if getattr(f, "concrete", False)}
            if "uid" in names:
                offenders.append(model._meta.label)
        if offenders:  # pragma: no cover - a coding error, asserted in tests too
            raise RuntimeError(
                "vault models must not have a field named 'uid': "
                + ", ".join(offenders)
                + ". backup_engine.is_backup_model() selects on that exact "
                  "name, so such a field would let peering credentials be "
                  "written into a backup archive. Use grant_uid / peer_uid."
            )
