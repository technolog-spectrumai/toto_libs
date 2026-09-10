"""API tokens for a client that has no browser session.

The capsule interface is moving to a desktop app (zinnia), which holds no
Django session and no CSRF cookie. So the API authenticates with a bearer
token, and this is the model behind it.

SELECTOR AND VERIFIER, NOT A BARE SECRET
----------------------------------------
A token is ``<selector>.<verifier>``. The selector is stored in the clear and
indexed; the verifier is stored only as a hash.

That shape is not decoration. A hash cannot be looked up by, so a bare secret
means hashing the candidate against every row until one matches — and
``toto.vault.peering`` records what that costs on this project: PBKDF2 at the
configured iteration count is ~100ms of CPU, and the SSO provider once
saturated at ~35 requests/second because the expensive check ran before the
cheap ones. A desktop client polls job status, so this endpoint will see far
more traffic than a peer manifest ever did.

With a selector, every request does one indexed lookup, then the cheap refusals
(inactive, expired), and only then the single hash comparison. `check_password`
is constant-time, so the verifier comparison does not leak.

WHAT A TOKEN IS NOT
-------------------
It is not a second permission system. A token acts as its owner and reaches
exactly what that person reaches — the same ownership rule the desk enforces by
404ing somebody else's capsule rather than 403ing it. Nothing here grants a
capability the owner does not already have, which is why there are no
per-token scopes to get wrong.
"""

from __future__ import annotations

import secrets

from django.conf import settings
from django.db import models
from django.utils import timezone

#: Bytes of entropy either half carries. 32 urlsafe bytes is ~256 bits; the
#: selector needs only enough to be unique, the verifier needs to be
#: unguessable, and using one size for both means nobody has to remember which
#: is which.
TOKEN_BYTES = 32

#: What the client sends. The prefix is for humans reading a log or a config
#: file — it makes an accidentally-pasted token recognisable as one.
PREFIX = "capsule"


class CapsuleTokenQuerySet(models.QuerySet):
    def live(self, now=None):
        now = now or timezone.now()
        return self.filter(revoked_at__isnull=True).exclude(expires_at__lt=now)


class CapsuleToken(models.Model):
    """One credential a client app presents. Hash only, never the secret."""

    owner = models.ForeignKey(settings.AUTH_USER_MODEL,
                              on_delete=models.CASCADE,
                              related_name="capsule_tokens")
    label = models.CharField(
        max_length=120,
        help_text="Which client this is for, so a person can revoke the right "
                  "one without guessing.")

    #: Indexed, stored in the clear. Finds at most one row, cheaply.
    selector = models.CharField(max_length=64, unique=True, db_index=True)

    #: Never the secret itself.
    verifier_hash = models.CharField(max_length=255)

    #: The last few characters of the secret, so an operator can match a token
    #: in a config file to a row without being able to reconstruct it.
    hint = models.CharField(max_length=12, blank=True)

    created_at = models.DateTimeField(default=timezone.now)
    expires_at = models.DateTimeField(null=True, blank=True)
    revoked_at = models.DateTimeField(null=True, blank=True)

    #: Written on use, so a stale token is visible rather than merely suspected.
    #: Deliberately coarse — see `touch()`.
    last_used_at = models.DateTimeField(null=True, blank=True)

    objects = CapsuleTokenQuerySet.as_manager()

    class Meta:
        verbose_name = "capsule API token"
        verbose_name_plural = "capsule API tokens"
        indexes = [models.Index(fields=["owner", "revoked_at"],
                                name="anastasia_token_owner_idx")]

    def __str__(self):
        return f"{self.label} ({self.hint or '…'})"

    # -- minting and checking ---------------------------------------------

    @classmethod
    def issue(cls, *, owner, label: str, expires_at=None):
        """Create a token and return ``(row, raw)``. The raw value is shown
        ONCE and cannot be recovered — that is the point of storing a hash."""
        from django.contrib.auth.hashers import make_password

        selector = secrets.token_urlsafe(TOKEN_BYTES)
        verifier = secrets.token_urlsafe(TOKEN_BYTES)
        row = cls.objects.create(
            owner=owner, label=label, selector=selector,
            verifier_hash=make_password(verifier), hint=verifier[-6:],
            expires_at=expires_at)
        return row, f"{PREFIX}.{selector}.{verifier}"

    @classmethod
    def authenticate(cls, raw: str, *, now=None):
        """The row this token names, or None. Cheap checks first, always.

        Order is the security-relevant part: parse, then ONE indexed lookup,
        then liveness, then — last — the hash comparison. Reversing any of
        those turns an unauthenticated endpoint into a CPU amplifier.
        """
        from django.contrib.auth.hashers import check_password

        if not raw:
            return None
        parts = raw.split(".")
        if len(parts) != 3 or parts[0] != PREFIX:
            return None
        _, selector, verifier = parts

        row = cls.objects.live(now).filter(selector=selector).first()
        if row is None:
            return None
        if not check_password(verifier, row.verifier_hash):
            return None
        return row

    def touch(self, now=None):
        """Record use, but not on every single request.

        A desktop client polls job status every second or two. Writing a row
        per poll turns a read-only endpoint into a write-heavy one for
        information nobody reads at that resolution — "used in the last hour"
        is what a person revoking a stale token actually wants.
        """
        now = now or timezone.now()
        if self.last_used_at and (now - self.last_used_at).total_seconds() < 3600:
            return
        self.last_used_at = now
        self.save(update_fields=["last_used_at"])

    def revoke(self, now=None):
        self.revoked_at = now or timezone.now()
        self.save(update_fields=["revoked_at"])

    @property
    def is_live(self) -> bool:
        now = timezone.now()
        return (self.revoked_at is None
                and (self.expires_at is None or self.expires_at >= now))
