"""
Document signing service.

Uses Ed25519 (stored as EncryptedPrivateKey in a UserStrongbox) to produce
and verify cryptographic signatures over canonical document payloads.

The private key never leaves the in-memory GervazyCryptoSession. Only the
public key is stored in plaintext (safe, by design).

Typical flow
------------
1. Caller opens a GervazyCryptoSession with the person's strongbox password.
2. Call SigningService.sign_document(session, person, payload) — provisions a
   signing key the first time, then signs and returns a DocumentSignature.
3. Call SigningService.verify(person, payload, signature_b64) anywhere to
   confirm a signature is authentic — no password needed.
"""
from __future__ import annotations

import base64
import uuid as _uuid
from dataclasses import dataclass

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives import serialization
from cryptography.exceptions import InvalidSignature


@dataclass(frozen=True)
class DocumentSignature:
    """Result returned by SigningService.sign_document."""
    payload: bytes
    signature_b64: str
    signing_key_id: str
    public_key_pem: str


class SigningError(Exception):
    pass


class SigningService:

    # ------------------------------------------------------------------
    # Key provisioning
    # ------------------------------------------------------------------

    @staticmethod
    def get_active_signing_key(person):
        """Return the active PersonSigningKey for person, or None."""
        from toto.gervazy.models import PersonSigningKey
        return (
            PersonSigningKey.objects
            .filter(person=person, is_active=True)
            .select_related("encrypted_private_key__wrapped_key__vmk__strongbox")
            .first()
        )

    @staticmethod
    def provision_signing_key(session, wrapped_key, person) -> "PersonSigningKey":
        """
        Generate an Ed25519 key pair, encrypt the private key into the
        given wrapped_key, retire any existing active signing key for this
        person, and return the new PersonSigningKey.

        Requires an open GervazyCryptoSession for the strongbox that owns
        wrapped_key.
        """
        from toto.gervazy.models import PersonSigningKey

        # Generate key pair.
        private_key = Ed25519PrivateKey.generate()
        public_key = private_key.public_key()

        private_pem = private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        ).decode("utf-8")

        public_pem = public_key.public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        ).decode("utf-8")

        key_id = f"person-{person.pk}-signing-{_uuid.uuid4().hex[:8]}"

        epk = session.encrypt_private_key(
            wrapped_key,
            private_pem,
            key_id=key_id,
            key_type="Ed25519",
            public_key_pem=public_pem,
            issuer="",
        )

        # Retire previous active key.
        PersonSigningKey.objects.filter(person=person, is_active=True).update(
            is_active=False,
        )

        return PersonSigningKey.objects.create(
            person=person,
            encrypted_private_key=epk,
            is_active=True,
        )

    # ------------------------------------------------------------------
    # Signing
    # ------------------------------------------------------------------

    @staticmethod
    def sign_document(
        session,
        person,
        payload: bytes,
        *,
        wrapped_key=None,
    ) -> DocumentSignature:
        """
        Sign payload with person's active signing key.

        If the person has no active signing key yet, one is provisioned
        using wrapped_key (required in that case — raises SigningError if
        omitted).
        """
        psk = SigningService.get_active_signing_key(person)

        if psk is None:
            if wrapped_key is None:
                raise SigningError(
                    "Person has no signing key. Pass wrapped_key to provision one."
                )
            psk = SigningService.provision_signing_key(session, wrapped_key, person)

        epk = psk.encrypted_private_key

        # Verify the session owns the right strongbox.
        if epk.strongbox_id != session._strongbox.id:
            raise SigningError(
                "The open session does not match the strongbox that holds "
                "this person's signing key."
            )

        private_pem = session.decrypt_private_key(epk)
        private_key = serialization.load_pem_private_key(
            private_pem.encode("utf-8"), password=None
        )

        if not isinstance(private_key, Ed25519PrivateKey):
            raise SigningError(f"Expected Ed25519 key, got {type(private_key).__name__}.")

        signature_bytes = private_key.sign(payload)
        signature_b64 = base64.b64encode(signature_bytes).decode("ascii")

        return DocumentSignature(
            payload=payload,
            signature_b64=signature_b64,
            signing_key_id=epk.key_id,
            public_key_pem=epk.public_key_pem,
        )

    # ------------------------------------------------------------------
    # Verification
    # ------------------------------------------------------------------

    @staticmethod
    def verify(person, payload: bytes, signature_b64: str) -> bool:
        """
        Verify a signature against the person's active public key.

        Also accepts archived keys — iterates all PersonSigningKey records
        for this person and tries each public key. Returns True if any
        matches (a retired key may still be the correct one for an old sig).

        No password or session required.
        """
        from toto.gervazy.models import PersonSigningKey

        signature_bytes = base64.b64decode(signature_b64)

        for psk in PersonSigningKey.objects.filter(person=person).select_related("encrypted_private_key"):
            pub_pem = psk.encrypted_private_key.public_key_pem
            try:
                public_key: Ed25519PublicKey = serialization.load_pem_public_key(
                    pub_pem.encode("utf-8")
                )
                public_key.verify(signature_bytes, payload)
                return True
            except (InvalidSignature, Exception):
                continue

        return False

    # ------------------------------------------------------------------
    # Canonical payload helpers
    # ------------------------------------------------------------------

    @staticmethod
    def canonical_certificate_payload(certificate, teacher, signed_at) -> bytes:
        """
        Build the canonical byte string for a teacher signing a certificate.

        Format (newline-separated, UTF-8):
            sign:certificate
            uuid:<certificate.uuid>
            person:<certificate.person.pk>
            course:<certificate.course_id or ''>
            teacher:<teacher.pk>
            at:<signed_at.isoformat()>
        """
        lines = [
            "sign:certificate",
            f"uuid:{certificate.uuid}",
            f"person:{certificate.person.pk}",
            f"course:{certificate.course_id or ''}",
            f"teacher:{teacher.pk}",
            f"at:{signed_at.isoformat()}",
        ]
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def canonical_contract_payload(contract, person, signed_at) -> bytes:
        """
        Build the canonical byte string that represents a person signing a
        contract at a given moment.

        Format (newline-separated, UTF-8):
            sign:contract
            uuid:<contract.uuid>
            name:<contract.name>
            person:<person.pk>
            at:<signed_at.isoformat()>
        """
        lines = [
            "sign:contract",
            f"uuid:{contract.uuid}",
            f"name:{contract.name}",
            f"person:{person.pk}",
            f"at:{signed_at.isoformat()}",
        ]
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def canonical_constitution_payload(constitution, person, signed_at) -> bytes:
        lines = [
            "sign:constitution",
            f"id:{constitution.pk}",
            f"slug:{constitution.slug}",
            f"community:{constitution.community_id}",
            f"person:{person.pk}",
            f"at:{signed_at.isoformat()}",
        ]
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def canonical_contract_file_payload(doc_id, version, content_hash, party_id, signed_at_iso) -> bytes:
        """
        Canonical payload for signing a file-based ``.contract`` (toto.notarius).

        Built entirely from values stored in the contract XML so a verifier can
        reconstruct it from the file alone (no DB row required).

        Format (newline-separated, UTF-8):
            sign:contract-file
            doc:<id>
            version:<version>
            content-sha256:<contentHash>
            party:<partyId>
            at:<signedAt ISO-8601>
        """
        lines = [
            "sign:contract-file",
            f"doc:{doc_id}",
            f"version:{version}",
            f"content-sha256:{content_hash}",
            f"party:{party_id}",
            f"at:{signed_at_iso}",
        ]
        return "\n".join(lines).encode("utf-8")

    @staticmethod
    def verify_with_public_key(public_key_pem: str, payload: bytes, signature_b64: str) -> bool:
        """
        Verify a signature using a public key carried alongside it (e.g. embedded in
        a ``.contract`` signature element) — no Person/DB lookup needed.
        """
        try:
            public_key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
            if not isinstance(public_key, Ed25519PublicKey):
                return False
            public_key.verify(base64.b64decode(signature_b64), payload)
            return True
        except (InvalidSignature, Exception):
            return False
