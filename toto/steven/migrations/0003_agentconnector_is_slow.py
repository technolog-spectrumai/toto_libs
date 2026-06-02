from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("steven", "0002_remove_agentprofile_graph_rag_fields"),
    ]

    operations = [
        migrations.AddField(
            model_name="agentconnector",
            name="is_slow",
            field=models.BooleanField(
                default=True,
                help_text=(
                    "When enabled (default), inference is queued via Celery and the "
                    "workflow engine. Disable only for connectors with sub-second latency."
                ),
            ),
        ),
    ]
