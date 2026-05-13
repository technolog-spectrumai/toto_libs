from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0003_alter_apiconnector_api_secret_and_more"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="platform",
            name="api_connector",
        ),
        migrations.DeleteModel(
            name="ApiConnector",
        ),
    ]
