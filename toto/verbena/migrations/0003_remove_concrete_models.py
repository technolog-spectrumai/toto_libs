# Generated manually after moving concrete writing models to Palimpsest.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("verbena", "0002_alter_section_author"),
        ("academy", "0004_coursemodule_palimpsest_page"),
        ("library", "0004_use_palimpsest_tags"),
    ]

    operations = [
        migrations.DeleteModel(name="Section"),
        migrations.DeleteModel(name="Page"),
        migrations.DeleteModel(name="Tag"),
    ]
