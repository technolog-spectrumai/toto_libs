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

    ``toto.api.cors.in_data_mesh`` gates every mesh read on membership of exactly this
    group, and nothing else creates it, so it is seeded here, on top of the fresh
    initial migration of the 2026-10-01 reset (the autodetector never writes data).
    ``get_or_create`` keeps it idempotent.
    """

    dependencies = [
        ("api", "0001_initial"),
        ("auth", "0001_initial"),
    ]

    operations = [
        migrations.RunPython(create_group, remove_group),
    ]
