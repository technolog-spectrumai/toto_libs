"""Record federated identities explicitly, and adopt the ones already implied.

Matching incoming claims used to fall back to username and then email, which on a
host with local accounts of its own is an account-takeover path — see
``sso_client.models.FederatedIdentity``. From here on only a recorded
``(provider, sub)`` matches.

That is a breaking change for accounts this host provisioned *before* the table
existed: with nothing recorded, and auto-provisioning off, their next sign-in
would be refused. ``backfill_provisioned_identities`` adopts them. It can do so
safely because a provisioned account has a derivable name — ``oidc_<sub>``, set by
``_provisioned_username`` — so the subject is recoverable from the username with
no guessing at all.

Accounts that were matched by *email* rather than provisioned are deliberately
NOT backfilled: there is no way to tell one that legitimately belonged to the
provider from a local account that merely collided, and guessing would re-create
the very takeover this migration closes. Those need a deliberate link (the
``/sso/link/`` flow), which is the right amount of friction for the ambiguity.
"""
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


def backfill_provisioned_identities(apps, schema_editor):
    OIDCProviderConfig = apps.get_model("sso_client", "OIDCProviderConfig")
    FederatedIdentity = apps.get_model("sso_client", "FederatedIdentity")
    User = apps.get_model(settings.AUTH_USER_MODEL)

    provider = OIDCProviderConfig.objects.filter(active=True).order_by("-imported_at").first()
    if provider is None:
        return  # nothing was ever federated here

    rows = [
        FederatedIdentity(
            provider=provider,
            sub=user.username[len("oidc_"):],
            user=user,
            provisioned=True,
        )
        for user in User.objects.filter(username__startswith="oidc_")
        if len(user.username) > len("oidc_")
    ]
    FederatedIdentity.objects.bulk_create(rows, ignore_conflicts=True)


def drop_backfilled_identities(apps, schema_editor):
    # The table goes away with the CreateModel reversal; nothing to undo here.
    pass


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('sso_client', '0002_oidcproviderconfig_app_name_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='FederatedIdentity',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('sub', models.CharField(max_length=255)),
                ('linked_at', models.DateTimeField(auto_now_add=True)),
                ('provisioned', models.BooleanField(default=False)),
                ('last_login_at', models.DateTimeField(blank=True, null=True)),
                ('provider', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='identities', to='sso_client.oidcproviderconfig')),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='federated_identities', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Federated Identity',
                'verbose_name_plural': 'Federated Identities',
            },
        ),
        migrations.AddConstraint(
            model_name='federatedidentity',
            constraint=models.UniqueConstraint(fields=('provider', 'sub'), name='uniq_provider_sub'),
        ),
        # After the constraint, so a duplicated subject in existing data surfaces
        # here rather than being written and rejected later.
        migrations.RunPython(
            backfill_provisioned_identities, drop_backfilled_identities,
        ),
    ]
