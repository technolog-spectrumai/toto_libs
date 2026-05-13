from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ("socialhub", "0001_initial"),
    ]

    operations = [
        migrations.SeparateDatabaseAndState(
            state_operations=[
                migrations.DeleteModel("Experience"),
            ],
            database_operations=[
                migrations.RunSQL(
                    sql="DROP TABLE IF EXISTS socialhub_experience",
                    reverse_sql="",
                ),
            ],
        ),
    ]
