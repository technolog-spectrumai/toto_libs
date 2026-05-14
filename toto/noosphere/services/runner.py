import hashlib
from pathlib import Path

from django.core.exceptions import ImproperlyConfigured
from django.utils import timezone

from noosphere.models import SyncRule, SyncRun
from noosphere.services.package_builder import SyncPackageBuilder
from noosphere.transport_registry import get_transport_for_rule


class SyncRunner:
    """
    Runs one SyncRule.

    One rule = one model + one direction + one remote platform.
    """

    def __init__(self, local_platform):
        self.local_platform = local_platform

    def run_rule(self, rule):
        if rule.local_platform_id != self.local_platform.id:
            raise ImproperlyConfigured(
                "Sync rule does not belong to this local platform."
            )

        if not rule.enabled:
            raise ImproperlyConfigured(f"Sync rule is disabled: {rule}")

        if not rule.remote_platform_id:
            raise ImproperlyConfigured("Sync rule has no remote platform.")

        if not rule.remote_platform.enabled:
            raise ImproperlyConfigured(
                f"Remote platform is disabled: {rule.remote_platform}"
            )

        if not rule.remote_platform.outgoing_secret_key:
            raise ImproperlyConfigured(
                f"Remote platform has no outgoing secret key: {rule.remote_platform}"
            )

        if rule.direction not in [SyncRule.DIRECTION_UP, SyncRule.DIRECTION_DOWN]:
            raise ImproperlyConfigured(f"Unsupported sync direction: {rule.direction}")

        run = SyncRun.objects.create(
            local_platform=self.local_platform,
            remote_platform=rule.remote_platform,
            rule=rule,
            direction=rule.direction,
        )

        try:
            package_path = self._build_package(rule)
            package_hash = self._sha256_file(package_path)

            run.package_hash = package_hash
            run.save(update_fields=["package_hash"])

            transport = get_transport_for_rule(rule)
            remote_response = transport.upload_package(package_path)

            self._apply_remote_response_to_run(run, remote_response)

            rule.mark_synced()

            rule.remote_platform.last_seen_at = timezone.now()
            rule.remote_platform.save(update_fields=["last_seen_at", "updated_at"])

            run.mark_success(
                message="Sync completed.",
                remote_response=remote_response,
            )

            return remote_response

        except Exception as exc:
            run.mark_failed(message=str(exc))
            raise

    def _build_package(self, rule):
        return SyncPackageBuilder(
            platform=self.local_platform,
            apps_to_sync=[rule.app_label],
            sign=True,
        ).create_rule_package(rule)

    def _sha256_file(self, path):
        h = hashlib.sha256()

        with Path(path).open("rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                h.update(chunk)

        return h.hexdigest()

    def _apply_remote_response_to_run(self, run, response):
        run.imported_count = response.get("imported_count", 0)
        run.created_count = response.get("created_count", 0)
        run.updated_count = response.get("updated_count", 0)
        run.skipped_count = response.get("skipped_count", 0)
        run.deleted_count = response.get("deleted_count", 0)
        run.failed_count = response.get("failed_count", 0)
        run.remote_response = response

        run.save(update_fields=[
            "imported_count",
            "created_count",
            "updated_count",
            "skipped_count",
            "deleted_count",
            "failed_count",
            "remote_response",
        ])
