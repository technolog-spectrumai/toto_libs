import logging

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured
from django.db import transaction

from .backup_engine import BackupEngine

logger = logging.getLogger(__name__)


class UnresolvableReference(Exception):
    """A ``__ref__`` pointing at something no restore could contain.

    Not an error in the archive: the target's app is simply not in
    ``APPS_TO_SYNC``, which is a deliberate choice for ``vault`` (blobs travel by
    bucket mirroring) and true of anything else a host declines to sync. What
    happens next depends on the field, and is decided by the caller — NULL for a
    nullable one, a skipped row for a required one.
    """

    def __init__(self, model_label, ident, field=None):
        self.model_label = model_label
        self.ident = ident
        self.field = field
        super().__init__(f"{model_label} ({ident}) cannot be resolved here")

    def _where(self, model):
        name = getattr(self.field, "name", "?")
        return f"{model._meta.label}.{name}"

    def as_dropped(self, model):
        return (f"Restore: {self._where(model)} -> {self.model_label} "
                f"({self.ident}) is not present on this host. Field set to NULL; "
                f"re-link it once the target exists — for a vault file that means "
                f"once its bucket has been mirrored.")

    def as_skipped(self, model, row_uid):
        return (f"Restore: SKIPPED {model._meta.label} uid={row_uid} — required "
                f"field {self._where(model)} points at {self.model_label} "
                f"({self.ident}), which is not present on this host.")


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
                    try:
                        model = apps.get_model(model_info["app"], model_info["model"])
                    except LookupError:
                        # A model that has since been removed — kanban.Column,
                        # for one. Skipping it loses that table's rows; raising
                        # would abort the entire restore inside this atomic
                        # block and lose every other table too.
                        logger.warning(
                            "Skipping %s.%s from the backup: no such model in this "
                            "version. Its rows are not restored.",
                            model_info["app"], model_info["model"],
                        )
                        continue
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

        created = updated = skipped = 0
        for item in objects_data:
            uid = item.get("uid")
            if not uid:
                raise ImproperlyConfigured(
                    f"Missing uid in backup object for {model._meta.label}"
                )
            try:
                fields = self._resolve_fields(model, item.get("fields", {}))
            except UnresolvableReference as exc:
                # A required reference into something this backup never carried.
                # The row cannot be built and nothing can invent the target, so
                # it is dropped — loudly, and by itself. Raising here would abort
                # the surrounding atomic block and lose every other table.
                skipped += 1
                logger.warning("%s", exc.as_skipped(model, uid))
                continue
            _, was_created = model.objects.update_or_create(uid=uid, defaults=fields)
            if was_created:
                created += 1
            else:
                updated += 1

        summary = f"Seeded {model._meta.label}: {created} created, {updated} updated"
        if skipped:
            summary += f", {skipped} SKIPPED (unresolvable required reference)"
        print(summary)

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
            if isinstance(value, dict) and (value.get("__ref__")
                                            or value.get("__natref__")):
                try:
                    if value.get("__natref__"):
                        resolved[field.name] = self._resolve_natural_reference(
                            value, field)
                    else:
                        resolved[field.name] = self._resolve_reference(value, field)
                except UnresolvableReference as exc:
                    if not getattr(field, "null", False):
                        raise
                    logger.warning("%s", exc.as_dropped(model))
                    resolved[field.name] = None
            elif isinstance(value, dict) and value.get("__geo__"):
                try:
                    from django.contrib.gis.geos import GEOSGeometry
                    resolved[field.name] = GEOSGeometry(value["ewkt"]) if value.get("ewkt") else None
                except ImportError:
                    resolved[field.name] = None
            else:
                resolved[field.name] = value
        return resolved

    def _in_backup_set(self, model) -> bool:
        """Whether a restore could ever have created rows of this model.

        Two conditions, and the second is the one that is easy to forget: the
        model must be backup-safe (it has a ``uid``) AND its app must actually be
        in ``APPS_TO_SYNC``. ``vault`` is the standing example — ``VaultFile``
        carries a ``uid`` so that references to it survive as identities, but the
        app is deliberately not synced, because its rows describe encrypted blobs
        that travel by bucket mirroring rather than in a database dump.
        """
        return (model._meta.app_label in self.apps_to_sync
                and self.is_backup_model(model))

    def _resolve_natural_reference(self, value, field=None):
        """Resolve a ``__natref__`` — a reference by the target's own identity.

        Used for models that cannot carry a ``uid`` but are still pointed at from
        the backup set; ``vault.VaultFile`` is the case this was built for, keyed
        on ``(bucket.slug, key)``.

        Unlike a uid reference, this one has a real chance of RESOLVING on the
        far side, because it is the same identity the bucket mirror uses to place
        a file (``mirror.py`` looks a file up with exactly ``filter(bucket=...,
        key=...)``). If the bucket has been mirrored, the link is restored
        properly. If it has not, the caller drops the field to NULL and says so,
        which is the honest answer and an enormous improvement on the previous
        behaviour of writing a stale primary key that pointed at a different file.
        """
        app_label, model_name = value["model"].split(".")
        related_model = apps.get_model(app_label, model_name)
        key = value.get("key") or {}
        ident = ", ".join(f"{k}={v}" for k, v in sorted(key.items()))
        try:
            return related_model.objects.get(**key)
        except (related_model.DoesNotExist, related_model.MultipleObjectsReturned):
            raise UnresolvableReference(value["model"], ident, field)

    def _resolve_reference(self, value, field=None):
        """Turn an exported ``__ref__`` back into an object.

        Three outcomes, and the middle one exists because the alternative was
        losing whole restores:

        * resolved — the ordinary case;
        * **unresolvable by design** — the target's app is not in the backup set,
          so no restore was ever going to contain it. A nullable field becomes
          NULL and a warning names the model, field and uid so the link can be
          re-made by hand; a non-nullable field raises
          :class:`UnresolvableReference`, which the caller turns into a skipped
          ROW rather than a failed restore.
        * missing though it should be there — the target IS in the backup set and
          the row still is not. That is a genuinely inconsistent archive and it
          still raises loudly, because silently dropping data in that case would
          hide the real problem.

        The distinction matters. Before it, five foreign keys into ``vault`` —
        ``socialhub.Community.statute`` among them — would have aborted every
        restore the moment ``VaultFile`` gained its ``uid``, taking every other
        table down with them.
        """
        app_label, model_name = value["model"].split(".")
        related_model = apps.get_model(app_label, model_name)

        if not self._in_backup_set(related_model):
            raise UnresolvableReference(value["model"], value.get("uid"), field)

        try:
            return related_model.objects.get(uid=value["uid"])
        except related_model.DoesNotExist:
            raise ImproperlyConfigured(
                f"Missing referenced object: {value['model']} uid={value['uid']}"
            )
