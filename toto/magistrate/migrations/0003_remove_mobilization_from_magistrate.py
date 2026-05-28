from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('magistrate', '0002_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='magistraterole',
            name='overseeing_mobilization',
        ),
        migrations.AlterField(
            model_name='magistratedecision',
            name='decision_type',
            field=models.CharField(
                choices=[
                    ('emergency_declare', 'Emergency Declaration'),
                    ('tribunal_order', 'Tribunal Order'),
                    ('trade_order', 'Trade Order'),
                    ('finance_directive', 'Finance Directive'),
                    ('public_order_directive', 'Public Order Directive'),
                    ('legislation_fast_track', 'Legislation Fast-Track'),
                    ('trade_reversal', 'Trade Reversal Order'),
                    ('merchandise_fine', 'Merchandise Quality Fine'),
                    ('education_directive', 'Education Directive'),
                    ('relations_directive', 'Relations Directive'),
                    ('logistics_order', 'Logistics Order'),
                    ('interior_directive', 'Interior Directive'),
                    ('productivity_directive', 'Productivity Directive'),
                    ('infraction_fine', 'Infraction Fine'),
                    ('general', 'General Directive'),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
    ]
