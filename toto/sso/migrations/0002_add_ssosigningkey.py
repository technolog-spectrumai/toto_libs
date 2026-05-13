from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("sso", "0001_initial"),
    ]

    operations = [
        migrations.CreateModel(
            name="SSOSigningKey",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("key_id", models.CharField(max_length=100, unique=True)),
                ("algorithm", models.CharField(default="RS256", max_length=16)),
                ("public_key_pem", models.TextField()),
                ("is_active", models.BooleanField(default=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
            ],
            options={"ordering": ["-created_at"]},
        ),
    ]
