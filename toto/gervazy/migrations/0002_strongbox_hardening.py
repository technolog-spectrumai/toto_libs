from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
from django.utils import timezone
import toto.gervazy.models


def populate_wrapped_data_key_vmk(apps, schema_editor):
    WrappedDataKey = apps.get_model("gervazy", "WrappedDataKey")
    VaultMasterKey = apps.get_model("gervazy", "VaultMasterKey")

    for wrapped_key in WrappedDataKey.objects.all().iterator():
        vmk = VaultMasterKey.objects.filter(
            strongbox_id=wrapped_key.strongbox_id,
            version=wrapped_key.vmk_version,
        ).first()
        if vmk is None:
            raise RuntimeError(
                "Cannot migrate WrappedDataKey "
                f"{wrapped_key.pk}: missing VMK v{wrapped_key.vmk_version} "
                f"for strongbox {wrapped_key.strongbox_id}."
            )
        wrapped_key.vmk_id = vmk.pk
        wrapped_key.save(update_fields=["vmk"])


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ("gervazy", "0001_initial"),
    ]

    operations = [
        migrations.RenameModel(
            old_name="UserVault",
            new_name="UserStrongbox",
        ),
        migrations.RenameField(
            model_name="vaultmasterkey",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.RenameField(
            model_name="wrappeddatakey",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.RenameField(
            model_name="encryptedsecret",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.RenameField(
            model_name="encryptedfile",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.RenameField(
            model_name="encryptedprivatekey",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.RenameField(
            model_name="cryptoauditlog",
            old_name="vault",
            new_name="strongbox",
        ),
        migrations.AlterModelOptions(
            name="encryptedfile",
            options={"ordering": ["-uploaded_at"]},
        ),
        migrations.AlterModelOptions(
            name="encryptedprivatekey",
            options={"ordering": ["key_id"]},
        ),
        migrations.AlterModelOptions(
            name="encryptedsecret",
            options={"ordering": ["name"]},
        ),
        migrations.AlterUniqueTogether(
            name="encryptedfilechunk",
            unique_together=set(),
        ),
        migrations.AlterField(
            model_name="userstrongbox",
            name="owner",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.CASCADE,
                related_name="user_strongboxes",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="userstrongbox",
            name="name",
            field=models.CharField(max_length=128),
        ),
        migrations.AlterField(
            model_name="userstrongbox",
            name="salt",
            field=models.BinaryField(
                default=toto.gervazy.models.random_16_byte_salt,
                editable=False,
                help_text="Random 16-byte KDF salt.",
            ),
        ),
        migrations.AlterField(
            model_name="userstrongbox",
            name="argon2_memory_cost",
            field=models.PositiveIntegerField(
                default=65536,
                help_text="Argon2id memory cost in KiB.",
            ),
        ),
        migrations.AddField(
            model_name="userstrongbox",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="encryptedsecret",
            name="rotated_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="encryptedsecret",
            name="updated_at",
            field=models.DateTimeField(auto_now=True, default=timezone.now),
            preserve_default=False,
        ),
        migrations.AddField(
            model_name="wrappeddatakey",
            name="vmk",
            field=models.ForeignKey(
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="wrapped_data_keys",
                to="gervazy.vaultmasterkey",
            ),
        ),
        migrations.RunPython(populate_wrapped_data_key_vmk, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="wrappeddatakey",
            name="vmk",
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name="wrapped_data_keys",
                to="gervazy.vaultmasterkey",
            ),
        ),
        migrations.RemoveField(
            model_name="wrappeddatakey",
            name="vmk_version",
        ),
        migrations.AlterField(
            model_name="cryptoauditlog",
            name="actor",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="crypto_audit_logs",
                to=settings.AUTH_USER_MODEL,
            ),
        ),
        migrations.AlterField(
            model_name="cryptoauditlog",
            name="strongbox",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="audit_logs",
                to="gervazy.userstrongbox",
            ),
        ),
        migrations.AlterField(
            model_name="cryptoauditlog",
            name="reason",
            field=models.TextField(blank=True, help_text="Do not store secret values here."),
        ),
        migrations.AlterField(
            model_name="encryptedfile",
            name="file",
            field=models.FileField(upload_to="strongbox/encrypted/"),
        ),
        migrations.AlterField(
            model_name="encryptedfile",
            name="original_name_nonce",
            field=models.BinaryField(default=toto.gervazy.models.random_12_byte_nonce),
        ),
        migrations.AlterField(
            model_name="encryptedfilechunk",
            name="nonce",
            field=models.BinaryField(default=toto.gervazy.models.random_12_byte_nonce),
        ),
        migrations.AlterField(
            model_name="encryptedprivatekey",
            name="nonce",
            field=models.BinaryField(default=toto.gervazy.models.random_12_byte_nonce),
        ),
        migrations.AlterField(
            model_name="encryptedsecret",
            name="aad",
            field=models.BinaryField(
                blank=True,
                default=b"",
                help_text="Additional authenticated data. Bind ciphertext to context.",
            ),
        ),
        migrations.AlterField(
            model_name="encryptedsecret",
            name="nonce",
            field=models.BinaryField(
                default=toto.gervazy.models.random_12_byte_nonce,
                help_text="Random 12-byte AES-GCM nonce. Must be unique per wrapped_key.",
            ),
        ),
        migrations.AlterField(
            model_name="encryptedsecret",
            name="purpose",
            field=models.CharField(
                blank=True,
                help_text="Example: smtp_password, api_token, oauth_client_secret.",
                max_length=255,
            ),
        ),
        migrations.AlterField(
            model_name="vaultmasterkey",
            name="nonce",
            field=models.BinaryField(default=toto.gervazy.models.random_12_byte_nonce),
        ),
        migrations.AlterField(
            model_name="wrappeddatakey",
            name="nonce",
            field=models.BinaryField(default=toto.gervazy.models.random_12_byte_nonce),
        ),
        migrations.AddConstraint(
            model_name="userstrongbox",
            constraint=models.UniqueConstraint(
                fields=("owner", "name"),
                name="unique_strongbox_name_per_owner",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedfile",
            constraint=models.UniqueConstraint(
                fields=("wrapped_key", "original_name_nonce"),
                name="unique_file_name_nonce_per_wrapped_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedfilechunk",
            constraint=models.UniqueConstraint(
                fields=("encrypted_file", "index"),
                name="unique_chunk_index_per_file",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedfilechunk",
            constraint=models.UniqueConstraint(
                fields=("encrypted_file", "nonce"),
                name="unique_chunk_nonce_per_file",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedprivatekey",
            constraint=models.UniqueConstraint(
                fields=("wrapped_key", "nonce"),
                name="unique_private_key_nonce_per_wrapped_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedsecret",
            constraint=models.UniqueConstraint(
                fields=("strongbox", "name"),
                name="unique_secret_name_per_strongbox",
            ),
        ),
        migrations.AddConstraint(
            model_name="encryptedsecret",
            constraint=models.UniqueConstraint(
                fields=("wrapped_key", "nonce"),
                name="unique_secret_nonce_per_wrapped_key",
            ),
        ),
        migrations.AddConstraint(
            model_name="vaultmasterkey",
            constraint=models.UniqueConstraint(
                fields=("strongbox", "version"),
                name="unique_vmk_version_per_strongbox",
            ),
        ),
        migrations.AddConstraint(
            model_name="vaultmasterkey",
            constraint=models.UniqueConstraint(
                fields=("strongbox", "nonce"),
                name="unique_vmk_nonce_per_strongbox",
            ),
        ),
        migrations.AddConstraint(
            model_name="wrappeddatakey",
            constraint=models.UniqueConstraint(
                fields=("strongbox", "version"),
                name="unique_dek_version_per_strongbox",
            ),
        ),
        migrations.AddConstraint(
            model_name="wrappeddatakey",
            constraint=models.UniqueConstraint(
                fields=("vmk", "nonce"),
                name="unique_dek_nonce_per_vmk",
            ),
        ),
    ]
