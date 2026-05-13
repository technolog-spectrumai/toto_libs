from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("socialhub", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.CreateModel(
                    name="Experience",
                    fields=[
                        ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                        ("title", models.CharField(max_length=200)),
                        ("institution", models.CharField(blank=True, max_length=200)),
                        ("place", models.CharField(blank=True, max_length=200)),
                        ("started_at", models.DateField(blank=True, null=True)),
                        ("ended_at", models.DateField(blank=True, null=True)),
                        ("is_current", models.BooleanField(default=False)),
                        ("description", models.TextField(blank=True)),
                        ("order", models.PositiveIntegerField(default=0)),
                        (
                            "person",
                            models.ForeignKey(
                                on_delete=django.db.models.deletion.CASCADE,
                                related_name="experiences",
                                to="socialhub.person",
                            ),
                        ),
                    ],
                    options={"ordering": ["order", "-started_at", "title"]},
                ),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="ALTER TABLE socialhub_experience RENAME TO academy_experience",
                    reverse_sql="ALTER TABLE academy_experience RENAME TO socialhub_experience",
                ),
            ],
        ),
    ]
