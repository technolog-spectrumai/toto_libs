from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import transaction

from .backup_engine import BackupEngine


class SyncService(BackupEngine):
    """Applies a signed backup ZIP into the local database using uid as identity."""

    def apply_backup(self, backup_path, verify_signature=True, clear_existing=False):
        with self.extract_zip(backup_path) as tmp:
            manifest = self.load_manifest(tmp)
            self.validate_manifest(manifest)
            # Use the app list from the manifest so the caller doesn't need to pass it
            self.apps_to_sync = manifest.get("apps", [])
            self.verify_hashes(tmp, manifest)

            if verify_signature:
                self.verify_signature(tmp)

            with transaction.atomic():
                for model_info in manifest["models"]:
                    model = apps.get_model(model_info["app"], model_info["model"])
                    if not self.is_backup_model(model):
                        raise ImproperlyConfigured(
                            f"Model is not backup-safe: {model._meta.label}"
                        )
                    payload = self.read_json(tmp / model_info["file"])
                    self._import_model(model, payload, clear_existing)
        return True

    def _import_model(self, model, payload, clear_existing):
        objects_data = payload.get("objects", [])

        if clear_existing:
            model.objects.all().delete()

        created = updated = 0
        for item in objects_data:
            uid = item.get("uid")
            if not uid:
                raise ImproperlyConfigured(
                    f"Missing uid in backup object for {model._meta.label}"
                )
            fields = self._resolve_fields(model, item.get("fields", {}))
            _, was_created = model.objects.update_or_create(uid=uid, defaults=fields)
            if was_created:
                created += 1
            else:
                updated += 1

        print(f"Seeded {model._meta.label}: {created} created, {updated} updated")

    def _resolve_fields(self, model, fields):
        resolved = {}
        for field_name, value in fields.items():
            try:
                field = model._meta.get_field(field_name)
            except FieldDoesNotExist:
                # Field absent in this build — e.g. a geometry column synced from
                # a GIS host onto a GIS-off (BUILD_GEO=0) host. Drop it; the
                # accompanying lat/lon floats carry the coordinates.
                continue
            if isinstance(value, dict) and value.get("__ref__"):
                resolved[field.name] = self._resolve_reference(value)
            elif isinstance(value, dict) and value.get("__geo__"):
                try:
                    from django.contrib.gis.geos import GEOSGeometry
                    resolved[field.name] = GEOSGeometry(value["ewkt"]) if value.get("ewkt") else None
                except ImportError:
                    resolved[field.name] = None
            else:
                resolved[field.name] = value
        return resolved

    def _resolve_reference(self, value):
        app_label, model_name = value["model"].split(".")
        related_model = apps.get_model(app_label, model_name)
        if not self.is_backup_model(related_model):
            raise ImproperlyConfigured(
                f"Referenced model is not backup-safe: {value['model']}"
            )
        try:
            return related_model.objects.get(uid=value["uid"])
        except related_model.DoesNotExist:
            raise ImproperlyConfigured(
                f"Missing referenced object: {value['model']} uid={value['uid']}"
            )
