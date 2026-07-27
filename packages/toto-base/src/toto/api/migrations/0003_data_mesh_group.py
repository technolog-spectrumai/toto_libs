from django.db import migrations


def create_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.get_or_create(name="data_mesh")


def remove_group(apps, schema_editor):
    Group = apps.get_model("auth", "Group")
    Group.objects.filter(name="data_mesh").delete()


class Migration(migrations.Migration):
    """Create the `data_mesh` group: its members may read the gated/synced data directly
    from the server; everyone else must pull it from a peer (the decentralized data mesh).

    This lived in ``telegraph.0002_data_mesh_group`` until the forum rework. It is not chat
    data — the group gates ``toto.api.cors`` and is consumed across the suite — so it moved
    here when telegraph's migration history was squashed. Hosts that already ran the old
    migration are unaffected: ``get_or_create`` is idempotent.
    """

    dependencies = [
        ("api", "0002_initial"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_group, remove_group),
    ]
