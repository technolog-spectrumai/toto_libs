from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("bento", "0005_remove_bentocategory_color_remove_bentocategory_icon_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="bentocategory",
            name="searchable_properties",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Property names (declared in property_schema) whose "
                "values identify a node in free text — used by the Ingestor to "
                "detect existing nodes of this category. Empty falls back to "
                "name/title/label by convention.",
            ),
        ),
        migrations.AddField(
            model_name="bentocategory",
            name="alias_property",
            field=models.CharField(
                blank=True,
                default="",
                help_text="Optional property holding alternate surface forms for "
                "a node (a JSON list or a comma-separated string). Also fed to "
                "detection.",
                max_length=120,
            ),
        ),
        migrations.AddField(
            model_name="bentoedgetype",
            name="trigger_lemmas",
            field=models.JSONField(
                blank=True,
                default=list,
                help_text="Optional lemma triggers (e.g. ['support', 'back']). "
                "When one appears in a sentence between two endpoints whose "
                "categories match this edge, the Ingestor proposes this "
                "relationship and raises its confidence.",
            ),
        ),
    ]
