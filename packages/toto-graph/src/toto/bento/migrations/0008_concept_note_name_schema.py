"""Straighten the seeded concept/note schema: identifier `title` → `name`.

The ingestor stores an entity's name in the category's identifier property; the
original concept/note seed declared `title`, so the name landed in `extra` and the
required `title` stayed empty (causing validation errors). Rewrite the schema to
`[name, body]` — but ONLY when it still matches the original `[title, body]` shape,
so user-customized schemas are never clobbered.
"""
from django.db import migrations

_OLD_NAMES = ["title", "body"]
_NEW_SCHEMA = [
    {"name": "name", "type": "string", "required": True, "label": "Name"},
    {"name": "body", "type": "text", "required": False, "label": "Body"},
]
_OLD_SCHEMA = [
    {"name": "title", "type": "string", "required": True, "label": "Title"},
    {"name": "body", "type": "text", "required": False, "label": "Body"},
]


def _field_names(schema):
    return [f.get("name") for f in (schema or []) if isinstance(f, dict)]


def _swap(apps, slugs, target_schema, expect_names):
    BentoCategory = apps.get_model("bento", "BentoCategory")
    for cat in BentoCategory.objects.filter(slug__in=slugs):
        if _field_names(cat.property_schema) == expect_names:
            cat.property_schema = target_schema
            cat.save(update_fields=["property_schema", "updated_at"])


def forwards(apps, schema_editor):
    _swap(apps, ["concept", "note"], _NEW_SCHEMA, _OLD_NAMES)


def backwards(apps, schema_editor):
    _swap(apps, ["concept", "note"], _OLD_SCHEMA, ["name", "body"])


class Migration(migrations.Migration):
    dependencies = [
        ("bento", "0007_bentocategory_internal"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
