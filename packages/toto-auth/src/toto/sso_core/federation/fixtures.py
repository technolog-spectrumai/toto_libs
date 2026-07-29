"""Everything both sides need before a federated login can happen.

Deliberately the real objects, created the real way: the relying party through
``provisioning.create_relying_party``, the signing key through the same
management command a deployment runs. If a prerequisite is missing in
production this fixture is missing it too, and the tests say so.
"""
from django.contrib.auth import get_user_model
from django.core.management import call_command

from toto.core.models import Platform
from toto.sso_client.models import OIDCProviderConfig
from toto.sso_master.models import SSOSigningKey
from toto.sso_master.provisioning import create_relying_party

User = get_user_model()

DEFAULT_SCOPES = "openid email profile roles"


def federation_fixture(
    *,
    portal_url,
    redirect_uri,
    secret="shared-s3cret",
    client_id="studio",
    scopes=DEFAULT_SCOPES,
    trusted=True,
):
    """Wire a provider and a consumer to each other. Returns the relying party.

    ``trusted=True`` matches how a real toto-to-toto federation is provisioned:
    an untrusted client sends every single login through the consent page.
    """
    platform = _ensure_platform()
    _ensure_signing_key()

    provisioned = create_relying_party(
        name="Studio",
        client_id=client_id,
        redirect_uris=[redirect_uri],
        trusted=trusted,
        scopes=scopes,
        raw_secret=secret,
        force_recreate=True,
    )

    OIDCProviderConfig.objects.update(active=False)
    OIDCProviderConfig.objects.create(
        label="Federation suite",
        portal_url=portal_url,
        client_id=client_id,
        client_secret=secret,
        scopes=scopes,
        app_name="Studio",
        trusted=trusted,
        redirect_uris=redirect_uri,
        active=True,
    )
    return provisioned.relying_party, platform


def _ensure_platform():
    """An active Platform. get_issuer() raises without one, so every token
    exchange would 500 — the same failure a freshly deployed provider hits."""
    platform = Platform.objects.filter(active=True).first()
    if platform:
        return platform
    return Platform.objects.create(
        site_name="Provider",
        author="Federation suite",
        publication_year=2026,
        active=True,
        domain="provider.test",
    )


def _ensure_signing_key():
    """An active RS256 key, private half in a Gervazy strongbox.

    Costs a real Argon2id derivation, so callers should do this once per test
    class in setUpTestData rather than per test.
    """
    if SSOSigningKey.objects.filter(is_active=True).exists():
        return
    owner = User.objects.filter(is_superuser=True).order_by("pk").first()
    if owner is None:
        owner = User.objects.create_superuser("vaultowner", "vault@provider.test", "x")
    call_command(
        "create_sso_signing_key",
        key_id="federation-suite",
        vault_owner=owner.username,
    )
