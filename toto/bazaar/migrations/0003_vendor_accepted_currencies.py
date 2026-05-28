from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('assets', '0002_initial'),
        ('bazaar', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='vendor',
            name='accepted_currencies',
            field=models.ManyToManyField(
                blank=True,
                help_text='Assets this vendor accepts as payment.',
                limit_choices_to={'active': True},
                related_name='bazaar_vendors',
                to='assets.asset',
            ),
        ),
    ]
