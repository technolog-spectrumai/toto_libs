import django.db.models.deletion
import django.utils.timezone
from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
        ("assembly", "0003_emergency_status_source_proposal"),
        ("people", "0003_person_is_federal_agent"),
        ("socialhub", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="MagistrateRole",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("name", models.CharField(max_length=100, unique=True)),
                ("slug", models.SlugField(blank=True, max_length=120, unique=True)),
                ("description", models.TextField(blank=True)),
                ("icon", models.CharField(default="fa-solid fa-scroll", max_length=100)),
                ("overseeing_mobilization", models.BooleanField(default=False, help_text="Holder may act on emergency declarations without full assembly vote.")),
                ("overseeing_tribunal", models.BooleanField(default=False, help_text="Holder oversees tribunal proceedings.")),
                ("overseeing_trade", models.BooleanField(default=False, help_text="Holder oversees trade and commerce.")),
                ("overseeing_finance", models.BooleanField(default=False, help_text="Holder oversees community finances and taxation.")),
                ("overseeing_public_order", models.BooleanField(default=False, help_text="Holder oversees public order and safety.")),
                ("overseeing_legislation", models.BooleanField(default=False, help_text="Holder may propose and fast-track legislation.")),
                ("order", models.PositiveIntegerField(default=0)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["order", "name"], "verbose_name": "Magistrate role", "verbose_name_plural": "Magistrate roles"},
        ),
        migrations.CreateModel(
            name="Magistrate",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("status", models.CharField(choices=[("active", "Active"), ("suspended", "Suspended"), ("impeached", "Impeached"), ("term_ended", "Term Ended")], db_index=True, default="active", max_length=20)),
                ("term_start", models.DateField()),
                ("term_end", models.DateField(blank=True, null=True)),
                ("elected_at", models.DateTimeField(default=django.utils.timezone.now)),
                ("notes", models.TextField(blank=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("person", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="magistrate_seats", to="people.person")),
                ("role", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="holders", to="magistrate.magistraterole")),
                ("community", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="magistrates", to="socialhub.community")),
                ("source_proposal", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="elected_magistrates", to="assembly.assemblyproposal")),
            ],
            options={"ordering": ["-elected_at"], "verbose_name": "Magistrate", "verbose_name_plural": "Magistrates"},
        ),
        migrations.AddIndex(
            model_name="magistrate",
            index=models.Index(fields=["community", "status"], name="magistrate__communi_idx"),
        ),
        migrations.AddIndex(
            model_name="magistrate",
            index=models.Index(fields=["role", "status"], name="magistrate__role_idx"),
        ),
        migrations.CreateModel(
            name="MagistrateReport",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("title", models.CharField(max_length=255)),
                ("body", models.TextField()),
                ("reporting_period_start", models.DateField(blank=True, null=True)),
                ("reporting_period_end", models.DateField(blank=True, null=True)),
                ("status", models.CharField(choices=[("draft", "Draft"), ("submitted", "Submitted"), ("acknowledged", "Acknowledged")], db_index=True, default="draft", max_length=20)),
                ("submitted_at", models.DateTimeField(blank=True, null=True)),
                ("acknowledged_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("magistrate", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="reports", to="magistrate.magistrate")),
                ("acknowledged_by", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="acknowledged_magistrate_reports", to="people.person")),
            ],
            options={"ordering": ["-created_at"], "verbose_name": "Magistrate report", "verbose_name_plural": "Magistrate reports"},
        ),
    ]
