"""
Drop kanban economy tables after data has been migrated to mission_economy.
Runs after mission_economy.0002_migrate_from_kanban so data is safe before removal.
"""

from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0002_initial"),
        ("mission_economy", "0002_migrate_from_kanban"),
    ]

    operations = [
        migrations.DeleteModel(name="PractitionerAllowance"),
        migrations.DeleteModel(name="ProjectTokenization"),
        migrations.RemoveField(model_name="Practitioner", name="default_income_account"),
    ]
