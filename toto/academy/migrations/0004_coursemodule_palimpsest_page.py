# Generated manually after moving concrete writing pages to Palimpsest.

from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("academy", "0003_alter_certificate_person_alter_script_author_and_more"),
        ("palimpsest", "0002_copy_verbena_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="coursemodule",
            name="verbena_page",
            field=models.ForeignKey(
                blank=True,
                help_text="Optional notes, article, or handout page for this module.",
                null=True,
                on_delete=django.db.models.deletion.SET_NULL,
                related_name="academy_modules",
                to="palimpsest.page",
            ),
        ),
    ]
