from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ("kanban", "0005_alter_campaign_zone_alter_mission_location_and_more"),
        ("assets", "0011_tokenization_default_state"),
    ]

    operations = [
        migrations.AddField(
            model_name="practitioner",
            name="default_income_account",
            field=models.ForeignKey(
                blank=True,
                help_text="Default account receiving salary, vesting releases, bonuses, revenue-share payouts, or lease/subscription payments.",
                null=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name="practitioner_income_accounts",
                to="assets.ledgeraccount",
            ),
        ),
        migrations.AddField(
            model_name="practitioner",
            name="work_description",
            field=models.TextField(
                blank=True,
                help_text="Free-text description of this practitioner's role or services in the project.",
            ),
        ),
    ]
