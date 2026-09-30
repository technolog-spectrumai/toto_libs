"""Bucket peering: who may use this host's buckets, and whose buckets we mount.

The vault's half of the platform's ONE host-to-host data channel. The
row-replication engine that used to share this vocabulary is parked
(``limbo/datalink/PARKED.md``); its credential mechanics were right and are
copied here with attribution, its transport never existed, and the sealing
decision made the bucket link the only data plane.

**The identity columns are ``grant_uid`` and ``peer_uid``, not ``uid``.**
Historical, but the history is worth keeping: the retired app-level backup
engine selected models for a signed, pullable archive purely by the presence
of a field named ``uid``, and a grant's magic token or a peer's api key in
such an archive would have handed the puller the bucket link — so vault models
were structurally barred from the name. (The parked datalink app learned and
recorded this exact rule.) The engine is gone — backups are a pg_dump sidecar
now, and a database dump of THIS host never leaves it by design — but the
column names stay: renaming identity columns buys nothing, and the caution
they encode is still true of any future export mechanism.

**The credential pair is two directional models, not one symmetric row.**
A host can export one bucket while mounting another, and rotating the key it
*presents* must not touch the keys it *accepts*. ``BucketGrant`` is "a peer we
let use this bucket" and holds only a hash; ``BucketPeer`` is "a remote bucket
we mount" and holds the secret we present, Fernet-sealed under
``FIELD_ENCRYPTION_KEY`` — clearing's custody convention: every deployed host
mints that key, so peering needs no passphrase ceremony of its own.

**An empty capability set grants nothing.** The vault's own history is the
warning here: ``VaultDirectory.allowed_users`` treats an empty whitelist as
"allow everyone", and that fail-open shape is exactly what a cross-host grant
must never have. Every ``may_*`` below defaults False, and a freshly minted
grant can authenticate and still do nothing until an operator ticks a box.

**Directory ACLs do not cross hosts.** A grant exports a BUCKET, whole: every
file in it is listable and (with ``may_download``) fetchable by the peer.
Half-replicating per-user whitelists would either fail open (see above) or
silently narrow, and both are worse than the honest statement that the
exporting operator grants the bucket or does not.

**Encrypted non-PDF files never cross.** Text- and image-encrypted files are
Fernet-sealed under Argon2id(password, THIS instance's strongbox salt);
ciphertext moved to another host is permanently unopenable there. The peer
API refuses to serve them and transfers skip them, each saying why.
"""
from __future__ import annotations

import base64
import json
import secrets
import uuid

from django.apps import apps as django_apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from django.utils import timezone


# ---------------------------------------------------------------------------
# Capabilities
# ---------------------------------------------------------------------------

#: Every right a grant can carry. ``has_bucket_right`` refuses names outside
#: this tuple: a mistyped right that quietly returns False is a gate nobody
#: can pass and nobody can find (socialhub's privileges resolver, verbatim).
#: ``may_list`` covers the listing AND single-file metadata — metadata of one
#: file is a listing of size one. Copy is not a right of its own: it is
#: ``may_download`` on the source grant and ``may_upload`` on the destination.
BUCKET_RIGHTS = ("may_list", "may_download", "may_upload", "may_delete")


def has_bucket_right(grant, right: str) -> bool:
    """The one resolver. Unknown right → raise; unusable grant → False."""
    if right not in BUCKET_RIGHTS:
        raise ValueError(
            f"{right!r} is not a bucket right; see "
            "toto.vault.peering.BUCKET_RIGHTS")
    return bool(grant is not None and grant.can_be_used
                and getattr(grant, right))


def _default_expiry():
    """Seven days — a grant is a live credential that travels through a chat
    window during pairing, so it expires by default and is extended
    deliberately once the link works. (The backup app's stored-pull default,
    via the parked datalink grant.)"""
    return timezone.now() + timezone.timedelta(days=7)


def _fernet():
    from cryptography.fernet import Fernet

    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode()
                  if isinstance(settings.FIELD_ENCRYPTION_KEY, str)
                  else settings.FIELD_ENCRYPTION_KEY)


#: The one path both halves build URLs from. The server's urls.py and the
#: client's PeerClient format the same constant, so the two cannot drift
#: silently — a drift is a test failure on the reverse() side.
PEER_PATH = "peer/{grant_uid}/{magic_token}/"


