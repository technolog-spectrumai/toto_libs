from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0004_workflowconnector_workflownode_connector"),
    ]

    operations = [
        migrations.AlterField(
            model_name="workflowconnector",
            name="connector_type",
            field=models.CharField(
                choices=[
                    ("file_read", "File read"),
                    ("file_write", "File write"),
                    ("api_request", "API request"),
                    ("people_read", "People read"),
                    ("socialhub_read", "Socialhub read"),
                    ("locations_read", "Locations read"),
                    ("events_read", "Events read"),
                ],
                max_length=40,
            ),
        ),
    ]
