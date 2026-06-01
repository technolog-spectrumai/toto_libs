from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("mandragora", "0001_initial"),
    ]

    operations = [
        migrations.AddField(
            model_name="computekernel",
            name="startup_timeout_ms",
            field=models.IntegerField(
                default=120000,
                help_text=(
                    "How long to wait for the kernel process to become ready, in milliseconds. "
                    "Increase this for slow Docker environments or when installing many dependencies."
                ),
            ),
        ),
    ]
