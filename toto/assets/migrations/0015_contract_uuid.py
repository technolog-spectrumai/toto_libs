import uuid

from django.db import migrations, models


def populate_contract_uuids(apps, schema_editor):
    Contract = apps.get_model("assets", "Contract")
    for c in Contract.objects.filter(uuid=None):
        c.uuid = uuid.uuid4()
        c.save(update_fields=["uuid"])


class Migration(migrations.Migration):
    dependencies = [
        ("assets", "0014_rename_contracttemplate_to_contract"),
    ]

    operations = [
        migrations.AddField(
            model_name="contract",
            name="uuid",
            field=models.UUIDField(null=True, editable=False),
        ),
        migrations.RunPython(populate_contract_uuids, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="contract",
            name="uuid",
            field=models.UUIDField(default=uuid.uuid4, editable=False, unique=True, db_index=True),
        ),
    ]
