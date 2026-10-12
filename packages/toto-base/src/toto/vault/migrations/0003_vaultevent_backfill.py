"""A file made before the timeline has no history: give each one its first
row, **created**, from what the file itself says (its owner, when it was
uploaded). Files that have a created row already are left alone; mirrored
rows of another server's listing are nobody's act here and get none."""

from django.db import migrations


def backfill(apps, schema_editor):
    VaultEvent = apps.get_model("vault", "VaultEvent")
    VaultFile = apps.get_model("vault", "VaultFile")
    have = set(VaultEvent.objects.filter(kind="created").values_list("file_number", flat=True))
    # _base_manager: the trashed ones too.
    files = (VaultFile._base_manager.exclude(bucket__isnull=True).exclude(origin="mirror")
             .select_related("owner").iterator())
    for vault_file in files:
        if vault_file.pk in have:
            continue
        owner = vault_file.owner
        event = VaultEvent.objects.create(
            bucket_id=vault_file.bucket_id, file_id=vault_file.pk, file_number=vault_file.pk,
            file_title=(vault_file.title or "")[:255], kind="created", actor=owner,
            actor_name=(owner.username if owner is not None else "")[:150], details={})
        if vault_file.uploaded_at:
            # ``at`` is stamped at creation: put the file's own time there.
            VaultEvent.objects.filter(pk=event.pk).update(at=vault_file.uploaded_at)


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0002_vaultevent"),
    ]

    operations = [
        migrations.RunPython(backfill, migrations.RunPython.noop),
    ]
