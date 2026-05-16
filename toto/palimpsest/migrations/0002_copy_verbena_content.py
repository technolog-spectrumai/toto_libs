# Generated manually to preserve existing Verbena content during the move.

from django.db import migrations


def copy_verbena_content(apps, schema_editor):
    try:
        VerbenaPage = apps.get_model("verbena", "Page")
        VerbenaSection = apps.get_model("verbena", "Section")
        VerbenaTag = apps.get_model("verbena", "Tag")
    except LookupError:
        return

    Page = apps.get_model("palimpsest", "Page")
    Section = apps.get_model("palimpsest", "Section")
    Tag = apps.get_model("palimpsest", "Tag")

    for old_tag in VerbenaTag.objects.all():
        Tag.objects.update_or_create(
            id=old_tag.id,
            defaults={
                "uid": old_tag.uid,
                "name": old_tag.name,
                "slug": old_tag.slug,
            },
        )

    for old_page in VerbenaPage.objects.all():
        page, _ = Page.objects.update_or_create(
            id=old_page.id,
            defaults={
                "uid": old_page.uid,
                "title": old_page.title,
                "slug": old_page.slug,
                "description": old_page.description,
                "created_at": old_page.created_at,
            },
        )
        page.tags.set(Tag.objects.filter(id__in=old_page.tags.values_list("id", flat=True)))

    for old_section in VerbenaSection.objects.all():
        section, _ = Section.objects.update_or_create(
            id=old_section.id,
            defaults={
                "uid": old_section.uid,
                "title": old_section.title,
                "content": old_section.content,
                "author_id": old_section.author_id,
                "order": old_section.order,
                "page_id": old_section.page_id,
            },
        )
        section.tags.set(Tag.objects.filter(id__in=old_section.tags.values_list("id", flat=True)))


class Migration(migrations.Migration):

    dependencies = [
        ("palimpsest", "0001_initial"),
        ("verbena", "0002_alter_section_author"),
    ]

    operations = [
        migrations.RunPython(copy_verbena_content, migrations.RunPython.noop),
    ]
