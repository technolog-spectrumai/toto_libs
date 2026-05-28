from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('ravioli', '0001_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='cypherquery',
            options={
                'verbose_name': 'Knowledge Graph Query',
                'verbose_name_plural': 'Knowledge Graph Queries',
            },
        ),
    ]
