from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("vod", "0005_rename_vod_vodacce_user_id_0b5e67_idx_vod_vodacce_user_id_c35240_idx_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="vodaccessgrant",
            name="invoice",
        ),
        migrations.RemoveField(
            model_name="vodaccessgrant",
            name="subscription",
        ),
        migrations.RemoveIndex(
            model_name="vodaccessgrant",
            name="vod_vodacce_invoice_id_idx",
        ),
        migrations.RemoveIndex(
            model_name="vodaccessgrant",
            name="vod_vodacce_subscri_idx",
        ),
    ]
