"""
Gervazy crypto layer.

Envelope hierarchy:

    password -> Argon2id UKEK -> decrypt VMK -> decrypt DEK -> decrypt data

All AES keys are raw 32 bytes.

UserStrongbox.derive_key() returns base64url(32 bytes), so this module decodes it
back to raw 32 bytes before using it as an AES-256-GCM key.
"""

import base64
import datetime as dt
import ipaddress
import os

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import NameOID


AES_GCM_KEY_SIZE = 32
AES_GCM_NONCE_SIZE = 12


# ---------------------------------------------------------------------------
# Low-level AES-256-GCM helpers
# ---------------------------------------------------------------------------

def _validate_aes_key(key: bytes) -> None:
    if not isinstance(key, bytes):
        raise TypeError("AES-GCM key must be bytes.")

    if len(key) != AES_GCM_KEY_SIZE:
        raise ValueError("AES-256-GCM key must be exactly 32 bytes.")


def _validate_nonce(nonce: bytes) -> None:
    if not isinstance(nonce, bytes):
        raise TypeError("AES-GCM nonce must be bytes.")

    if len(nonce) != AES_GCM_NONCE_SIZE:
        raise ValueError("AES-GCM nonce must be exactly 12 bytes.")


def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    """Encrypt plaintext and return (ciphertext_with_tag, nonce)."""
    _validate_aes_key(key)

    if not isinstance(plaintext, bytes):
        raise TypeError("Plaintext must be bytes.")

    if aad is None:
        aad = b""

    if not isinstance(aad, bytes):
        raise TypeError("AAD must be bytes.")

    nonce = os.urandom(AES_GCM_NONCE_SIZE)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad)
    return ciphertext, nonce


def aes_gcm_decrypt(key: bytes, ciphertext: bytes, nonce: bytes, aad: bytes = b"") -> bytes:
    """Decrypt ciphertext. Raises InvalidTag if authentication fails."""
    _validate_aes_key(key)
    _validate_nonce(nonce)

    if not isinstance(ciphertext, bytes):
        raise TypeError("Ciphertext must be bytes.")

    if aad is None:
        aad = b""

    if not isinstance(aad, bytes):
        raise TypeError("AAD must be bytes.")

    return AESGCM(key).decrypt(nonce, ciphertext, aad)


def generate_raw_key() -> bytes:
    """Generate a random 32-byte AES-256 key."""
    return os.urandom(AES_GCM_KEY_SIZE)


def decode_derived_key(encoded_key: bytes) -> bytes:
    """Decode UserStrongbox.derive_key() output into a raw AES-256-GCM key."""
    raw_key = base64.urlsafe_b64decode(encoded_key)

    if len(raw_key) != AES_GCM_KEY_SIZE:
        raise ValueError("Decoded derived key must be exactly 32 bytes.")

    return raw_key


# ---------------------------------------------------------------------------
# TLS certificate helpers
# ---------------------------------------------------------------------------

def generate_self_signed_certificate(
    *,
    common_name: str,
    dns_names: list[str] | None = None,
    ip_addresses: list[str] | None = None,
    valid_days: int = 825,
) -> tuple[bytes, bytes]:
    """Generate a self-signed RSA certificate and private key in PEM format."""
    dns_names = dns_names or [common_name]
    ip_addresses = ip_addresses or []

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    subject = issuer = x509.Name(
        [
            x509.NameAttribute(NameOID.COUNTRY_NAME, "PL"),
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, _OWN_ORGANIZATION),
            x509.NameAttribute(NameOID.COMMON_NAME, common_name),
        ]
    )

    san_names: list[x509.GeneralName] = [x509.DNSName(name) for name in dns_names]
    san_names.extend(x509.IPAddress(ipaddress.ip_address(address)) for address in ip_addresses)

    now = dt.datetime.now(dt.timezone.utc)

    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(issuer)
        .public_key(private_key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - dt.timedelta(minutes=5))
        .not_valid_after(now + dt.timedelta(days=valid_days))
        .add_extension(x509.SubjectAlternativeName(san_names), critical=False)
        .add_extension(x509.BasicConstraints(ca=True, path_length=None), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                key_encipherment=True,
                key_cert_sign=True,
                key_agreement=False,
                content_commitment=False,
                data_encipherment=False,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .sign(private_key, hashes.SHA256())
    )

    key_pem = private_key.private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.TraditionalOpenSSL,
        encryption_algorithm=serialization.NoEncryption(),
    )

    cert_pem = certificate.public_bytes(serialization.Encoding.PEM)
    return cert_pem, key_pem


