from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("vault", "0014_vaultinvoice_bucket_obligation"),
        ("invoice", "0001_initial"),
    ]

    operations = [
        migrations.DeleteModel(
            name="VaultInvoice",
        ),
    ]
