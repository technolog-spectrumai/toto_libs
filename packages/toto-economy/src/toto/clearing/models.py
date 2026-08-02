"""Clearing — bilateral ledger federation between two toto platforms.

One parent and one child zenobia keep their own double-entry books (toto.assets is
always host-truth) and exchange SIGNED messages about the movements that concern them
both. This module holds the trust anchor: the peer platform, its credentials, the
pinned signing keys, and the per-peer message sequence.

Design constraints inherited from the fleet:
* toto-economy must not import toto-auth — the peer's HTTP credentials live HERE (a
  host-side command may copy them from the identity pairing, but this package never
  reaches into it).
* Secrets at rest follow the assets convention (WalletAuthorization): Fernet under
  FIELD_ENCRYPTION_KEY — every deployed host mints that key, so clearing needs no
  passphrase ceremony of its own.
* Cheap checks precede expensive ones on unauthenticated surfaces: an inbound request
  is matched against a SHA-256 fingerprint before any constant-time secret compare.
"""

from __future__ import annotations

import base64
import hashlib
import uuid as uuid_lib

from django.db import models


def _fernet():
    from django.conf import settings
    from cryptography.fernet import Fernet

    return Fernet(settings.FIELD_ENCRYPTION_KEY.encode()
                  if isinstance(settings.FIELD_ENCRYPTION_KEY, str)
                  else settings.FIELD_ENCRYPTION_KEY)


def cheap_secret_hash(secret: str) -> str:
    """SHA-256 fingerprint used to reject junk before the real compare."""
    return hashlib.sha256(secret.encode()).hexdigest()


class LedgerPeer(models.Model):
    """The other platform. Exactly one active row in a parent↔child pair.

    ``role`` is OUR view of the peer: a parent host's row says ``child`` and vice
    versa. The parent is always the swap coordinator. ``platform_id`` is the stable
    string identity that stamps ``LedgerTransaction.origin_platform`` on rows applied
    from this peer; ``epoch`` changes when the peer's database is reset — a mismatch
    at handshake suspends the trustlines until an operator re-adopts (the RESET=1
    recovery rule; see clearing.md).
    """

    ROLE_PARENT = "parent"
    ROLE_CHILD = "child"
    ROLES = [(ROLE_PARENT, "Parent"), (ROLE_CHILD, "Child")]

    STATUS_PENDING = "pending"
    STATUS_ACTIVE = "active"
    STATUS_SUSPENDED = "suspended"
    STATUSES = [(STATUS_PENDING, "Pending"), (STATUS_ACTIVE, "Active"),
                (STATUS_SUSPENDED, "Suspended")]

    name = models.CharField(max_length=100)
    base_url = models.URLField(help_text="The peer's public base URL, e.g. https://master.example.com")
    role = models.CharField(max_length=10, choices=ROLES)
    platform_id = models.CharField(max_length=100, unique=True)
    status = models.CharField(max_length=12, choices=STATUSES, default=STATUS_PENDING)

    # ── Transport credentials (HTTP Basic, shared secret per link) ─────────
    client_id = models.CharField(max_length=128)
    client_secret_encrypted = models.BinaryField(blank=True, default=b"")
    previous_secret_encrypted = models.BinaryField(blank=True, default=b"")
    secret_cheap_hash = models.CharField(max_length=64, blank=True, default="")
    previous_cheap_hash = models.CharField(max_length=64, blank=True, default="")

    # ── Signing keys (Ed25519; ours private, theirs pinned public) ─────────
    our_private_key_encrypted = models.BinaryField(blank=True, default=b"")
    our_public_key_pem = models.TextField(blank=True, default="")
    peer_public_key_pem = models.TextField(blank=True, default="")

    # ── Stream state ───────────────────────────────────────────────────────
    epoch = models.CharField(max_length=36, default=uuid_lib.uuid4)
    peer_epoch = models.CharField(max_length=36, blank=True, default="")
    send_seq = models.BigIntegerField(default=0)
    recv_seq = models.BigIntegerField(default=0)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self):
        return f"{self.name} ({self.role}, {self.status})"

    # ── Secret custody ─────────────────────────────────────────────────────
    def set_secret(self, secret: str) -> None:
        """Rotate the link secret; the outgoing one stays valid (grace window)."""
        if self.client_secret_encrypted:
            self.previous_secret_encrypted = self.client_secret_encrypted
            self.previous_cheap_hash = self.secret_cheap_hash
        self.client_secret_encrypted = _fernet().encrypt(secret.encode())
        self.secret_cheap_hash = cheap_secret_hash(secret)

    def get_secret(self) -> str:
        if not self.client_secret_encrypted:
            return ""
        return _fernet().decrypt(bytes(self.client_secret_encrypted)).decode()

    def check_secret(self, presented: str) -> bool:
        """current|previous acceptance, cheap fingerprint first."""
        import hmac as _hmac

        fp = cheap_secret_hash(presented)
        if fp == self.secret_cheap_hash:
            return _hmac.compare_digest(presented, self.get_secret())
        if fp == self.previous_cheap_hash and self.previous_secret_encrypted:
            previous = _fernet().decrypt(bytes(self.previous_secret_encrypted)).decode()
            return _hmac.compare_digest(presented, previous)
        return False

    # ── Signing keys ───────────────────────────────────────────────────────
    def ensure_keypair(self) -> None:
        """Generate our Ed25519 pair on first use; private half Fernet-sealed."""
        if self.our_private_key_encrypted:
            return
        from cryptography.hazmat.primitives import serialization
        from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

        key = Ed25519PrivateKey.generate()
        priv = key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
        pub = key.public_key().public_bytes(
            serialization.Encoding.PEM,
            serialization.PublicFormat.SubjectPublicKeyInfo,
        )
        self.our_private_key_encrypted = _fernet().encrypt(priv)
        self.our_public_key_pem = pub.decode()

    def sign(self, message: bytes) -> str:
        from cryptography.hazmat.primitives import serialization

        self.ensure_keypair()
        key = serialization.load_pem_private_key(
            _fernet().decrypt(bytes(self.our_private_key_encrypted)), password=None)
        return base64.b64encode(key.sign(message)).decode()

    def verify_peer(self, message: bytes, signature_b64: str) -> bool:
        """Verify against the PINNED peer key — never a payload-carried one."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import serialization

        if not self.peer_public_key_pem:
            return False
        try:
            key = serialization.load_pem_public_key(self.peer_public_key_pem.encode())
            key.verify(base64.b64decode(signature_b64), message)
            return True
        except (InvalidSignature, ValueError):
            return False