class BucketGrant(models.Model):
    """A peer we let use one of this host's buckets. Hash only, never a secret."""

    grant_uid = models.UUIDField(default=uuid.uuid4, unique=True,
                                 editable=False)
    label = models.CharField(
        max_length=180,
        help_text="The peer this grant is for, e.g. 'placidia'. Operators only.")
    bucket = models.ForeignKey(
        "vault.Bucket", on_delete=models.CASCADE, related_name="peer_grants",
        help_text="The exported bucket. Whole-bucket: every file in it is "
                  "covered by the rights below.")
    magic_token = models.CharField(
        max_length=128, unique=True, default=secrets.token_urlsafe,
        help_text=(
            "High-entropy URL segment, checked before the api key. Its "
            "purpose is to make the expensive key verification unreachable "
            "by guessing."))
    api_key_hash = models.CharField(max_length=255, blank=True)
    api_key_hint = models.CharField(
        max_length=16, blank=True,
        help_text="Last characters of the key, so an operator can tell "
                  "which one is live.")

    #: The capability set. ALL default False: a fresh grant grants nothing.
    may_list = models.BooleanField(
        default=False, help_text="List the bucket and read file metadata.")
    may_download = models.BooleanField(
        default=False, help_text="Fetch file content.")
    may_upload = models.BooleanField(
        default=False, help_text="Add files to the bucket.")
    may_delete = models.BooleanField(
        default=False, help_text="Delete files from the bucket.")

    is_active = models.BooleanField(default=True)
    expires_at = models.DateTimeField(null=True, blank=True,
                                      default=_default_expiry)
    key_rotated_at = models.DateTimeField(null=True, blank=True)

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bucket_grants_created")
    created_at = models.DateTimeField(auto_now_add=True)

    last_read_at = models.DateTimeField(null=True, blank=True)
    read_count = models.PositiveIntegerField(default=0)
    last_peer_ip = models.GenericIPAddressField(
        null=True, blank=True,
        help_text="Where the last use came from, so a human notices a new "
                  "source.")

    class Meta:
        ordering = ["-created_at"]
        verbose_name = "bucket grant"
        indexes = [models.Index(fields=["grant_uid", "magic_token"])]

    def __str__(self):
        return f"grant of {self.bucket_id and self.bucket.slug} to {self.label}"

    def clean(self):
        super().clean()
        # No daisy-chaining: exporting a bucket whose bytes live on a THIRD
        # host would make this host a relay whose failures nobody can read.
        if self.bucket_id and self.bucket.storage_backend == "remote_toto":
            raise ValidationError(
                "A mounted remote bucket cannot be re-exported — grant it "
                "from the host that holds the bytes.")

    def issue_api_key(self, raw_key=None):
        """Mint a key, store only its hash, and return the raw value once."""
        from django.contrib.auth.hashers import make_password

        raw_key = raw_key or secrets.token_urlsafe(48)
        self.api_key_hash = make_password(raw_key)
        self.api_key_hint = raw_key[-8:]
        return raw_key

    def verify_api_key(self, raw_key):
        """PBKDF2 at the project's iteration count — deliberately the LAST
        check. At 600k iterations this is ~100ms of CPU on an unauthenticated
        endpoint; the SSO provider once saturated at ~35 req/s because
        everything cheap did not run first."""
        from django.contrib.auth.hashers import check_password

        if not raw_key or not self.api_key_hash:
            return False
        return check_password(raw_key, self.api_key_hash)

    @property
    def is_expired(self):
        return bool(self.expires_at and timezone.now() >= self.expires_at)

    @property
    def can_be_used(self):
        return self.is_active and not self.is_expired

    def base_path(self) -> str:
        return "/vault/" + PEER_PATH.format(grant_uid=self.grant_uid,
                                            magic_token=self.magic_token)


class BucketPeer(models.Model):
    """A remote bucket we mount. Holds the secret we present, sealed."""

    peer_uid = models.UUIDField(default=uuid.uuid4, unique=True,
                                editable=False)
    label = models.CharField(
        max_length=180,
        help_text="What this host calls the mount, e.g. 'Placidia media'. "
                  "This is the name badges show — never the URL.")
    base_url = models.URLField(
        help_text="The peer's root, e.g. https://placidia.example.org")

    # What the peer issued us, from its BucketGrant (the pairing code).
    grant_uid = models.UUIDField()
    magic_token = models.CharField(max_length=128)
    api_key_encrypted = models.BinaryField(blank=True, default=b"")
    api_key_hint = models.CharField(max_length=16, blank=True)
    remote_bucket_slug = models.SlugField(
        max_length=140, blank=True,
        help_text="The exported bucket's slug on the peer, learned at the "
                  "pairing probe.")
    capabilities = models.JSONField(
        default=list, blank=True,
        help_text="What the peer said it granted. Advisory — the peer "
                  "enforces it.")

    is_active = models.BooleanField(default=True)
    probe_error = models.TextField(
        blank=True,
        help_text="Why the pairing probe failed, so a wrong key or an "
                  "expired grant surfaces immediately rather than during a "
                  "refresh.")
    peer_site_name = models.CharField(max_length=180, blank=True)

    #: Reachability, stamped only by jobs and real transfers — never by a
    #: page render (the jess rule: no third party's latency on a request's
    #: critical path). Badges read these columns.
    last_ok_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    paired_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL,
        null=True, blank=True, related_name="bucket_peers_paired")
    paired_at = models.DateTimeField(auto_now_add=True)
    last_pull_at = models.DateTimeField(null=True, blank=True)
    pull_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-paired_at"]
        verbose_name = "bucket peer"
        constraints = [
            models.UniqueConstraint(fields=["base_url", "grant_uid"],
                                    name="vault_one_peer_per_grant"),
        ]

    def __str__(self):
        return f"peer {self.label}"

    def set_api_key(self, raw: str) -> None:
        self.api_key_encrypted = _fernet().encrypt(raw.encode())
        self.api_key_hint = raw[-8:]

    def get_api_key(self) -> str:
        if not self.api_key_encrypted:
            return ""
        return _fernet().decrypt(bytes(self.api_key_encrypted)).decode()


