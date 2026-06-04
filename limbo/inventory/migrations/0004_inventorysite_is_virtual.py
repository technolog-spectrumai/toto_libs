from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("inventory", "0003_objecttype_category_objecttype_is_mobile"),
    ]

    operations = [
        migrations.AddField(
            model_name="inventorysite",
            name="is_virtual",
            field=models.BooleanField(
                default=False,
                help_text="Virtual sites have no physical location (e.g. cloud storage, in-transit, write-off pool).",
            ),
        ),
        migrations.AddIndex(
            model_name="inventorysite",
            index=models.Index(fields=["is_virtual"], name="inventory_i_is_virt_idx"),
        ),
    ]
