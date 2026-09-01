# help_text only — the stale 'the link hides itself' prose became wrong when
# the two-flow reset shipped (the page serves patron recovery instead). No
# schema change; Django tracks help_text in field state, so the autodetector
# must be answered or the gate's makemigrations --check fails.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('jess', '0005_remove_jessquotapolicy_jess_jessquotapolicy_one_default_and_more'),
    ]

    operations = [
        migrations.AlterField(
            model_name='emailprovider',
            name='backend',
            field=models.CharField(choices=[('smtp', 'SMTP'), ('console', 'Console (printed, not sent)'), ('dummy', 'Dummy (discarded)'), ('locmem', 'In-memory (tests)'), ('filebased', 'Files on disk')], default='console', help_text='How to send. Console and Dummy do not deliver, and Jess reports that honestly — the reset page then serves patron-approved recovery instead of promising an email.', max_length=20),
        ),
    ]