# ---------------------------------------------------------------------------
# The pairing wire format
# ---------------------------------------------------------------------------
# Moved here from admin.py so a non-admin door can mint and read the same code.
# ONE wire format only: two toto hosts on different versions must keep pairing,
# so this is the single place that knows the shape. admin.py keeps underscore
# aliases because tests import the old names from there.


def pairing_code_for(grant, raw_key, *, host: str = ""):
    """The one wire format for handing a grant to the peer's operator.

    base64 over JSON, versioned. Carries the raw api key, so it exists only in
    the message that shows it — never in a column (the grant stores a hash, the
    peer that pastes it stores sealed ciphertext).

    ``expires_at`` (always, when the grant expires) and ``host`` (the exporting
    host's base URL, when the minting door knows it — a request does) were
    added on 2026-09-30 so the mounting side can SHOW what a code grants before
    anything is saved. Both are optional keys inside v1: an older decoder
    ignores them, an older code simply lacks them, and the mounting side says
    "not stated" instead of guessing.
    """
    payload = {
        "v": 1,
        "grant_uid": str(grant.grant_uid),
        "magic_token": grant.magic_token,
        "api_key": raw_key,
        "bucket": grant.bucket.slug,
        "rights": [r for r in BUCKET_RIGHTS if getattr(grant, r)],
    }
    if grant.expires_at:
        payload["expires_at"] = grant.expires_at.isoformat()
    if host:
        payload["host"] = str(host).rstrip("/")
    return base64.b64encode(json.dumps(payload).encode()).decode()


def decode_pairing_code(code):
    """Inverse of :func:`pairing_code_for`.

    Raises ``ValidationError`` with a sentence an operator can act on — the
    code travels through a chat window and arrives mangled more often than
    wrong.
    """
    from django import forms

    try:
        payload = json.loads(base64.b64decode(code.strip().encode()))
    except Exception:
        raise forms.ValidationError(
            "That does not decode as a pairing code. Paste the whole code, "
            "with no surrounding quotes or line breaks.")
    if not isinstance(payload, dict) or payload.get("v") != 1:
        raise forms.ValidationError(
            "Unsupported pairing-code version — mint a fresh code on the "
            "exporting host.")
    missing = [k for k in ("grant_uid", "magic_token", "api_key")
               if not payload.get(k)]
    if missing:
        raise forms.ValidationError(
            f"Pairing code is missing {', '.join(missing)} — mint a fresh "
            "code on the exporting host.")
    return payload


def federated_host_choices():
    """Base URLs of hosts this one is federated with, from the SSO pairing
    rows — looked up at runtime so toto-base never imports toto-auth. Empty
    when neither side of SSO is installed; the form then falls back to the
    free-text URL field."""
    choices = []
    try:
        SSORelyingParty = django_apps.get_model("sso_master", "SSORelyingParty")
    except LookupError:
        SSORelyingParty = None
    if SSORelyingParty is not None:
        from urllib.parse import urlsplit
        for rp in SSORelyingParty.objects.filter(active=True):
            uris = rp.redirect_uri_list()
            if not uris:
                continue
            parts = urlsplit(uris[0])
            base = f"{parts.scheme}://{parts.netloc}"
            choices.append((base, f"{rp.name} ({base})"))
    try:
        OIDCProviderConfig = django_apps.get_model("sso_client", "OIDCProviderConfig")
    except LookupError:
        OIDCProviderConfig = None
    if OIDCProviderConfig is not None:
        for cfg in OIDCProviderConfig.objects.filter(active=True):
            base = cfg.portal_url.rstrip("/")
            choices.append((base, f"{cfg.label} ({base})"))
    seen, unique = set(), []
    for value, label in choices:
        if value not in seen:
            seen.add(value)
            unique.append((value, label))
    return unique


def apply_manifest(peer, manifest):
    """Stamp a successful probe onto the peer row.

    Extracted from ``BucketPeerAdmin._probe`` so the admin and any other door
    that probes write the SAME columns. The caller owns the message it shows;
    this owns what is persisted.
    """
    peer.probe_error = ""
    peer.peer_site_name = manifest.get("site_name", "")
    peer.remote_bucket_slug = manifest.get("bucket", peer.remote_bucket_slug)
    peer.capabilities = manifest.get("rights", peer.capabilities)
    peer.last_ok_at = timezone.now()
    peer.last_error = ""
    peer.save(update_fields=["probe_error", "peer_site_name",
                             "remote_bucket_slug", "capabilities",
                             "last_ok_at", "last_error"])
    return peer
