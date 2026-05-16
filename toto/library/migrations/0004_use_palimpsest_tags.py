# Generated manually after moving concrete tags to Palimpsest.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("library", "0003_alter_article_authors_alter_book_authors"),
        ("palimpsest", "0002_copy_verbena_content"),
    ]

    operations = [
        migrations.AlterField(
            model_name="article",
            name="tags",
            field=models.ManyToManyField(blank=True, related_name="%(class)s_items", to="palimpsest.tag"),
        ),
        migrations.AlterField(
            model_name="book",
            name="tags",
            field=models.ManyToManyField(blank=True, related_name="%(class)s_items", to="palimpsest.tag"),
        ),
    ]
