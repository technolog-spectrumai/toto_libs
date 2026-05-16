# Generated manually for simpler community news posts.

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0005_community_news"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="communitynewspost",
            name="is_announcement",
        ),
        migrations.RemoveField(
            model_name="communitynewspost",
            name="pinned",
        ),
        migrations.AlterModelOptions(
            name="communitynewspost",
            options={
                "ordering": ["-created_at"],
                "verbose_name": "Community news post",
                "verbose_name_plural": "Community news posts",
            },
        ),
    ]
