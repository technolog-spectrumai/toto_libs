"""No invitations (2026-09-28): a password will do.

Invite-only rooms become password rooms with NO password. Nothing opens up:
`creation.join` refuses a password room without a verifier, so the members
they had stay and nobody new joins until the room's creator or staff set a
password on the Members tab. An encrypted room's key stays wrapped under the
platform; `change_password` adds the password wrapping when one is set.
"""

from django.db import migrations, models


def invite_to_password(apps, schema_editor):
    ForumChannel = apps.get_model("forum", "ForumChannel")
    ForumChannel.objects.filter(access="invite").update(access="password")


class Migration(migrations.Migration):

    dependencies = [
        ("forum", "0006_room_kinds_and_billing"),
    ]

    operations = [
        migrations.RunPython(invite_to_password, migrations.RunPython.noop),
        migrations.AlterField(
            model_name="forumchannel",
            name="access",
            field=models.CharField(
                choices=[("open", "Open"), ("password", "Password")],
                default="open", max_length=8),
        ),
    ]