#: A certificate this module made is renewed this long before it ends.
RENEW_WITHIN_DAYS = 30
_OWN_ORGANIZATION = "Toto Local Development"


def _own_certificate_needs_renewal(cert_path, wanted_dns: set, wanted_ips: set) -> bool:
    """True for a certificate this module made whose names differ from the
    wanted ones or which ends within RENEW_WITHIN_DAYS. False for one it did
    not make (somebody's own, pushed there) and for one that still fits; an
    unreadable file is made again."""
    try:
        with open(cert_path, "rb") as cert_file:
            cert = x509.load_pem_x509_certificate(cert_file.read())
    except (OSError, ValueError):
        return True
    organizations = [a.value for a in cert.subject.get_attributes_for_oid(NameOID.ORGANIZATION_NAME)]
    if cert.issuer != cert.subject or _OWN_ORGANIZATION not in organizations:
        return False
    try:
        san = cert.extensions.get_extension_for_class(x509.SubjectAlternativeName).value
        have_dns = set(san.get_values_for_type(x509.DNSName))
        have_ips = {str(a) for a in san.get_values_for_type(x509.IPAddress)}
    except x509.ExtensionNotFound:
        have_dns, have_ips = set(), set()
    if (have_dns, have_ips) != (wanted_dns, wanted_ips):
        return True
    ends = getattr(cert, "not_valid_after_utc", None)
    if ends is None:  # cryptography < 42, as a host's own Python may carry (deploy.py)
        ends = cert.not_valid_after.replace(tzinfo=dt.timezone.utc)
    return ends - dt.datetime.now(dt.timezone.utc) < dt.timedelta(days=RENEW_WITHIN_DAYS)


def ensure_self_signed_certificate(
    cert_path,
    key_path,
    *,
    common_name: str = "localhost",
    dns_names: list[str] | None = None,
    ip_addresses: list[str] | None = None,
) -> bool:
    """Create the self-signed certificate, or renew the one this made before.

    Kept while both files exist and the certificate still fits (stage 51):
    one this function made (its own organisation, self-issued) is made again
    when its DNS or IP names differ from the ones asked for, or when it ends
    within RENEW_WITHIN_DAYS. It used to be kept as soon as both files
    existed, so a name added to the config, or 825 days passing, left every
    browser warning. A certificate somebody else put there is never touched.
    Returns whether it wrote one.
    """
    cert_path = os.fspath(cert_path)
    key_path = os.fspath(key_path)

    if os.path.exists(cert_path) and os.path.exists(key_path):
        wanted_dns = set(dns_names or [common_name])
        wanted_ips = {str(ipaddress.ip_address(a)) for a in (ip_addresses or [])}
        if not _own_certificate_needs_renewal(cert_path, wanted_dns, wanted_ips):
            return False

    cert_pem, key_pem = generate_self_signed_certificate(
        common_name=common_name,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
    )

    cert_dir = os.path.dirname(cert_path)
    key_dir = os.path.dirname(key_path)

    if cert_dir:
        os.makedirs(cert_dir, exist_ok=True)
    if key_dir:
        os.makedirs(key_dir, exist_ok=True)

    with open(cert_path, "wb") as cert_file:
        cert_file.write(cert_pem)

    with open(key_path, "wb") as key_file:
        key_file.write(key_pem)

    os.chmod(key_path, 0o600)
    return True


# ---------------------------------------------------------------------------
# GervazyCryptoSession
# ---------------------------------------------------------------------------

