"""The stored "which capsule" key, renamed inside the JSON, 2026-09-10.

`gears.KEY` moved from "gear" to "capsule". The value lives in
`Workspace.settings`, a JSONField keyed by namespace — so this is not a schema
change and the autodetector will never notice it. Without this, every workspace
that had chosen a capsule silently reverts to AUTOMATIC.

That failure is quiet and then loud in the wrong place: AUTOMATIC on an account
holding two mounted capsules is precisely the ambiguity `require_gear` refuses,
so jobs start failing with "say which one to use" and nothing connects it to a
rename. `gears.LEGACY_KEY` covers the same gap in code for rows this misses.

Reversible on purpose. A migration that cannot be rolled back is one nobody
dares run on a Friday.
"""

from django.db import migrations


def _move(apps, old, new):
    Workspace = apps.get_model("ambrosia", "Workspace")
    moved = 0
    # Only rows that actually carry the key: `settings` is free-form, most
    # workspaces have never set one, and rewriting every row to change none of
    # them is how a migration times out on a large table.
    for workspace in Workspace.objects.exclude(settings={}).iterator():
        stored = workspace.settings or {}
        if not isinstance(stored, dict):
            continue
        touched = False
        for namespace, section in stored.items():
            if not isinstance(section, dict) or old not in section:
                continue
            # Do not clobber a value already written under the new name by a
            # deployment that ran the code before the migration.
            section.setdefault(new, section.pop(old))
            section.pop(old, None)
            touched = True
        if touched:
            workspace.settings = stored
            workspace.save(update_fields=["settings"])
            moved += 1
    return moved


def to_capsule(apps, schema_editor):
    _move(apps, "gear", "capsule")


def back_to_gear(apps, schema_editor):
    _move(apps, "capsule", "gear")


class Migration(migrations.Migration):

    dependencies = [("ambrosia", "0004_workspacehibernation")]

    operations = [migrations.RunPython(to_capsule, back_to_gear)]
