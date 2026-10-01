"""Remote storage credentials, sealed under a human-typed storage PIN.

## The rule

An S3 secret access key and a peer api key may live in this database only
encrypted under a PIN that **is never stored anywhere**. A database dump alone
recovers nothing; a dump plus the deploy config still recovers nothing. The only
thing that opens a credential is a person typing their PIN, per action.

That is a deliberate step up from ``BucketPeer.api_key_encrypted``, which is
Fernet-sealed under ``FIELD_ENCRYPTION_KEY`` — a key that travels in the deploy
config beside the dump.

## The key hierarchy

::

    typed storage PIN                       never stored, never compared
         │  Argon2id(salt=UserStrongbox.salt, m=64MiB, t=3, p=4)
         ▼
       UKEK ──AES-256-GCM──▶ VMK ──AES-256-GCM──▶ DEK      (all gervazy, unmodified)
         │
         ▼
       BCK   32 random bytes, ONE per credential          CredentialWrap.wrapped_bck
         │   wrapped once PER OPERATOR under their own DEK
         ▼
    credential JSON                                       RemoteCredential.ciphertext
        s3   {"aws_access_key_id", "aws_secret_access_key", "session_token"}
        peer {"api_key"}

**One bucket key, wrapped per operator** — not per-operator copies of the
credential. Rotating the S3 secret then rewrites one ciphertext and touches zero
wraps, which matters because no one can assemble every operator's PIN at once.
Enrolling is one INSERT, revoking is one DELETE. The cost, stated plainly: any
single enrolled operator's PIN yields the credential. That is inherent to
multi-recipient sharing.

## AAD binds every layer to its context

Nothing here is a bare ciphertext. Each layer names what it belongs to, so a row
copied somewhere else fails its tag check even with the right key in hand:

* the wrap binds ``(credential_uid, operator_pk)`` — a wrap cannot be moved to
  another operator or another credential;
* the credential binds ``(credential_uid, kind, version)`` — the previous
  version's bytes cannot be swapped back after a rotation, and an s3 blob cannot
  be replayed as a peer blob;
* a run capability binds ``(capability_uid, run_kind, run_id, credential_uid,
  version)`` — so it dies automatically when the credential rotates.

## Why ``credential_uid`` and not ``uid``

Vault models avoid a bare ``uid`` (see ``peering.py``). The backup engine that
motivated the rule is gone from the suite, but the convention is kept: every
peering-era identity column here is ``*_uid``.
"""

from __future__ import annotations

import hashlib
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone

from toto.core.django_compat import check_constraint


CREDENTIAL_KINDS = (
    ("s3", "S3 access key"),
    ("peer", "Peer api key"),
)

#: What a run may do with a redeemed capability. Named operations rather than a
#: free string so a capability minted for a listing walk cannot drive a copy.
RUN_KINDS = (
    ("refresh", "Mirror refresh"),
    ("transfer", "Transfer"),
    ("probe", "Connection test"),
)


def fingerprint_for(secret: str) -> str:
    """A value that identifies a key without shortening the search for it.

    64 bits of SHA-256, rendered with its algorithm so nobody mistakes it for a
    prefix of the secret. Enough to answer "did the rotation change the key?"
    and "is host A's key the same as host B's?"; useless as an attack shortcut.
    """
    return "sha256:" + hashlib.sha256((secret or "").encode()).hexdigest()[:16]


