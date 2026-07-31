import secrets
import uuid
from datetime import timedelta

from django.contrib.auth import get_user_model
from django.contrib.auth.hashers import check_password, make_password
from django.db import models
from django.utils import timezone

User = get_user_model()

# How long a replaced client secret keeps working after a rotation. Long enough
# that re-pairing a live federation is never an outage, short enough that a
# leaked old secret is not useful for long. Closed early the moment the far side
# proves it holds the new one — see SSOClient.secret_proven_at.
SECRET_GRACE_WINDOW = timedelta(hours=24)

# How long a federation pairing code is redeemable for, unless the admin says
# otherwise. Five minutes is ample for "read the screen, paste it on the other
# box", and short enough that a code captured in a proxy log or an error report is
# already dead by the time anyone reads it. Bounds are enforced in the admin form.
DEFAULT_INVITE_TTL = timedelta(minutes=5)
# The admin form offers this many minutes by default. Kept in step with
# DEFAULT_INVITE_TTL so the number an admin sees pre-filled is the five minutes
# every operator-facing text promises — the form always passes an explicit ttl to
# mint(), so DEFAULT_INVITE_TTL itself never reaches that path and the form is
# where the default has to live.
DEFAULT_INVITE_TTL_MINUTES = int(DEFAULT_INVITE_TTL.total_seconds() // 60)
MIN_INVITE_TTL_MINUTES = 1
MAX_INVITE_TTL_MINUTES = 120

# client_ids federation pairing must never take. Grafana and Gitea are sidecars
# registered from host configuration; if an invite labelled "Gitea" grabbed the id
# first — easy on a fresh database, after RESET=1, or while the service is
# switched off — then create_relying_party would refuse the real one forever after.
RESERVED_CLIENT_IDS = frozenset({"gitea", "grafana"})


class SSOClient(models.Model):
    """
    A server/application that is allowed to use this Django project as SSO.

    This is the OpenID Connect relying party / client registration record.
    """

    CONFIDENTIAL = "confidential"
    PUBLIC = "public"

    CLIENT_TYPES = [
        (CONFIDENTIAL, "Confidential"),
        (PUBLIC, "Public"),
    ]

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    client_id = models.CharField(max_length=128, unique=True)
    client_secret_hash = models.CharField(max_length=255, blank=True)

    name = models.CharField(max_length=255)
    client_type = models.CharField(max_length=20, choices=CLIENT_TYPES, default=CONFIDENTIAL)

    redirect_uris = models.TextField(help_text="One redirect URI per line. Must match exactly.")
    allowed_scopes = models.CharField(max_length=255, default="openid email profile")

    active = models.BooleanField(default=True)
    trusted = models.BooleanField(
        default=False,
        help_text="If true, skip the consent page. Use only for first-party/internal apps.",
    )

    # --- secret rotation with a grace window ---
    # Rotating used to be a cliff: the old secret died the instant a new one was
    # minted, so re-issuing credentials for a live federation logged everyone out
    # until the far side was updated by hand. The previous hash stays valid for a
    # bounded window instead, which is what makes re-pairing a running platform a
    # non-event.
    previous_secret_hash = models.CharField(max_length=255, blank=True, default="")
    previous_secret_expires_at = models.DateTimeField(null=True, blank=True)
    secret_rotated_at = models.DateTimeField(null=True, blank=True)
    # Stamped at /token the first time the CURRENT secret verifies. Two things
    # depend on it: the grace window closes early once the far side has proven it
    # has the new secret, and a second fumbled pairing cannot overwrite the last
    # known-good previous hash with a secret nobody ever used.
    secret_proven_at = models.DateTimeField(null=True, blank=True)
    # Stamped at /token whenever the PREVIOUS secret is what verified. This is the
    # only way to answer "has the other side actually picked up the new secret
    # yet?" — without it an operator mid-rotation is guessing, and the admin would
    # show a rotation as complete while the peer was still on the old value.
    previous_secret_used_at = models.DateTimeField(null=True, blank=True)

    # --- federation pairing ---
    paired_at = models.DateTimeField(null=True, blank=True)
    # False for Grafana and Gitea: they are sidecar containers whose secret is
    # shared with a config deploy.py generates, with no operator and no admin on
    # the other end to hand a pairing code to. They keep their provisioning path;
    # this flag is what lets the admin show which rows are which, and what keeps
    # the pairing machinery from touching them.
    pairing_managed = models.BooleanField(
        default=False,
        help_text="Registered by federation pairing rather than by host configuration.",
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name} ({self.client_id})"

    def rotate_client_secret(self, raw_secret=None, *, grace=None):
        """Mint (or re-apply) a client secret. The only writer of the hash.

        Returns the raw value once; it is never recoverable afterwards.

        Three behaviours worth knowing, each of which exists because of a real
        failure:

        * **Re-applying the same secret is a no-op.** ``ingress_sso_master``
          re-provisions Grafana and Gitea on every container start with the same
          deployment secret; without this check each restart would push a
          perfectly good secret into the previous-secret slot and open a grace
          window for no reason.
        * **The previous hash is only replaced once the current one has been
          proven** (``secret_proven_at``). Two failed pairing attempts inside one
          window therefore leave the last *working* secret in the previous slot,
          not the first attempt's dead one.
        * The window is only opened when there is something to put in it, so a
          first-ever secret does not create a grace period for a secret that
          never existed.
        """
        if grace is None:
            grace = SECRET_GRACE_WINDOW

        if raw_secret and self.client_secret_hash and check_password(
            raw_secret, self.client_secret_hash
        ):
            return raw_secret

        if self.secret_proven_at and self.client_secret_hash:
            self.previous_secret_hash = self.client_secret_hash
            self.previous_secret_expires_at = timezone.now() + grace

        raw_secret = raw_secret or secrets.token_urlsafe(48)
        self.client_secret_hash = make_password(raw_secret)
        self.secret_rotated_at = timezone.now()
        self.secret_proven_at = None
        return raw_secret

    def set_client_secret(self, raw_secret=None):
        """Deprecated alias for :meth:`rotate_client_secret`.

        Kept because ``sso_master/tests/test_claims_and_discovery.py`` calls it,
        and because the name reads correctly at the one place a secret is being
        set rather than rotated.
        """
        return self.rotate_client_secret(raw_secret)

    def check_client_secret(self, raw_secret):
        """``"current"``, ``"previous"``, or ``None``. The one secret comparator.

        A public client has no secret at all — PKCE is its proof of possession,
        enforced at ``/authorize`` (which refuses a public client with no
        ``code_challenge``) and again at ``/token``. Returning "current" here is
        therefore correct OIDC rather than a hole.
        """
        if self.client_type == self.PUBLIC:
            return "current"
        if not raw_secret:
            return None
        if self.client_secret_hash and check_password(raw_secret, self.client_secret_hash):
            return "current"
        if (
            self.previous_secret_hash
            and self.previous_secret_expires_at
            and timezone.now() < self.previous_secret_expires_at
            and check_password(raw_secret, self.previous_secret_hash)
        ):
            return "previous"
        return None

    def verify_client_secret(self, raw_secret):
        return self.check_client_secret(raw_secret) is not None

    def redirect_uri_list(self):
        return [uri.strip() for uri in self.redirect_uris.splitlines() if uri.strip()]

    def is_redirect_uri_allowed(self, redirect_uri):
        return redirect_uri in self.redirect_uri_list()

    def scope_list(self):
        return [scope.strip() for scope in self.allowed_scopes.split() if scope.strip()]


class SSOSubject(models.Model):
    """
    Stable public subject identifier for a local user.

    Do not expose User.pk as OIDC `sub`.
    """

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="sso_subject")
    subject = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.user}: {self.subject}"


