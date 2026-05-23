from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("ravioli", "0003_graphprojectionplan"),
    ]

    operations = [
        migrations.AlterField(
            model_name="graphprojectionplan",
            name="status",
            field=models.CharField(
                choices=[
                    ("ready", "Ready"),
                    ("applying", "Applying…"),
                    ("applied", "Applied"),
                    ("failed", "Failed"),
                ],
                db_index=True,
                default="ready",
                max_length=16,
            ),
        ),
    ]
