from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0001_initial'),
        ('magistrate', '0003_remove_mobilization_from_magistrate'),
    ]

    operations = [
        migrations.AddField(
            model_name='magistraterole',
            name='can_freeze_assets',
            field=models.BooleanField(
                default=False,
                help_text='Holder may issue a temporary freeze on an asset, preventing all transfers of that token until the freeze is lifted.',
            ),
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
                    ('asset_freeze', 'Asset Freeze Order'),
                    ('asset_freeze_lift', 'Asset Freeze Lift'),
                    ('general', 'General Directive'),
                ],
                db_index=True,
                max_length=30,
            ),
        ),
        migrations.CreateModel(
            name='AssetFreeze',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reason', models.TextField(help_text='Grounds for the freeze order.')),
                ('status', models.CharField(
                    choices=[('active', 'Active'), ('lifted', 'Lifted')],
                    db_index=True,
                    default='active',
                    max_length=16,
                )),
                ('lifted_at', models.DateTimeField(blank=True, null=True)),
                ('lift_reason', models.TextField(blank=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('asset', models.ForeignKey(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='magistrate_freezes',
                    to='assets.asset',
                )),
                ('decision', models.OneToOneField(
                    on_delete=django.db.models.deletion.CASCADE,
                    related_name='asset_freeze_detail',
                    to='magistrate.magistratedecision',
                )),
                ('lift_decision', models.OneToOneField(
                    blank=True,
                    null=True,
                    on_delete=django.db.models.deletion.SET_NULL,
                    related_name='asset_freeze_lift_detail',
                    to='magistrate.magistratedecision',
                )),
            ],
            options={
                'verbose_name': 'Asset freeze',
                'verbose_name_plural': 'Asset freezes',
                'ordering': ['-created_at'],
            },
        ),
    ]
