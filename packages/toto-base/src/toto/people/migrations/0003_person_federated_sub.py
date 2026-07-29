from django.db import migrations, models


class Migration(migrations.Migration):
    """Carry the identity provider's OIDC subject on a consumer host's Person.

    A federated host provisions its own Person for each account that signs in
    through the provider, and needs a stable key to recognise the same human on
    the next login. The provider's ``sub`` is that key.

    Deliberately a plain indexed string and not a foreign key: the two hosts
    have separate databases, so there is nothing to point at and no integrity to
    enforce. Blank on the provider itself, where a Person is a local record.
    """

    dependencies = [
        ('people', '0002_initial'),
    ]

    operations = [
        migrations.AddField(
            model_name='person',
            name='federated_sub',
            field=models.CharField(
                blank=True,
                db_index=True,
                help_text=(
                    'OIDC subject of the identity provider this person was '
                    'provisioned from, on a consumer host. Empty on the '
                    'provider itself.'
                ),
                max_length=255,
            ),
        ),
    ]