class SSOAuthorizationCode(models.Model):
    """
    Short-lived one-time code issued by /sso/authorize/ and consumed by /sso/token/.
    """

    code = models.CharField(max_length=128, unique=True)
    client = models.ForeignKey(SSOClient, on_delete=models.CASCADE, related_name="authorization_codes")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sso_authorization_codes")

    redirect_uri = models.URLField(max_length=1000)
    scope = models.CharField(max_length=255)
    nonce = models.CharField(max_length=255, blank=True, null=True)
    state = models.CharField(max_length=255, blank=True, null=True)

    code_challenge = models.CharField(max_length=255, blank=True, null=True)
    code_challenge_method = models.CharField(max_length=20, blank=True, null=True)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.code:
            self.code = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(minutes=5)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_used(self):
        return self.used_at is not None

    def mark_used(self):
        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        return f"Code for {self.user} / {self.client}"


class SSOSigningKey(models.Model):
    """
    RSA signing key pair for OIDC ID tokens.

    The public key is stored in plaintext for fast JWKS responses.
    The private key is stored encrypted in Gervazy (EncryptedPrivateKey).
    Decryption requires the SSO_VAULT_PASSWORD setting.
    """

    key_id = models.CharField(max_length=100, unique=True)
    algorithm = models.CharField(max_length=16, default="RS256")
    public_key_pem = models.TextField()
    encrypted_key = models.OneToOneField(
        "gervazy.EncryptedPrivateKey",
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="sso_signing_key",
        help_text="Gervazy EncryptedPrivateKey holding the RSA private key.",
    )
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def __str__(self):
        return f"{self.key_id} ({'active' if self.is_active else 'inactive'})"