class GervazyCryptoSession:
    """
    In-memory session for an unlocked UserStrongbox.

    Decrypted key material is cached in memory only:
    - UKEK
    - VMK
    - DEK

    Create a fresh session per request or operation. Do not share across threads.
    """

    def __init__(self, strongbox, password: str):
        self._strongbox = strongbox
        self._vault = strongbox  # Backward-compatible attribute during transition.
        self._ukek: bytes = decode_derived_key(strongbox.derive_key(password))

        # Cache by database primary key, not by version.
        self._vmk_cache: dict[object, bytes] = {}
        self._dek_cache: dict[object, bytes] = {}

    # ------------------------------------------------------------------
    # State checks
    # ------------------------------------------------------------------

    def _require_active_vmk(self, vmk) -> None:
        if vmk.strongbox_id != self._strongbox.id:
            raise RuntimeError("VMK does not belong to this strongbox.")

        if vmk.state != "active":
            raise RuntimeError(f"VMK is not active. Current state: {vmk.state}")

    def _require_active_dek(self, wrapped_key) -> None:
        if wrapped_key.strongbox_id != self._strongbox.id:
            raise RuntimeError("WrappedDataKey does not belong to this strongbox.")

        if wrapped_key.state != "active":
            raise RuntimeError(f"WrappedDataKey is not active. Current state: {wrapped_key.state}")

        self._require_active_vmk(wrapped_key.vmk)

    def _require_active_secret(self, secret) -> None:
        if secret.strongbox_id != self._strongbox.id:
            raise RuntimeError("EncryptedSecret does not belong to this strongbox.")

        if secret.state != "active":
            raise RuntimeError(f"EncryptedSecret is not active. Current state: {secret.state}")

        if secret.is_expired():
            raise RuntimeError("EncryptedSecret is expired.")

        self._require_active_dek(secret.wrapped_key)

    def _require_active_private_key(self, epk) -> None:
        if epk.strongbox_id != self._strongbox.id:
            raise RuntimeError("EncryptedPrivateKey does not belong to this strongbox.")

        if epk.state != "active":
            raise RuntimeError(f"EncryptedPrivateKey is not active. Current state: {epk.state}")

        self._require_active_dek(epk.wrapped_key)

    # ------------------------------------------------------------------
    # Internal key unwrapping
    # ------------------------------------------------------------------

    def _unwrap_vmk(self, vmk) -> bytes:
        self._require_active_vmk(vmk)
        cache_key = vmk.pk

        if cache_key not in self._vmk_cache:
            raw_vmk = aes_gcm_decrypt(
                self._ukek,
                bytes(vmk.encrypted_vmk),
                bytes(vmk.nonce),
            )

            if len(raw_vmk) != AES_GCM_KEY_SIZE:
                raise RuntimeError("Unwrapped VMK is not a valid AES-256 key.")

            self._vmk_cache[cache_key] = raw_vmk

        return self._vmk_cache[cache_key]

    def _unwrap_dek(self, wrapped_key) -> bytes:
        self._require_active_dek(wrapped_key)
        cache_key = wrapped_key.pk

        if cache_key not in self._dek_cache:
            raw_vmk = self._unwrap_vmk(wrapped_key.vmk)

            raw_dek = aes_gcm_decrypt(
                raw_vmk,
                bytes(wrapped_key.encrypted_dek),
                bytes(wrapped_key.nonce),
            )

            if len(raw_dek) != AES_GCM_KEY_SIZE:
                raise RuntimeError("Unwrapped DEK is not a valid AES-256 key.")

            self._dek_cache[cache_key] = raw_dek

        return self._dek_cache[cache_key]

    # ------------------------------------------------------------------
    # Public decrypt methods
    # ------------------------------------------------------------------

    def decrypt_secret(self, secret) -> str:
        """Decrypt an EncryptedSecret and return the plaintext string."""
        self._require_active_secret(secret)
        dek = self._unwrap_dek(secret.wrapped_key)

        plaintext = aes_gcm_decrypt(
            dek,
            bytes(secret.ciphertext),
            bytes(secret.nonce),
            bytes(secret.aad or b""),
        )

        return plaintext.decode("utf-8")

    def decrypt_private_key(self, epk) -> str:
        """Decrypt an EncryptedPrivateKey and return the PEM string."""
        self._require_active_private_key(epk)
        dek = self._unwrap_dek(epk.wrapped_key)

        plaintext = aes_gcm_decrypt(
            dek,
            bytes(epk.encrypted_private_key),
            bytes(epk.nonce),
            bytes(epk.aad or b""),
        )

        return plaintext.decode("utf-8")

    # ------------------------------------------------------------------
    # Public encrypt helpers
    # ------------------------------------------------------------------

    def encrypt_secret(
        self,
        wrapped_key,
        plaintext: str,
        *,
        name: str,
        purpose: str = "",
    ) -> "EncryptedSecret":
        """
        Encrypt a string and save it as an EncryptedSecret.

        This creates the object first to get a stable UUID, then uses that UUID
        in AAD, then saves ciphertext.
        """
        from toto.gervazy.models import EncryptedSecret

        self._require_active_dek(wrapped_key)

        secret = EncryptedSecret.objects.create(
            strongbox=self._strongbox,
            wrapped_key=wrapped_key,
            name=name,
            purpose=purpose,
            ciphertext=b"temporary",
            state="active",
        )

        aad = secret.build_aad()
        dek = self._unwrap_dek(wrapped_key)

        ciphertext, nonce = aes_gcm_encrypt(
            dek,
            plaintext.encode("utf-8"),
            aad,
        )

        secret.ciphertext = ciphertext
        secret.nonce = nonce
        secret.aad = aad
        secret.save(update_fields=["ciphertext", "nonce", "aad", "updated_at"])
        return secret

    def encrypt_private_key(
        self,
        wrapped_key,
        private_key_pem: str,
        *,
        key_id: str,
        key_type: str,
        public_key_pem: str,
        issuer: str = "",
        aad: bytes = b"",
    ) -> "EncryptedPrivateKey":
        """Encrypt a private key PEM and save it as an EncryptedPrivateKey."""
        from toto.gervazy.models import EncryptedPrivateKey

        self._require_active_dek(wrapped_key)
        dek = self._unwrap_dek(wrapped_key)

        ciphertext, nonce = aes_gcm_encrypt(
            dek,
            private_key_pem.encode("utf-8"),
            aad or b"",
        )

        return EncryptedPrivateKey.objects.create(
            strongbox=self._strongbox,
            wrapped_key=wrapped_key,
            key_id=key_id,
            key_type=key_type,
            public_key_pem=public_key_pem,
            issuer=issuer,
            encrypted_private_key=ciphertext,
            nonce=nonce,
            aad=aad or b"",
            state="active",
        )

    # ------------------------------------------------------------------
    # Public blob helpers (arbitrary ciphertext under a DEK — no model row)
    # ------------------------------------------------------------------

    def encrypt_blob(self, wrapped_key, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
        """Encrypt arbitrary bytes under a WrappedDataKey's DEK.

        Returns ``(ciphertext_with_tag, nonce)``. Unlike :meth:`encrypt_secret` this
        creates no database row — the caller stores the ciphertext wherever it likes
        (e.g. a ``VaultFile`` row). ``aad`` should bind the ciphertext to its
        context so a row cannot be replayed under a different identity.
        """
        if not isinstance(plaintext, bytes):
            raise TypeError("Plaintext must be bytes.")

        dek = self._unwrap_dek(wrapped_key)
        return aes_gcm_encrypt(dek, plaintext, aad or b"")

    def decrypt_blob(self, wrapped_key, ciphertext, nonce, aad: bytes = b"") -> bytes:
        """Decrypt bytes produced by :meth:`encrypt_blob` under the same WrappedDataKey.

        Raises ``InvalidTag`` if the ciphertext, nonce, or AAD do not match.
        """
        dek = self._unwrap_dek(wrapped_key)
        return aes_gcm_decrypt(dek, bytes(ciphertext), bytes(nonce), aad or b"")

    def create_data_key(self) -> "WrappedDataKey":
        """Create and persist a fresh DEK under this strongbox's active VMK.

        Returns the new ``WrappedDataKey`` with its raw DEK warmed into the session
        cache, so an immediate ``encrypt_blob`` does not re-unwrap. Used when a new
        namespace (e.g. a vault folder) needs its own data key.
        """
        from toto.gervazy.models import VaultMasterKey, WrappedDataKey

        vmk = (
            VaultMasterKey.objects.filter(strongbox=self._strongbox, state="active")
            .order_by("-version")
            .first()
        )
        if not vmk:
            raise RuntimeError("No active VMK for this strongbox.")

        raw_vmk = self._unwrap_vmk(vmk)
        raw_dek = generate_raw_key()
        encrypted_dek, nonce = aes_gcm_encrypt(raw_vmk, raw_dek)

        last = (
            WrappedDataKey.objects.filter(strongbox=self._strongbox)
            .order_by("-version")
            .first()
        )
        version = (last.version + 1) if last else 1

        wrapped_key = WrappedDataKey.objects.create(
            strongbox=self._strongbox,
            vmk=vmk,
            encrypted_dek=encrypted_dek,
            nonce=nonce,
            version=version,
            state="active",
        )
        self._dek_cache[wrapped_key.pk] = raw_dek
        return wrapped_key

    # ------------------------------------------------------------------
    # Strongbox initialization
    # ------------------------------------------------------------------

    @classmethod
    def initialize_strongbox(
        cls,
        owner,
        strongbox_name: str,
        password: str,
    ) -> tuple["GervazyCryptoSession", "WrappedDataKey"]:
        """Create a new UserStrongbox with one VMK and one initial DEK."""
        from toto.gervazy.models import UserStrongbox, VaultMasterKey, WrappedDataKey

        if not password:
            raise ValueError("Strongbox password is required.")

        strongbox = UserStrongbox.objects.create(owner=owner, name=strongbox_name)
        raw_ukek = decode_derived_key(strongbox.derive_key(password))

        # Generate and wrap VMK.
        raw_vmk = generate_raw_key()
        encrypted_vmk, vmk_nonce = aes_gcm_encrypt(raw_ukek, raw_vmk)

        vmk = VaultMasterKey.objects.create(
            strongbox=strongbox,
            encrypted_vmk=encrypted_vmk,
            nonce=vmk_nonce,
            version=1,
            state="active",
        )

        # Generate and wrap DEK.
        raw_dek = generate_raw_key()
        encrypted_dek, dek_nonce = aes_gcm_encrypt(raw_vmk, raw_dek)

        wrapped_key = WrappedDataKey.objects.create(
            strongbox=strongbox,
            vmk=vmk,
            encrypted_dek=encrypted_dek,
            nonce=dek_nonce,
            version=1,
            state="active",
        )

        # Build unlocked session with warm caches.
        session = cls.__new__(cls)
        session._strongbox = strongbox
        session._vault = strongbox  # Backward-compatible attribute during transition.
        session._ukek = raw_ukek
        session._vmk_cache = {vmk.pk: raw_vmk}
        session._dek_cache = {wrapped_key.pk: raw_dek}
        return session, wrapped_key

    # Backward-compatible alias during transition.
    initialize_vault = initialize_strongbox

    # ------------------------------------------------------------------
    # Passphrase rotation
    # ------------------------------------------------------------------

    @classmethod
    def rewrap_master_keys(cls, strongbox, old_password: str, new_password: str) -> None:
        """Re-key a strongbox to a new passphrase without touching the data below the VMK.

        The passphrase derives the UKEK, which wraps the VMK(s); the VMK wraps the DEKs,
        which wrap the secrets/keys/files. So changing the passphrase only requires
        re-deriving the UKEK under a fresh salt and re-wrapping the VMK(s): every DEK,
        secret, private key and file is left byte-for-byte unchanged, and nothing below
        the VMK is decrypted.

        Verify-first and atomic. The old passphrase is proven by unwrapping the VMK(s)
        *before* anything is written, so a wrong one raises ``ValueError`` and changes
        nothing. The new salt and the re-wrapped VMK(s) are then committed together in
        one transaction — a salt persisted without a matching VMK would brick the
        strongbox.
        """
        from django.db import transaction as _txn

        from toto.gervazy.models import VaultMasterKey

        if not old_password or not new_password:
            raise ValueError("Both the current and the new passphrase are required.")

        # A destroyed VMK may have had its ciphertext cleared; every other VMK must be
        # re-wrapped or it becomes permanently unreadable under the new passphrase.
        vmks = list(
            VaultMasterKey.objects.filter(strongbox=strongbox).exclude(state="destroyed")
        )
        if not vmks:
            raise RuntimeError("Strongbox has no master key to re-wrap.")

        old_ukek = decode_derived_key(strongbox.derive_key(old_password))

        # Unwrap with the OLD key first — a wrong current passphrase is an InvalidTag
        # here, before any write happens.
        raw_vmks: dict[object, bytes] = {}
        for vmk in vmks:
            try:
                raw = aes_gcm_decrypt(old_ukek, bytes(vmk.encrypted_vmk), bytes(vmk.nonce))
            except Exception as exc:
                raise ValueError("The current passphrase is incorrect.") from exc
            if len(raw) != AES_GCM_KEY_SIZE:
                raise RuntimeError("Unwrapped VMK is not a valid AES-256 key.")
            raw_vmks[vmk.pk] = raw

        # New salt → new UKEK. derive_key reads strongbox.salt, so set it first.
        strongbox.salt = os.urandom(16)
        new_ukek = decode_derived_key(strongbox.derive_key(new_password))
        rewrapped = {
            vmk.pk: aes_gcm_encrypt(new_ukek, raw_vmks[vmk.pk]) for vmk in vmks
        }

        # jess-system has exactly one VMK; the loop is correct for any count because the
        # (strongbox, nonce) uniqueness is over fresh random 12-byte nonces.
        with _txn.atomic():
            strongbox.save()
            for vmk in vmks:
                ciphertext, nonce = rewrapped[vmk.pk]
                vmk.encrypted_vmk = ciphertext
                vmk.nonce = nonce
                vmk.save(update_fields=["encrypted_vmk", "nonce"])

    # ------------------------------------------------------------------
    # File decryption
    # ------------------------------------------------------------------

    def decrypt_filename(self, encrypted_file) -> str:
        """Decrypt and return the original filename of an EncryptedFile."""
        dek = self._unwrap_dek(encrypted_file.wrapped_key)
        return aes_gcm_decrypt(
            dek,
            bytes(encrypted_file.original_name_encrypted),
            bytes(encrypted_file.original_name_nonce),
        ).decode("utf-8", errors="replace")

    def decrypt_file(self, encrypted_file) -> tuple[bytes, str]:
        """
        Decrypt all chunks of an EncryptedFile and return (plaintext_bytes, original_filename).

        Chunks are stored concatenated in encrypted_file.file; each chunk's nonce
        and ciphertext_size are in the related EncryptedFileChunk rows.
        """
        dek = self._unwrap_dek(encrypted_file.wrapped_key)
        chunks = list(encrypted_file.chunks.order_by("index"))
        plaintext_parts = []

        with encrypted_file.file.open("rb") as fh:
            for chunk in chunks:
                ciphertext = fh.read(chunk.ciphertext_size)
                plaintext_parts.append(
                    aes_gcm_decrypt(dek, ciphertext, bytes(chunk.nonce))
                )

        filename = self.decrypt_filename(encrypted_file)
        return b"".join(plaintext_parts), filename

    # ------------------------------------------------------------------
    # Cleanup
    # ------------------------------------------------------------------

    def close(self) -> None:
        """
        Best-effort cleanup.

        Python cannot guarantee memory zeroization for immutable bytes, but
        clearing references is still better than keeping secrets around.
        """
        self._ukek = b""
        self._vmk_cache.clear()
        self._dek_cache.clear()
