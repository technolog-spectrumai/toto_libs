from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("workflows", "0002_lambdafunction_move"),
    ]

    operations = [
        migrations.AddField(
            model_name="workflownoderun",
            name="celery_task_id",
            field=models.CharField(blank=True, max_length=255),
        ),
    ]
