"""
Data migration: copy ProjectTokenization and PractitionerAllowance rows from kanban
to mission_economy, preserving PKs so related objects keep working during transition.
Also seeds PractitionerIncomeProfile from Practitioner.default_income_account.
"""

from django.db import migrations


def copy_from_kanban(apps, schema_editor):
    OldTokenization = apps.get_model("kanban", "ProjectTokenization")
    NewTokenization = apps.get_model("mission_economy", "ProjectTokenization")

    for old in OldTokenization.objects.all():
        NewTokenization.objects.get_or_create(
            pk=old.pk,
            defaults=dict(
                project_id=old.project_id,
                asset_id=old.asset_id,
                supervisor_id=old.supervisor_id,
                status=old.status,
                default_reason=old.default_reason,
                default_note=old.default_note,
                defaulted_at=old.defaulted_at,
                defaulted_by_id=old.defaulted_by_id,
                created_at=old.created_at,
                metadata=old.metadata,
            ),
        )

    OldAllowance = apps.get_model("kanban", "PractitionerAllowance")
    NewAllowance = apps.get_model("mission_economy", "PractitionerAllowance")

    for old in OldAllowance.objects.all():
        NewAllowance.objects.get_or_create(
            pk=old.pk,
            defaults=dict(
                practitioner_id=old.practitioner_id,
                payer_account_id=old.payer_account_id,
                recipient_account_id=old.recipient_account_id,
                asset_id=old.asset_id,
                amount_base_units=old.amount_base_units,
                allowance_type=old.allowance_type,
                valid_from=old.valid_from,
                valid_until=old.valid_until,
                active=old.active,
                metadata=old.metadata,
            ),
        )

    OldPractitioner = apps.get_model("kanban", "Practitioner")
    IncomeProfile = apps.get_model("mission_economy", "PractitionerIncomeProfile")

    for p in OldPractitioner.objects.filter(default_income_account__isnull=False):
        IncomeProfile.objects.get_or_create(
            practitioner_id=p.pk,
            defaults={"default_income_account_id": p.default_income_account_id},
        )


def reverse_copy(apps, schema_editor):
    apps.get_model("mission_economy", "ProjectTokenization").objects.all().delete()
    apps.get_model("mission_economy", "PractitionerAllowance").objects.all().delete()
    apps.get_model("mission_economy", "PractitionerIncomeProfile").objects.all().delete()


class Migration(migrations.Migration):

    dependencies = [
        ("mission_economy", "0001_initial"),
        ("kanban", "0002_initial"),
    ]

    operations = [
        migrations.RunPython(copy_from_kanban, reverse_copy),
    ]
