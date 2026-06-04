"""
Migration: remove BillingUnit model, revert unit FK columns to plain VARCHAR.

Steps:
  1. Add nullable unit_text CharField to TariffItem, UsageRecord, UsageCharge
  2. Populate unit_text from the old BillingUnit.slug via FK
  3. Drop old unit FK columns
  4. Rename unit_text → unit
  5. Make unit non-null
  6. Delete BillingUnit model
"""
import django.db.models.deletion
from django.db import migrations, models


def restore_unit_as_text(apps, schema_editor):
    BillingUnit = apps.get_model("tariffs", "BillingUnit")
    TariffItem = apps.get_model("tariffs", "TariffItem")
    UsageRecord = apps.get_model("tariffs", "UsageRecord")
    UsageCharge = apps.get_model("tariffs", "UsageCharge")

    slug_map = {bu.pk: bu.slug for bu in BillingUnit.objects.all()}

    for obj in TariffItem.objects.all():
        obj.unit_text = slug_map.get(obj.unit_id, "")
        obj.save()

    for obj in UsageRecord.objects.all():
        obj.unit_text = slug_map.get(obj.unit_id, "")
        obj.save()

    for obj in UsageCharge.objects.all():
        obj.unit_text = slug_map.get(obj.unit_id, "")
        obj.save()


class Migration(migrations.Migration):

    dependencies = [
        ("tariffs", "0002_billingunit_model"),
    ]

    operations = [
        # 1. Add transitional text columns
        migrations.AddField(
            model_name="tariffitem",
            name="unit_text",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="usagerecord",
            name="unit_text",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),
        migrations.AddField(
            model_name="usagecharge",
            name="unit_text",
            field=models.CharField(blank=True, max_length=100, null=True),
        ),

        # 2. Copy slug from BillingUnit FK
        migrations.RunPython(restore_unit_as_text, migrations.RunPython.noop),

        # 3. Drop FK columns
        migrations.RemoveField(model_name="tariffitem",  name="unit"),
        migrations.RemoveField(model_name="usagerecord", name="unit"),
        migrations.RemoveField(model_name="usagecharge", name="unit"),

        # 4. Rename unit_text → unit
        migrations.RenameField(model_name="tariffitem",  old_name="unit_text", new_name="unit"),
        migrations.RenameField(model_name="usagerecord", old_name="unit_text", new_name="unit"),
        migrations.RenameField(model_name="usagecharge", old_name="unit_text", new_name="unit"),

        # 5. Make non-null
        migrations.AlterField(
            model_name="tariffitem",
            name="unit",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="usagerecord",
            name="unit",
            field=models.CharField(blank=True, max_length=100),
        ),
        migrations.AlterField(
            model_name="usagecharge",
            name="unit",
            field=models.CharField(blank=True, max_length=100),
        ),

        # 6. Delete BillingUnit model
        migrations.DeleteModel(name="BillingUnit"),
    ]
