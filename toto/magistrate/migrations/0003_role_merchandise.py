from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("magistrate", "0002_magistratedecision"),
    ]

    operations = [
        migrations.AddField(
            model_name="magistraterole",
            name="overseeing_merchandise",
            field=models.BooleanField(
                default=False,
                help_text="Holder may inspect merchandise quality and impose fines. Fines may be contested before the tribunal.",
            ),
        ),
        migrations.AlterField(
            model_name="magistratedecision",
            name="decision_type",
            field=models.CharField(
                choices=[
                    ("mobilization_call", "Mobilization Call"),
                    ("emergency_declare", "Emergency Declaration"),
                    ("tribunal_order", "Tribunal Order"),
                    ("trade_order", "Trade Order"),
                    ("trade_reversal", "Trade Reversal Order"),
                    ("merchandise_fine", "Merchandise Quality Fine"),
                    ("finance_directive", "Finance Directive"),
                    ("public_order_directive", "Public Order Directive"),
                    ("legislation_fast_track", "Legislation Fast-Track"),
                    ("general", "General Directive"),
                ],
                db_index=True, max_length=30,
            ),
        ),
    ]
