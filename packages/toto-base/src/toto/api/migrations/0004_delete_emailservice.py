"""DESTRUCTIVE. Drops the table ``socialhub_emailservice`` and every row in it.

Half two of absorbing ``api.EmailService`` into ``toto.jess`` — see
``socialhub/migrations/0002_remove_community_email_service.py`` for why, and note the
dependency on it below: the referring column must be gone before the table is.

Any stored SMTP configuration is lost. That was accepted deliberately in favour of a
clean schema, and it costs nothing in practice: the send path never worked, so no
deployment has a configuration here worth keeping. There is no data migration into
``jess.EmailProvider``, and no preserved ``db_table`` — ``jess_emailprovider`` is a new
table with a different shape (a separate SMTP login and From address, a timeout, an
explicit backend choice).

The ``gervazy.EncryptedSecret`` rows these pointed at are NOT deleted. The FK ran this
way and was ``SET_NULL``, so the ciphertext is simply orphaned in the strongbox rather
than lost — and blind deletion of gervazy rows is unsafe in general, because other
models hold ``PROTECT`` FKs into the same table. Retire them by hand if it ever matters;
nothing ever stored one.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('socialhub', '0002_remove_community_email_service'),
        ('api', '0003_data_mesh_group'),
    ]

    operations = [
        migrations.DeleteModel(
            name='EmailService',
        ),
    ]
