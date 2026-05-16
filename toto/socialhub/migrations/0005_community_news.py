# Generated manually for SocialHub community news.

from django.db import migrations, models
import django.db.models.deletion
import trix_editor.fields
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ("people", "0001_initial"),
        ("palimpsest", "0002_copy_verbena_content"),
        ("socialhub", "0004_community_senior_members"),
    ]

    operations = [
        migrations.CreateModel(
            name="CommunityNewsTopic",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("name", models.CharField(max_length=50, unique=True)),
                ("slug", models.SlugField(blank=True, unique=True)),
            ],
            options={
                "verbose_name": "Community news topic",
                "verbose_name_plural": "Community news topics",
            },
        ),
        migrations.CreateModel(
            name="CommunityNewsPost",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("uid", models.UUIDField(default=uuid.uuid4, editable=False, unique=True)),
                ("title", models.CharField(blank=True, max_length=255)),
                ("content", trix_editor.fields.TrixEditorField(blank=True)),
                ("order", models.PositiveIntegerField(default=0)),
                ("visibility", models.CharField(choices=[("public", "Public"), ("community", "Community")], default="public", max_length=20)),
                ("is_announcement", models.BooleanField(default=False)),
                ("pinned", models.BooleanField(default=False)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
                ("author", models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="community_news_posts", to="people.person")),
                ("community", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="news_posts", to="socialhub.community")),
                ("source_page", models.ForeignKey(blank=True, help_text="Optional long-form Palimpsest page this post points to.", null=True, on_delete=django.db.models.deletion.SET_NULL, related_name="community_news_posts", to="palimpsest.page")),
                ("topics", models.ManyToManyField(blank=True, related_name="posts", to="socialhub.communitynewstopic")),
            ],
            options={
                "verbose_name": "Community news post",
                "verbose_name_plural": "Community news posts",
                "ordering": ["-pinned", "-created_at"],
            },
        ),
    ]