class RemoteCredential(models.Model):
    """One credential, sealed once under a Bucket Credential Key."""

    credential_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    kind = models.CharField(max_length=8, choices=CREDENTIAL_KINDS)
    bucket = models.OneToOneField(
        "vault.Bucket", null=True, blank=True, on_delete=models.CASCADE,
        related_name="remote_credential")
    peer = models.OneToOneField(
        "vault.BucketPeer", null=True, blank=True, on_delete=models.CASCADE,
        related_name="remote_credential")

    ciphertext = models.BinaryField()
    nonce = models.BinaryField()
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    version = models.PositiveIntegerField(default=1)

    #: Identifies the key to a human. For s3 this is the access key id, which is
    #: not a secret — it travels in every Authorization header. For a peer it is
    #: the last 8 characters, matching the convention on the exporting host so
    #: two operators can compare notes.
    hint = models.CharField(max_length=64, blank=True)
    fingerprint = models.CharField(max_length=23, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vault_credentials_created")
    created_at = models.DateTimeField(auto_now_add=True)
    rotated_at = models.DateTimeField(null=True, blank=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "remote credential"
        constraints = [
            check_constraint(
                condition=(models.Q(bucket__isnull=False, peer__isnull=True)
                           | models.Q(bucket__isnull=True, peer__isnull=False)),
                name="vault_credential_targets_exactly_one"),
        ]

    def __str__(self):
        target = self.bucket or self.peer
        return f"{self.get_kind_display()} for {target}"

    def clean(self):
        if len(bytes(self.nonce or b"")) != 12:
            raise ValidationError("AES-GCM nonce must be exactly 12 bytes.")
        if not self.ciphertext:
            raise ValidationError("A credential cannot be empty.")

    def build_aad(self) -> bytes:
        return (f"toto:vault:cred:v1:{self.credential_uid}:"
                f"{self.kind}:{self.version}").encode()

    @property
    def operator_count(self) -> int:
        return self.wraps.filter(is_active=True).count()

    @property
    def is_shared(self) -> bool:
        """Two or more operators can open it — the only safe steady state.

        A credential only one person can open is one forgotten PIN away from
        being unrecoverable, and gervazy has no escrow by design.
        """
        return self.operator_count >= 2

    def note_use(self):
        type(self).objects.filter(pk=self.pk).update(
            last_used_at=timezone.now(), use_count=models.F("use_count") + 1)


class CredentialWrap(models.Model):
    """This operator may open that credential: the BCK, under their storage DEK.

    One row per (credential, operator). Deleting it revokes that operator and
    nothing else — no other wrap moves, no ciphertext is rewritten.
    """

    credential = models.ForeignKey(
        RemoteCredential, on_delete=models.CASCADE, related_name="wraps")
    operator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="vault_credential_wraps")
    #: PROTECT: the operator's DEK is pinned so a gervazy-side retirement cannot
    #: silently orphan a wrap. Every read re-fetches this graph, because a PIN
    #: change rewrites VaultMasterKey.encrypted_vmk in place.
    wrapped_key = models.ForeignKey(
        "gervazy.WrappedDataKey", on_delete=models.PROTECT,
        related_name="vault_credential_wraps")
    wrapped_bck = models.BinaryField()
    nonce = models.BinaryField()
    algorithm = models.CharField(max_length=32, default="AES-256-GCM")
    is_active = models.BooleanField(default=True)

    enrolled_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vault_enrollments_granted")
    enrolled_at = models.DateTimeField(auto_now_add=True)
    last_used_at = models.DateTimeField(null=True, blank=True)
    use_count = models.PositiveIntegerField(default=0)

    class Meta:
        verbose_name = "credential wrap"
        constraints = [
            models.UniqueConstraint(fields=["credential", "operator"],
                                    name="vault_one_wrap_per_operator"),
        ]

    def __str__(self):
        return f"{self.operator} may open {self.credential_id}"

    def build_aad(self) -> bytes:
        return (f"toto:vault:bck:v1:{self.credential.credential_uid}:"
                f"{self.operator_id}").encode()

    def note_use(self):
        type(self).objects.filter(pk=self.pk).update(
            last_used_at=timezone.now(), use_count=models.F("use_count") + 1)


def _default_enrollment_expiry():
    """48 hours.

    Shorter than a BucketGrant's 7 days on purpose: a pairing code carries a
    grant the far host still enforces, while an enrollment code carries a live
    key with nothing behind it.
    """
    return timezone.now() + timezone.timedelta(hours=48)


class CredentialEnrollment(models.Model):
    """A show-once ticket carrying one BCK to one invitee. Single-use, expiring.

    The bind this solves: writing Bob's wrap needs the BCK (which only Alice's
    PIN opens) AND Bob's DEK (which only Bob's PIN opens), and nobody holds
    both. Gervazy has no per-user public key to seal to, so the BCK travels
    through a code Alice hands over, exactly as a pairing code does.

    For the ticket's lifetime the code IS the credential in transit. Mitigated
    by: shown once, stored only as a hash, single-use, 48h, and bound by AAD to
    one invitee and one credential. The window is real and is documented rather
    than glossed.
    """

    enrollment_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    credential = models.ForeignKey(
        RemoteCredential, on_delete=models.CASCADE, related_name="enrollments")
    invitee = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE,
        related_name="vault_credential_enrollments")

    sealed_bck = models.BinaryField(blank=True, default=b"")
    nonce = models.BinaryField(blank=True, default=b"")
    code_hash = models.CharField(max_length=255)
    code_hint = models.CharField(max_length=16, blank=True)

    expires_at = models.DateTimeField(default=_default_enrollment_expiry)
    offered_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vault_enrollments_offered")
    created_at = models.DateTimeField(auto_now_add=True)
    redeemed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "credential enrollment"
        ordering = ("-created_at",)

    def __str__(self):
        return f"enrollment for {self.invitee} ({self.code_hint})"

    def build_aad(self) -> bytes:
        return (f"toto:vault:enroll:v1:{self.enrollment_uid}:"
                f"{self.credential.credential_uid}:{self.invitee_id}").encode()

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at

    @property
    def can_be_used(self) -> bool:
        return self.redeemed_at is None and not self.is_expired and bool(self.sealed_bck)


