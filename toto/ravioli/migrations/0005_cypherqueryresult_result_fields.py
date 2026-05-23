from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ravioli", "0004_graphprojectionplan_applying_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="cypherqueryresult",
            name="result_nodes",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cypherqueryresult",
            name="result_edges",
            field=models.JSONField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cypherqueryresult",
            name="last_run_at",
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name="cypherqueryresult",
            name="error",
            field=models.TextField(blank=True),
        ),
        migrations.AlterField(
            model_name="cypherqueryresult",
            name="query",
            field=models.ForeignKey(
                on_delete=models.deletion.CASCADE,
                related_name="results",
                to="ravioli.cypherquery",
            ),
        ),
    ]