class SSOAccessToken(models.Model):
    """
    Opaque bearer token used by /sso/userinfo/.
    """

    token = models.CharField(max_length=255, unique=True)
    client = models.ForeignKey(SSOClient, on_delete=models.CASCADE, related_name="access_tokens")
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="sso_access_tokens")
    scope = models.CharField(max_length=255)

    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    revoked_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]

    def save(self, *args, **kwargs):
        if not self.token:
            self.token = secrets.token_urlsafe(48)
        if not self.expires_at:
            self.expires_at = timezone.now() + timedelta(hours=1)
        super().save(*args, **kwargs)

    @property
    def is_expired(self):
        return timezone.now() >= self.expires_at

    @property
    def is_revoked(self):
        return self.revoked_at is not None

    def revoke(self):
        self.revoked_at = timezone.now()
        self.save(update_fields=["revoked_at"])

    def __str__(self):
        return f"Access token for {self.user} / {self.client}"


class SSORelyingParty(SSOClient):
    """
    First-class relying-party name for the OIDC client registration table.

    This proxy avoids duplicating client credentials while exposing the domain
    concept used by OIDC relying-party onboarding and provisioning.
    """

    class Meta:
        proxy = True
        ordering = ["name"]
        verbose_name = "SSO relying party"
        verbose_name_plural = "SSO relying parties"


class SSOFederationInvite(models.Model):
    """A one-shot code that lets another platform register itself here.

    The pairing story in one object: an admin describes who they are federating
    with and what that platform is allowed to do, and gets back a code to hand
    over. The far side's *server* redeems it; no human ever handles a client
    secret.

    **The secret is not stored.** Only ``secret_sha256``, so a database leak
    yields nothing redeemable. ``ticket_prefix`` exists purely so the admin can
    tell two live invites apart without revealing either.

    **What stops a stolen code.** This design deliberately has no human
    confirmation step — the operator who mints the code is trusted to be handing
    it to the right platform, which is the stated trust model. What remains is:
    single use (under a row lock), a short expiry, and ``expected_host``. That
    last one is the important one. Without it, whoever holds the code can
    register *their* callback URL as a trusted relying party here and start
    receiving tokens for this platform's users; with it, the code is inert unless
    they also control the hostname the admin already wrote down. The admin knows
    the host; only the far side knows its exact callback path, so the host is
    pinned here and the path is supplied at redemption.

    ``relying_party`` is a CASCADE from invite to party, never the reverse:
    deleting an invite must never be able to take a live relying party and its
    tokens with it.
    """

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    relying_party = models.ForeignKey(
        SSOClient, on_delete=models.CASCADE, related_name="federation_invites",
    )

    # Never the code itself. See the class docstring.
    secret_sha256 = models.CharField(max_length=64, unique=True, db_index=True)
    ticket_prefix = models.CharField(max_length=12, blank=True)

    # HMAC(FEDERATION_KEY, handle) — binds this row to the deployment that minted
    # it. `.env` and the database have different lifetimes: RESET=1 wipes the
    # database while the key survives, and a restored production backup arrives on
    # a staging box carrying production's pending invites. Blank when the host has
    # no FEDERATION_KEY, in which case the check is skipped rather than failing
    # closed — an unconfigured host should still be able to federate.
    deployment_mac = models.CharField(max_length=64, blank=True)

    # The hostname the far side must call back on. Compared case-insensitively
    # against the host of the callback URI it presents at redemption.
    expected_host = models.CharField(
        max_length=255,
        help_text="The hostname of the platform you are inviting, e.g. studio.example.com",
    )

    # What the far side gets. Fixed here, at mint time, so a redeemer cannot ask
    # for more than the admin decided to give.
    granted_scopes = models.CharField(max_length=255, default="openid email profile")
    granted_trusted = models.BooleanField(default=True)

    expires_at = models.DateTimeField()
    created_at = models.DateTimeField(auto_now_add=True)
    created_by = models.ForeignKey(
        User, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="minted_federation_invites",
    )

    # Filled at redemption, and kept afterwards: this is the audit trail for
    # "who actually used this code, from where, claiming what".
    redeemed_at = models.DateTimeField(null=True, blank=True)
    redeemed_ip = models.GenericIPAddressField(null=True, blank=True)
    redeemed_callback_uri = models.CharField(max_length=1000, blank=True)

    revoked_at = models.DateTimeField(null=True, blank=True)
    # Every attempt, successful or not. A number climbing here without a
    # redemption is somebody guessing.
    attempt_count = models.PositiveIntegerField(default=0)
    last_error = models.CharField(max_length=255, blank=True)

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "SSO federation invite"
        verbose_name_plural = "SSO federation invites"

    def __str__(self):
        return f"Invite for {self.expected_host} ({self.state})"

    @property
    def state(self) -> str:
        if self.revoked_at:
            return "revoked"
        if self.redeemed_at:
            return "redeemed"
        if timezone.now() >= self.expires_at:
            return "expired"
        return "pending"

    @property
    def is_redeemable(self) -> bool:
        return self.state == "pending"

    def seconds_remaining(self) -> int:
        if not self.is_redeemable:
            return 0
        return max(0, int((self.expires_at - timezone.now()).total_seconds()))
