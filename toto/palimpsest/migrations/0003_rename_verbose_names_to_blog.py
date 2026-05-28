from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('palimpsest', '0002_initial'),
    ]

    operations = [
        migrations.AlterModelOptions(
            name='tag',
            options={
                'verbose_name': 'Blog tag',
                'verbose_name_plural': 'Blog tags',
            },
        ),
        migrations.AlterModelOptions(
            name='page',
            options={
                'ordering': ['-created_at'],
                'verbose_name': 'Blog post',
                'verbose_name_plural': 'Blog posts',
            },
        ),
    ]
