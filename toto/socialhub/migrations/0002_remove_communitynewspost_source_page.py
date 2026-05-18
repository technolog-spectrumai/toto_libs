from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('socialhub', '0001_initial'),
        ('palimpsest', '0002_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='communitynewspost',
            name='source_page',
        ),
    ]
