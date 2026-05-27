from django.db import migrations, models


OLD_TO_NEW_COLLECTION = {
    "public": "public",
    "unlisted": "public",
    "subscribers": "private",
    "invoice": "private",
    "staff": "private",
    "protected": "private",
}


def migrate_access_modes(apps, schema_editor):
    VodCollection = apps.get_model("vod", "VodCollection")
    for obj in VodCollection.objects.all():
        obj.access_mode = OLD_TO_NEW_COLLECTION.get(obj.access_mode, "public")
        obj.save(update_fields=["access_mode"])


class Migration(migrations.Migration):
    dependencies = [
        ("vod", "0002_remove_subscription_usage_add_views_count"),
    ]

    operations = [
        # Remove indexes that reference removed fields
        migrations.RemoveIndex(model_name="vodcollection", name="vod_vodcoll_require_b75d31_idx"),
        migrations.RemoveIndex(model_name="vodvideo", name="vod_vodvide_require_ea04c0_idx"),

        # Drop plan/invoice/usage fields from VodCollection
        migrations.RemoveField(model_name="vodcollection", name="required_plan"),
        migrations.RemoveField(model_name="vodcollection", name="usage_feature_code"),
        migrations.RemoveField(model_name="vodcollection", name="invoice_amount"),
        migrations.RemoveField(model_name="vodcollection", name="invoice_currency_label"),

        # Drop plan/usage/invoice fields from VodVideo
        migrations.RemoveField(model_name="vodvideo", name="required_plan"),
        migrations.RemoveField(model_name="vodvideo", name="usage_feature_code"),
        migrations.RemoveField(model_name="vodvideo", name="invoice_amount"),
        migrations.RemoveField(model_name="vodvideo", name="invoice_currency_label"),
        # Access is collection-level only — drop per-video access_mode
        migrations.RemoveIndex(model_name="vodvideo", name="vod_vodvide_collect_806abc_idx"),
        migrations.RemoveField(model_name="vodvideo", name="access_mode"),
        migrations.AddIndex(
            model_name="vodvideo",
            index=models.Index(fields=["collection", "status"], name="vod_vodvide_collect_status_idx"),
        ),

        # Data-migrate existing access_mode values
        migrations.RunPython(migrate_access_modes, migrations.RunPython.noop),

        # Alter access_mode choices to new 3-value set
        migrations.AlterField(
            model_name="vodcollection",
            name="access_mode",
            field=models.CharField(
                choices=[("public", "Public"), ("private", "Private — readers list only")],
                default="public",
                max_length=24,
            ),
        ),
    ]
