from django.core.exceptions import ImproperlyConfigured

from toto.core.services.backup_service import BackupService

from noosphere.registry import get_sync_adapter


class SyncPackageBuilder(BackupService):
    """
    Selective sync package builder.

    Reuses core BackupService for:
      - temp directory creation
      - manifest creation
      - JSON writing
      - hashing
      - signing
      - ZIP writing

    Adds only:
      - model selection
      - queryset selection
      - field selection
      - sync metadata
    """

    def create_rule_package(self, rule, output_path=None):
        if not rule.enabled:
            raise ImproperlyConfigured(f"Sync rule is disabled: {rule}")

        if not rule.remote_platform_id:
            raise ImproperlyConfigured("Sync rule has no remote platform.")

        if not rule.remote_platform.enabled:
            raise ImproperlyConfigured(
                f"Remote platform is disabled: {rule.remote_platform}"
            )

        adapter = get_sync_adapter(rule.model_label)

        queryset = adapter.get_queryset(rule)
        fields = adapter.get_export_fields(rule)

        if not fields:
            raise ImproperlyConfigured(
                f"No export fields configured for sync rule: {rule}"
            )

        sync_meta = {
            "type": "sync",

            "rule_id": rule.id,
            "rule_name": rule.name,

            "local_platform_id": rule.local_platform_id,
            "local_platform": {
                "id": rule.local_platform_id,
                "site_name": getattr(rule.local_platform, "site_name", ""),
                "domain": getattr(rule.local_platform, "domain", ""),
            },

            "remote_platform_id": rule.remote_platform_id,
            "remote_platform": {
                "id": rule.remote_platform_id,
                "name": rule.remote_platform.name,
                "base_url": rule.remote_platform.normalized_base_url,
            },

            "model_label": adapter.model_label,
            "direction": rule.direction,

            "fields": fields,
            "filters": rule.filters or {},

            "sync_creates": rule.sync_creates,
            "sync_updates": rule.sync_updates,
            "sync_deletes": rule.sync_deletes,
            "conflict_policy": rule.conflict_policy,
            "include_dependencies": rule.include_dependencies,
        }

        return self.create_backup(
            output_path=output_path,
            model_labels=[adapter.model_label],
            queryset_map={
                adapter.model_label: queryset,
            },
            field_map={
                adapter.model_label: fields,
            },
            sync_meta=sync_meta,
        )
