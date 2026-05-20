from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0002_initial"),
        ("people", "0002_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="task",
            name="reviewer",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional reviewer who signs off the task before it is completed.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="review_tasks",
                to="people.person",
            ),
        ),
    ]
