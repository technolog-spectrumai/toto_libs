"""
Migration: replace BillingUnit TextChoices with a first-class model.

Steps:
  1. Create BillingUnit table
  2. Seed known billing units (data migration)
  3. Add nullable billing_unit FK to TariffItem, UsageRecord, UsageCharge
  4. Populate FK from the existing slug string column
  5. Remove old unit CharField columns
  6. Rename billing_unit → unit (DB column: billing_unit_id → unit_id)
  7. Make unit FK non-null
"""
import django.db.models.deletion
from django.db import migrations, models


SEED_UNITS = [
    ("request",      "Request",       "usage"),
    ("token",        "Token",         "token"),
    ("input_token",  "Input Token",   "token"),
    ("output_token", "Output Token",  "token"),
    ("byte",         "Byte",          "storage"),
    ("kb",           "KB",            "storage"),
    ("mb",           "MB",            "storage"),
    ("gb",           "GB",            "storage"),
    ("second",       "Second",        "time"),
    ("minute",       "Minute",        "time"),
    ("hour",         "Hour",          "time"),
    ("mb_second",    "MB·Second",     "compute"),
    ("mb_minute",    "MB·Minute",     "compute"),
    ("mb_hour",      "MB·Hour",       "storage"),
    ("gb_hour",      "GB·Hour",       "storage"),
    ("node",         "Node",          "graph"),
    ("relationship", "Relationship",  "graph"),
    ("custom",       "Custom",        ""),
]


def seed_billing_units(apps, schema_editor):
    BillingUnit = apps.get_model("tariffs", "BillingUnit")
    for slug, name, trait in SEED_UNITS:
        BillingUnit.objects.get_or_create(slug=slug, defaults={"name": name, "trait": trait})


def populate_billing_unit_fk(apps, schema_editor):
    BillingUnit = apps.get_model("tariffs", "BillingUnit")
    TariffItem = apps.get_model("tariffs", "TariffItem")
    UsageRecord = apps.get_model("tariffs", "UsageRecord")
    UsageCharge = apps.get_model("tariffs", "UsageCharge")

    unit_map = {bu.slug: bu for bu in BillingUnit.objects.all()}

    for obj in TariffItem.objects.all():
        bu = unit_map.get(obj.unit)
        if bu:
            obj.billing_unit = bu
            obj.save()

    for obj in UsageRecord.objects.all():
        bu = unit_map.get(obj.unit)
        if bu:
            obj.billing_unit = bu
            obj.save()

    for obj in UsageCharge.objects.all():
        bu = unit_map.get(obj.unit)
        if bu:
            obj.billing_unit = bu
            obj.save()


class Migration(migrations.Migration):

    dependencies = [
        ("tariffs", "0001_initial"),
    ]

    operations = [
        # 1. Create BillingUnit table
        migrations.CreateModel(
            name="BillingUnit",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("slug", models.SlugField(max_length=50, unique=True)),
                ("name", models.CharField(max_length=100)),
                ("trait", models.CharField(blank=True, max_length=100)),
            ],
            options={
                "verbose_name": "billing unit",
                "verbose_name_plural": "billing units",
                "ordering": ["slug"],
            },
        ),

        # 2. Seed known billing units
        migrations.RunPython(seed_billing_units, migrations.RunPython.noop),

        # 3. Add nullable billing_unit FK columns (transitional name to avoid clash)
        migrations.AddField(
            model_name="tariffitem",
            name="billing_unit",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),
        migrations.AddField(
            model_name="usagerecord",
            name="billing_unit",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),
        migrations.AddField(
            model_name="usagecharge",
            name="billing_unit",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),

        # 4. Populate FK from old slug string
        migrations.RunPython(populate_billing_unit_fk, migrations.RunPython.noop),

        # 5. Drop old unit CharField columns
        migrations.RemoveField(model_name="tariffitem",  name="unit"),
        migrations.RemoveField(model_name="usagerecord", name="unit"),
        migrations.RemoveField(model_name="usagecharge", name="unit"),

        # 6. Rename billing_unit → unit (DB column: billing_unit_id → unit_id)
        migrations.RenameField(model_name="tariffitem",  old_name="billing_unit", new_name="unit"),
        migrations.RenameField(model_name="usagerecord", old_name="billing_unit", new_name="unit"),
        migrations.RenameField(model_name="usagecharge", old_name="billing_unit", new_name="unit"),

        # 7. Make unit FK non-null
        migrations.AlterField(
            model_name="tariffitem",
            name="unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),
        migrations.AlterField(
            model_name="usagerecord",
            name="unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),
        migrations.AlterField(
            model_name="usagecharge",
            name="unit",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="+",
                to="tariffs.billingunit",
            ),
        ),
    ]
