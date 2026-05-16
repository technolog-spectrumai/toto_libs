# Generated manually for Palimpsest.

from django.db import migrations, models
import django.db.models.deletion
import trix_editor.fields
import uuid


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("people", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="Page",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(max_length=255)),
                ("slug", models.SlugField(blank=True, unique=True)),
                ("description", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={
                "verbose_name": "Palimpsest page",
                "verbose_name_plural": "Palimpsest pages",
                "ordering": ["-created_at"],
            },
        ),
        migrations.CreateModel(
            name="Tag",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=50, unique=True)),
                ("slug", models.SlugField(blank=True, unique=True)),
            ],
            options={
                "verbose_name": "Palimpsest tag",
                "verbose_name_plural": "Palimpsest tags",
            },
        ),
        migrations.CreateModel(
            name="Section",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(blank=True, max_length=255)),
                ("content", trix_editor.fields.TrixEditorField(blank=True)),
                ("order", models.PositiveIntegerField(default=0)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="palimpsest_sections", to="people.person")),
                ("page", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="sections", to="palimpsest.page")),
                ("tags", models.ManyToManyField(blank=True, related_name="sections", to="palimpsest.tag")),
            ],
            options={
                "ordering": ["order"],
            },
        ),
        migrations.AddField(
            model_name="page",
            name="tags",
            field=models.ManyToManyField(blank=True, related_name="pages", to="palimpsest.tag"),
        ),
    ]
