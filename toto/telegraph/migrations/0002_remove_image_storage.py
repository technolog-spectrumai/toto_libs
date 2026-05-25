from django.db import migrations


def _drop_image_table(apps, schema_editor):
    from django.db import connection
    if "telegraph_telegraphimage" in connection.introspection.table_names():
        with connection.cursor() as cursor:
            cursor.execute("DROP TABLE telegraph_telegraphimage")


class Migration(migrations.Migration):

    dependencies = [
        ('telegraph', '0001_initial'),
        ('people', '0002_initial'),
    ]

    operations = [
        migrations.RunPython(_drop_image_table, migrations.RunPython.noop),
    ]
