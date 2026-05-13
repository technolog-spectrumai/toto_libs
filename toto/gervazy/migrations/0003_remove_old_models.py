from django.db import migrations


def _nullify_orphaned_fks(apps, schema_editor):
    """Nullify any FKs to old gervazy models in apps that may not be installed in every project."""
    db = schema_editor.connection
    cursor = db.cursor()

    # steven.AgentConnector may reference SecretPassword — null it out if the table exists
    cursor.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='steven_agentconnector'"
        if db.vendor == 'sqlite'
        else "SELECT table_name FROM information_schema.tables WHERE table_name='steven_agentconnector'"
    )
    if cursor.fetchone():
        cursor.execute("UPDATE steven_agentconnector SET secret_password_id = NULL")


class Migration(migrations.Migration):
    """Remove old insecure models: KeyRing, RSAKeyPair (plaintext private key),
    SecretKey (PBKDF2/Fernet), EnvironmentVariable (os.environ injection), SecretPassword."""

    dependencies = [
        ("gervazy", "0002_add_new_models"),
        ("core", "0002_update_platform_add_apiconnector"),
        ("socialhub", "0002_update_emailservice"),
    ]

    operations = [
        migrations.RunPython(_nullify_orphaned_fks, migrations.RunPython.noop),
        migrations.DeleteModel(name="SecretPassword"),
        migrations.DeleteModel(name="SecretKey"),
        migrations.DeleteModel(name="EnvironmentVariable"),
        migrations.DeleteModel(name="RSAKeyPair"),
        migrations.DeleteModel(name="KeyRing"),
    ]
