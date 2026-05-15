import django.db.models.deletion
from django.db import migrations, models


def _remove_secret_password_if_exists(apps, schema_editor):
    """Remove secret_password column from agentconnector if it exists (existing DB upgrade path)."""
    db = schema_editor.connection
    cursor = db.cursor()
    if db.vendor == "sqlite":
        cursor.execute("PRAGMA table_info(steven_agentconnector)")
        columns = [row[1] for row in cursor.fetchall()]
        if "secret_password_id" in columns:
            cursor.execute("ALTER TABLE steven_agentconnector DROP COLUMN secret_password_id")
    else:
        cursor.execute(
            "SELECT column_name FROM information_schema.columns "
            "WHERE table_name='steven_agentconnector' AND column_name='secret_password_id'"
        )
        if cursor.fetchone():
            cursor.execute("ALTER TABLE steven_agentconnector DROP COLUMN secret_password_id")


class Migration(migrations.Migration):

    dependencies = [
        ("steven", "0001_initial"),
        ("gervazy", "0002_add_new_models"),
    ]

    operations = [
        migrations.RunPython(_remove_secret_password_if_exists, migrations.RunPython.noop),
        migrations.AddField(
            model_name="agentconnector",
            name="api_secret",
            field=models.ForeignKey(
                blank=True,
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="agent_connectors",
                to="gervazy.encryptedsecret",
                help_text="Gervazy EncryptedSecret holding the OpenAI API key.",
            ),
        ),
    ]