class RunCapability(models.Model):
    """A credential a worker may use ONCE, for ONE run, until a deadline.

    An operator's PIN opened the credential in a request; this row carries it
    across the handoff to a process with no human in it.

    It cannot ride the payload and it cannot ride the run row:

    * ``WorkflowRun.input_data`` is readable by **any authenticated user**
      (``WorkflowRunDetailUIView`` is LoginRequiredMixin plus a bare
      ``get_object_or_404``, and workflows' own permissions module says viewing
      stays open to every signed-in user) and is staff-editable in the admin;
    * Celery here is unauthenticated Redis with RDB persistence, sharing the
      result backend, with returns logged at INFO into Loki.

    So the payload carries only this row's uid, and the row carries ciphertext
    sealed under ``VAULT_RUN_KEY`` — an env secret that deliberately does NOT
    fall back to ``FIELD_ENCRYPTION_KEY``, the same argument the monetary issuer
    key makes: a restored dump must be inert rather than able to act.
    """

    capability_uid = models.UUIDField(default=uuid.uuid4, unique=True, editable=False)
    credential = models.ForeignKey(
        RemoteCredential, on_delete=models.CASCADE, related_name="run_capabilities")
    run_kind = models.CharField(max_length=16, choices=RUN_KINDS)
    #: Not an FK: two different run models point here, and toto.workflows is
    #: optional on some hosts.
    run_id = models.PositiveIntegerField(null=True, blank=True)

    sealed = models.BinaryField(blank=True, default=b"")
    salt = models.BinaryField()
    argon2_memory_cost = models.PositiveIntegerField(default=65536)
    argon2_iterations = models.PositiveIntegerField(default=3)
    argon2_lanes = models.PositiveSmallIntegerField(default=4)

    issued_to = models.ForeignKey(
        settings.AUTH_USER_MODEL, null=True, blank=True, on_delete=models.SET_NULL,
        related_name="vault_run_capabilities")
    issued_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    consumed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "run capability"
        ordering = ("-issued_at",)
        indexes = [models.Index(fields=["expires_at"])]

    def __str__(self):
        return f"{self.run_kind} capability {self.capability_uid}"

    def build_aad(self) -> bytes:
        """Every component is re-derived from the row when it is opened.

        Including ``credential.version`` is what makes a capability minted
        before a rotation automatically dead after it, with no sweep required.
        """
        return (f"toto:vault:run-capability:v1:{self.capability_uid}:"
                f"{self.run_kind}:{self.run_id}:"
                f"{self.credential.credential_uid}:{self.credential.version}").encode()

    @property
    def is_spent(self) -> bool:
        return self.consumed_at is not None or not self.sealed

    @property
    def is_expired(self) -> bool:
        return timezone.now() >= self.expires_at
