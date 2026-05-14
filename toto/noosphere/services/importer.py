from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import transaction

from toto.core.services.sync_service import SyncService

from noosphere.models import SyncObjectRun
from noosphere.registry import get_sync_adapter


class SyncPackageImporter(SyncService):
    """
    Applies a selective sync package.

    Reuses core SyncService for:
      - ZIP extraction
      - manifest loading
      - manifest validation
      - hash verification
      - signature verification
      - JSON reading
      - _resolve_fields()
      - _resolve_reference()

    Adds only:
      - sync manifest policy
      - create/update skipping
      - selected field enforcement
      - run/object logging
    """

    def apply_sync_package(self, backup_path, run=None, verify_signature=True):
        result = {
            "imported_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
        }

        with self.extract_zip(backup_path) as tmp:
            manifest = self.load_manifest(tmp)
            self.validate_manifest(manifest)

            self.apps_to_sync = manifest.get("apps", [])
            self.verify_hashes(tmp, manifest)

            if verify_signature:
                self.verify_signature(tmp)

            sync_meta = manifest.get("sync") or {}

            if sync_meta.get("type") != "sync":
                raise ImproperlyConfigured("Package is not a sync package.")

            expected_model_label = sync_meta.get("model_label")

            if not expected_model_label:
                raise ImproperlyConfigured("Sync package is missing sync.model_label.")

            with transaction.atomic():
                for model_info in manifest.get("models", []):
                    model = apps.get_model(model_info["app"], model_info["model"])

                    if model._meta.label != expected_model_label:
                        raise ImproperlyConfigured(
                            f"Unexpected model in sync package: {model._meta.label}"
                        )

                    if not self.is_backup_model(model):
                        raise ImproperlyConfigured(
                            f"Model is not backup-safe: {model._meta.label}"
                        )

                    adapter = get_sync_adapter(model._meta.label)
                    payload = self.read_json(tmp / model_info["file"])

                    model_result = self._import_model_for_sync(
                        model=model,
                        adapter=adapter,
                        payload=payload,
                        sync_meta=sync_meta,
                        run=run,
                    )

                    for key, value in model_result.items():
                        result[key] += value

        return result

    def _import_model_for_sync(self, model, adapter, payload, sync_meta, run=None):
        result = {
            "imported_count": 0,
            "created_count": 0,
            "updated_count": 0,
            "skipped_count": 0,
            "deleted_count": 0,
            "failed_count": 0,
        }

        allowed_fields = set(sync_meta.get("fields") or [])
        sync_creates = sync_meta.get("sync_creates", True)
        sync_updates = sync_meta.get("sync_updates", True)

        for item in payload.get("objects", []):
            uid = item.get("uid")

            if not uid:
                result["failed_count"] += 1
                self._log_object(
                    run=run,
                    model_label=model._meta.label,
                    uid="",
                    action=SyncObjectRun.ACTION_ERROR,
                    status=SyncObjectRun.STATUS_FAILED,
                    message="Missing uid.",
                    payload=item,
                )
                continue

            try:
                incoming = item.get("fields", {})

                if allowed_fields:
                    incoming = {
                        key: value
                        for key, value in incoming.items()
                        if key in allowed_fields
                    }

                exists = model.objects.filter(uid=uid).exists()

                if not exists and not sync_creates:
                    result["skipped_count"] += 1
                    self._log_object(
                        run=run,
                        model_label=model._meta.label,
                        uid=uid,
                        action=SyncObjectRun.ACTION_SKIP,
                        status=SyncObjectRun.STATUS_SKIPPED,
                        message="Create skipped by sync policy.",
                        payload=item,
                    )
                    continue

                if exists and not sync_updates:
                    result["skipped_count"] += 1
                    self._log_object(
                        run=run,
                        model_label=model._meta.label,
                        uid=uid,
                        action=SyncObjectRun.ACTION_SKIP,
                        status=SyncObjectRun.STATUS_SKIPPED,
                        message="Update skipped by sync policy.",
                        payload=item,
                    )
                    continue

                resolved = self._resolve_fields(model, incoming)

                _, was_created = model.objects.update_or_create(
                    uid=uid,
                    defaults=resolved,
                )

                result["imported_count"] += 1

                if was_created:
                    result["created_count"] += 1
                    action = SyncObjectRun.ACTION_CREATE
                else:
                    result["updated_count"] += 1
                    action = SyncObjectRun.ACTION_UPDATE

                self._log_object(
                    run=run,
                    model_label=model._meta.label,
                    uid=uid,
                    action=action,
                    status=SyncObjectRun.STATUS_SUCCESS,
                    message="Imported.",
                    payload=item,
                )

            except Exception as exc:
                result["failed_count"] += 1

                self._log_object(
                    run=run,
                    model_label=model._meta.label,
                    uid=uid,
                    action=SyncObjectRun.ACTION_ERROR,
                    status=SyncObjectRun.STATUS_FAILED,
                    message=str(exc),
                    payload=item,
                )

                raise

        return result

    def _log_object(self, run, model_label, uid, action, status, message="", payload=None):
        if not run:
            return

        SyncObjectRun.objects.create(
            run=run,
            model_label=model_label,
            uid=uid,
            action=action,
            status=status,
            message=message,
            payload=payload or {},
        )
