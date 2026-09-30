# The e-mail change link is bound to the address the account had when it was
# asked for (review, 2026-10-01). A row from before has "" and its link is
# refused; the member asks again.

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('socialhub', '0015_pending_email_change'),
    ]

    operations = [
        migrations.AddField(
            model_name='pendingemailchange',
            name='old_email',
            field=models.EmailField(blank=True, default='', max_length=254),
        ),
    ]
