import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0001_initial"),
        ("gervazy", "0002_add_new_models"),
    ]

    operations = [
        # Remove old SecretPassword FK
        migrations.RemoveField(model_name="emailservice", name="secret_password"),

        # Add new EncryptedSecret FK
        migrations.AddField(
            model_name="emailservice",
            name="smtp_secret",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="email_services",
                to="gervazy.encryptedsecret",
                help_text="Gervazy EncryptedSecret holding the SMTP password.",
            ),
        ),
    ]
