from django.conf import settings
from django.db import models


class OIDCProviderConfig(models.Model):
    """
    Single source of truth for OIDC consumer configuration.
    Only one record should be active at a time.
    Populated via admin (import connection bundle) or ingress_sso_client (dev).
    """
    label = models.CharField(max_length=100, default="Portal")
    portal_url = models.URLField()
    client_id = models.CharField(max_length=128)

    # The client secret, encrypted at rest in the SSO strongbox. There is no
    # plaintext column: the previous one was readable by anyone with database
    # access, and was silently overridden by an SSO_CLIENT_SECRET environment
    # variable that beat whatever an operator typed in the admin.
    secret = models.ForeignKey(
        "gervazy.EncryptedSecret",
        null=True, blank=True,
        on_delete=models.PROTECT,
        related_name="oidc_provider_configs",
        help_text="Gervazy EncryptedSecret holding the OIDC client secret.",
    )

    scopes = models.CharField(max_length=255, default="openid email profile")
    trusted = models.BooleanField(default=False)
    redirect_uris = models.TextField(blank=True, help_text="One redirect URI per line.")
    active = models.BooleanField(default=True)
    imported_at = models.DateTimeField(auto_now_add=True)

    # --- learned from the provider at pairing time, never typed ---
    # These used to be built by string concatenation on every request
    # (f"{portal}/sso/token/"), which silently breaks against a provider mounted
    # at any other prefix. The provider states them once, at pairing, and they
    # are read back verbatim.
    issuer = models.CharField(max_length=255, blank=True)
    authorization_endpoint = models.CharField(max_length=500, blank=True)
    token_endpoint = models.CharField(max_length=500, blank=True)
    userinfo_endpoint = models.CharField(max_length=500, blank=True)
    jwks_uri = models.CharField(max_length=500, blank=True)

    # The exact callback this host sent at pairing. The provider compares it as a
    # literal string at both /authorize and /token, so keeping the value that was
    # actually registered removes the guesswork from diagnosing a mismatch.
    callback_uri = models.CharField(max_length=500, blank=True)
    # The provider's relying-party UUID. Stable across re-pairings, so it is how
    # this row recognises "the same registration" rather than a new one.
    remote_client_uuid = models.CharField(max_length=64, blank=True)

    paired_at = models.DateTimeField(null=True, blank=True)
    last_pair_error = models.TextField(blank=True)

    class Meta:
        ordering = ["-imported_at"]
        verbose_name = "OIDC Provider Config"
        verbose_name_plural = "OIDC Provider Configs"

    def __str__(self):
        return f"{self.label} ({self.client_id})"

    def redirect_uris_list(self):
        return [u.strip() for u in self.redirect_uris.splitlines() if u.strip()]


class FederatedIdentity(models.Model):
    """"This local account belongs to that provider subject" — stated, not guessed.

    Without this table the consumer had no way to tell a local-only account from
    a federated one, so ``_find_existing_user_for_claims`` matched incoming claims
    on ``username`` and then on ``email__iexact``. On a pure consumer that is
    correct and useful: every account came from the provider anyway, and matching
    reconciles one that was seeded ahead of time.

    On a host that also has **its own** users it is an account takeover. A local
    user whose email happens to equal a provider account's gets adopted by that
    identity, keeps their password (so both routes now reach one account), and has
    ``email``, names and — when the provider grants the ``roles`` scope —
    ``is_staff`` / ``is_superuser`` / ``is_active`` overwritten from the claims.
    A provider admin therefore became a local admin here by coincidence of email.

    With the table, matching is only ever on ``(provider, sub)``: an identity this
    host has been *told* about. A local-only account is never matched, so it can
    neither be adopted nor promoted. Attaching one to a federated identity becomes
    a deliberate act — see ``link_federated_identity`` — which is also what keeps a
    host whose users authenticate by password (faros' desktop binaries) safe from
    having ``set_unusable_password()`` applied to them by accident.

    A user may hold several: one per provider, and the unique constraint is on
    ``(provider, sub)`` rather than on ``user``.
    """

    # PROTECT, not CASCADE: deleting a provider config used to silently destroy
    # every account link with it, which is the one table that decides whether a
    # returning federated user is recognised at all. Retiring a connection is a
    # deliberate act with a confirmation, not a side effect of a delete button.
    provider = models.ForeignKey(
        OIDCProviderConfig, on_delete=models.PROTECT, related_name="identities",
    )
    # Opaque and provider-scoped. Not an email and not a username: both of those
    # are mutable at the provider, and matching on a mutable field is the bug this
    # model exists to close.
    sub = models.CharField(max_length=255)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="federated_identities",
    )
    linked_at = models.DateTimeField(auto_now_add=True)
    # True when the account was created for this identity, False when an existing
    # local account was deliberately linked. Kept because the two are different
    # things to a later audit: one is "the provider owns this account", the other
    # is "a local user chose to also sign in that way".
    provisioned = models.BooleanField(default=False)
    last_login_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["provider", "sub"], name="uniq_provider_sub"),
        ]
        verbose_name = "Federated Identity"
        verbose_name_plural = "Federated Identities"

    def __str__(self):
        return f"{self.user} @ {self.provider.label}"
