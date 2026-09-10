"""Gear becomes Capsule, 2026-09-10.

RENAMES, NOT DROP-AND-CREATE. `RenameModel` and `RenameField` carry the rows
across; the autodetector would otherwise offer to delete a table and make a new
one, which reads the same in a diff and loses every reservation, every runtime
row and the whole refusal history.

Safe to run against a live database because it renames rather than rewrites,
but it must NOT run while an executor is mid-job: `services` reads
`CapsuleRuntime` by name on every status poll.

The persisted refusal codes are rewritten too. They are strings a person reads
in the audit trail, and leaving `too_many_gears` beside a UI that says Capsule
would split one history across two vocabularies for no reason.
"""

from django.db import migrations


def rename_refusal_codes(apps, schema_editor):
    CapsuleEvent = apps.get_model("anastasia", "CapsuleEvent")
    for old, new in (("too_many_gears", "too_many_capsules"),
                     ("gear_full", "capsule_full"),
                     ("too_big_for_gear", "too_big_for_capsule")):
        CapsuleEvent.objects.filter(refusal_code=old).update(refusal_code=new)


def restore_refusal_codes(apps, schema_editor):
    CapsuleEvent = apps.get_model("anastasia", "CapsuleEvent")
    for new, old in (("too_many_capsules", "too_many_gears"),
                     ("capsule_full", "gear_full"),
                     ("too_big_for_capsule", "too_big_for_gear")):
        CapsuleEvent.objects.filter(refusal_code=new).update(refusal_code=old)


class Migration(migrations.Migration):

    dependencies = [("anastasia", "0005_gearruntime_tier")]

    operations = [
        migrations.RenameModel(old_name="GearRuntime", new_name="CapsuleRuntime"),
        migrations.RenameModel(old_name="GearEvent", new_name="CapsuleEvent"),
        # Postgres index names are global per schema, so this is a real rename
        # and not cosmetic: two deployments in one database would collide.
        migrations.RenameIndex(
            model_name="capsuleruntime",
            new_name="anastasia_capsule_state_idx",
            old_name="anastasia_gear_state_idx",
        ),
        migrations.RunPython(rename_refusal_codes, restore_refusal_codes),
    ]
