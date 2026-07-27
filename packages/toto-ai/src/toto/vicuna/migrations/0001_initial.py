from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="OllamaServer",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=120, unique=True)),
                ("host", models.URLField(default="http://localhost:11434", help_text="Ollama base URL, e.g. http://localhost:11434", unique=True)),
                ("uses_gpu", models.BooleanField(default=False, help_text="Whether this Ollama instance runs on GPU.")),
                ("is_active", models.BooleanField(default=True)),
                ("last_synced", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={"ordering": ["name"]},
        ),
        migrations.CreateModel(
            name="OllamaModel",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=200)),
                ("size_bytes", models.BigIntegerField(blank=True, help_text="Size reported by Ollama /api/tags.", null=True)),
                ("last_seen", models.DateTimeField(auto_now=True, help_text="Last time this model appeared in /api/tags.")),
                ("server", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="models", to="vicuna.ollamaserver")),
            ],
            options={"ordering": ["server", "name"], "unique_together": {("server", "name")}},
        ),
    ]
