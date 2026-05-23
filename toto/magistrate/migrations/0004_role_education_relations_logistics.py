from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("magistrate", "0003_role_merchandise"),
    ]

    operations = [
        migrations.AddField(
            model_name="magistraterole",
            name="overseeing_education",
            field=models.BooleanField(
                default=False,
                help_text="Holder oversees community education, academies, and knowledge standards.",
            ),
        ),
        migrations.AddField(
            model_name="magistraterole",
            name="overseeing_relations",
            field=models.BooleanField(
                default=False,
                help_text="Holder oversees inter-community diplomatic relations, treaties, and external liaisons.",
            ),
        ),
        migrations.AddField(
            model_name="magistraterole",
            name="overseeing_logistics",
            field=models.BooleanField(
                default=False,
                help_text="Holder oversees logistics, supply chains, and transport operations.",
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
                    ("education_directive", "Education Directive"),
                    ("relations_directive", "Relations Directive"),
                    ("logistics_order", "Logistics Order"),
                    ("general", "General Directive"),
                ],
                db_index=True, max_length=30,
            ),
        ),
    ]
