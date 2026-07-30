"""DESTRUCTIVE. Drops the ``email_service_id`` column from ``socialhub_community``.

Half one of absorbing ``api.EmailService`` into ``toto.jess``; half two is
``api/migrations/0004_delete_emailservice.py``, which must run after this.

What is being dropped is the per-community mail relay. It has never carried a message:
``EmailService.send_email`` declared ``smtp_password`` keyword-only and both call sites
in ``socialhub/views/application.py`` omitted it, so every send raised ``TypeError``
into a bare ``except Exception`` and was logged as a failure. Nothing seeded the
``default-email-service`` row the fallback looked for, and the admin had no way to write
the password at all.

Jess replaces it with one platform-wide active provider. The useful half of "mail from
this community looks like it came from this community" survives as a per-message
``reply_to`` — see ``_send_endorsement_mail`` in that same views module. Per-community
*transport* does not survive, deliberately: it meant a second place to keep an SMTP
credential.
"""
from django.db import migrations


class Migration(migrations.Migration):

    dependencies = [
        ('socialhub', '0001_initial'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='community',
            name='email_service',
        ),
    ]
