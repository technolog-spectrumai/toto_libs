import logging

from django.apps import AppConfig

log = logging.getLogger(__name__)


class DatalinkConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "toto.datalink"
    verbose_name = "Data link"

    def ready(self):
        # Collect every app's replication policies. Safe from here wherever datalink
        # sits in INSTALLED_APPS: Django imports every models.py before it calls any
        # ready(), so apps installed later are already importable.
        from toto.core.plugin_autodiscover import autodiscover_plugins

        autodiscover_plugins("datalink_policies")

        # Importing the module is what registers the check.
        from . import checks  # noqa: F401

        self._assert_credentials_cannot_be_backed_up()

        # Log, never raise. A broken policy module must not be able to stop
        # `manage.py migrate` — that would make a bad declaration unfixable. The
        # places that refuse are `manage.py datalink_check`, the system check, and
        # the run's own preflight.
        from .validate import validate_registry

        try:
            problems = validate_registry()
        except Exception:  # noqa: BLE001 - never let validation break app loading
            log.exception("datalink: registry validation raised")
            return
        for problem in problems:
            log.error("datalink registry: %s", problem)

    def _assert_credentials_cannot_be_backed_up(self):
        """datalink's own tables hold peer credentials. Keep them out of backups.

        ``backup_engine.is_backup_model()`` selects a model for a backup ZIP purely
        by asking whether it has a field literally named ``uid`` — within an app named
        in ``settings.APPS_TO_SYNC``. So a host that adds "datalink" to that list
        would serialise its peer grants, magic tokens and api keys into a signed,
        pullable archive.

        Two defences. The host is told not to add the label (and
        ``BACKUP_EXCLUDED_MODELS`` names the credential models as a belt). And here:
        no datalink model may carry a field called ``uid``, which makes the selector
        structurally unable to pick one up. Identity columns are named ``grant_uid``,
        ``peer_uid``, ``run_uid`` instead.
        """
        offenders = []
        for model in self.get_models():
            names = {f.name for f in model._meta.get_fields() if getattr(f, "concrete", False)}
            if "uid" in names:
                offenders.append(model._meta.label)
        if offenders:  # pragma: no cover - a coding error, asserted in tests too
            raise RuntimeError(
                "datalink models must not have a field named 'uid': "
                + ", ".join(offenders)
                + ". backup_engine.is_backup_model() selects on that exact name, so "
                  "such a field would let peer credentials be written into a backup "
                  "archive. Use grant_uid / peer_uid / run_uid."
            )

        from django.conf import settings

        if "datalink" in (getattr(settings, "APPS_TO_SYNC", None) or ()):
            log.error(
                "datalink: this host lists 'datalink' in APPS_TO_SYNC. Peer "
                "credentials and run history would be serialised into backup "
                "archives. Remove it."
            )
