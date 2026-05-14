import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("gervazy", "0002_add_new_models"),
        ("sso", "0002_add_ssosigningkey"),
    ]

    operations = [
        migrations.AddField(
            model_name="ssosigningkey",
            name="encrypted_key",
            field=models.OneToOneField(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="sso_signing_key",
                to="gervazy.encryptedprivatekey",
                help_text="Gervazy EncryptedPrivateKey holding the RSA private key.",
            ),
        ),
    ]
