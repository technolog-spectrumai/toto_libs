"""
Gervazy crypto layer.

Envelope hierarchy (bottom up):
  password  →  Argon2id UKEK  →  decrypt VMK  →  decrypt DEK  →  decrypt data

All AES keys are raw 32 bytes.  derive_key() returns base64url(32 bytes);
we decode that back to raw bytes before using it as an AES-256-GCM key.
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


# ---------------------------------------------------------------------------
# Low-level AES-256-GCM helpers
# ---------------------------------------------------------------------------

def aes_gcm_encrypt(key: bytes, plaintext: bytes, aad: bytes = b"") -> tuple[bytes, bytes]:
    """Encrypt *plaintext* and return ``(ciphertext_with_tag, nonce)``."""
    nonce = os.urandom(12)
    ciphertext = AESGCM(key).encrypt(nonce, plaintext, aad or None)
    return ciphertext, nonce


def aes_gcm_decrypt(key: bytes, ciphertext: bytes, nonce: bytes, aad: bytes = b"") -> bytes:
    """Decrypt *ciphertext*. Raises ``cryptography.exceptions.InvalidTag`` on failure."""
    return AESGCM(key).decrypt(nonce, ciphertext, aad or None)


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
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, "Toto Local Development"),
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


def ensure_self_signed_certificate(
    cert_path,
    key_path,
    *,
    common_name: str = "localhost",
    dns_names: list[str] | None = None,
    ip_addresses: list[str] | None = None,
) -> bool:
    """Create a self-signed certificate unless both cert and key already exist."""
    cert_path = os.fspath(cert_path)
    key_path = os.fspath(key_path)

    if os.path.exists(cert_path) and os.path.exists(key_path):
        return False

    cert_pem, key_pem = generate_self_signed_certificate(
        common_name=common_name,
        dns_names=dns_names,
        ip_addresses=ip_addresses,
    )
    os.makedirs(os.path.dirname(cert_path), exist_ok=True)

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
    In-memory session for an unlocked UserVault.

    Decrypted key material (UKEK, VMK, DEK) is cached in instance dicts and
    never written to the database.  Create a fresh session per request or
    operation; do not share across threads.
    """

    def __init__(self, vault, password: str):
        from toto.gervazy.models import UserVault  # noqa: F401 – import guard
        self._vault = vault
        # derive_key returns base64url-encoded bytes; decode to raw 32-byte key
        self._ukek: bytes = base64.urlsafe_b64decode(vault.derive_key(password))
        self._vmk_cache: dict[int, bytes] = {}   # vmk.version → raw VMK bytes
        self._dek_cache: dict[int, bytes] = {}   # wrapped_key.pk → raw DEK bytes

    # ------------------------------------------------------------------
    # Internal key unwrapping
    # ------------------------------------------------------------------

    def _unwrap_vmk(self, vmk) -> bytes:
        if vmk.version not in self._vmk_cache:
            raw = aes_gcm_decrypt(
                self._ukek,
                bytes(vmk.encrypted_vmk),
                bytes(vmk.nonce),
            )
            self._vmk_cache[vmk.version] = raw
        return self._vmk_cache[vmk.version]

    def _unwrap_dek(self, wrapped_key) -> bytes:
        pk = wrapped_key.pk
        if pk not in self._dek_cache:
            vmk_record = self._vault.master_keys.filter(
                version=wrapped_key.vmk_version
            ).first()
            if vmk_record is None:
                raise RuntimeError(
                    f"VMK v{wrapped_key.vmk_version} not found in vault {self._vault.name!r}."
                )
            raw_vmk = self._unwrap_vmk(vmk_record)
            raw_dek = aes_gcm_decrypt(
                raw_vmk,
                bytes(wrapped_key.encrypted_dek),
                bytes(wrapped_key.nonce),
            )
            self._dek_cache[pk] = raw_dek
        return self._dek_cache[pk]

    # ------------------------------------------------------------------
    # Public decrypt methods
    # ------------------------------------------------------------------

    def decrypt_secret(self, secret) -> str:
        """Decrypt an EncryptedSecret and return the plaintext string."""
        dek = self._unwrap_dek(secret.wrapped_key)
        plaintext = aes_gcm_decrypt(
            dek,
            bytes(secret.ciphertext),
            bytes(secret.nonce),
            bytes(secret.aad),
        )
        return plaintext.decode()

    def decrypt_private_key(self, epk) -> str:
        """Decrypt an EncryptedPrivateKey and return the PEM string."""
        dek = self._unwrap_dek(epk.wrapped_key)
        plaintext = aes_gcm_decrypt(
            dek,
            bytes(epk.encrypted_private_key),
            bytes(epk.nonce),
            bytes(epk.aad),
        )
        return plaintext.decode()

    # ------------------------------------------------------------------
    # Public encrypt helpers
    # ------------------------------------------------------------------

    def encrypt_secret(self, wrapped_key, plaintext: str, *, name: str, purpose: str = "") -> "EncryptedSecret":
        """Encrypt a string and save it as an EncryptedSecret."""
        from toto.gervazy.models import EncryptedSecret
        dek = self._unwrap_dek(wrapped_key)
        ciphertext, nonce = aes_gcm_encrypt(dek, plaintext.encode())
        return EncryptedSecret.objects.create(
            vault=self._vault,
            wrapped_key=wrapped_key,
            name=name,
            purpose=purpose,
            ciphertext=ciphertext,
            nonce=nonce,
            state="active",
        )

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
        dek = self._unwrap_dek(wrapped_key)
        ciphertext, nonce = aes_gcm_encrypt(dek, private_key_pem.encode(), aad)
        return EncryptedPrivateKey.objects.create(
            vault=self._vault,
            wrapped_key=wrapped_key,
            key_id=key_id,
            key_type=key_type,
            public_key_pem=public_key_pem,
            issuer=issuer,
            encrypted_private_key=ciphertext,
            nonce=nonce,
            aad=aad,
            state="active",
        )

    # ------------------------------------------------------------------
    # Vault initialisation (classmethod factory)
    # ------------------------------------------------------------------

    @classmethod
    def initialize_vault(cls, owner, vault_name: str, password: str) -> tuple["GervazyCryptoSession", "WrappedDataKey"]:
        """
        Create a new UserVault with a VMK and an initial DEK.

        Returns ``(session, wrapped_key)`` where *session* is already unlocked
        and *wrapped_key* is the first active WrappedDataKey for that vault.
        """
        from toto.gervazy.models import UserVault, VaultMasterKey, WrappedDataKey

        vault = UserVault.objects.create(owner=owner, name=vault_name)
        raw_ukek: bytes = base64.urlsafe_b64decode(vault.derive_key(password))

        # Generate and wrap VMK
        raw_vmk = os.urandom(32)
        encrypted_vmk, vmk_nonce = aes_gcm_encrypt(raw_ukek, raw_vmk)
        vmk = VaultMasterKey.objects.create(
            vault=vault,
            encrypted_vmk=encrypted_vmk,
            nonce=vmk_nonce,
            version=1,
            state="active",
        )

        # Generate and wrap DEK
        raw_dek = os.urandom(32)
        encrypted_dek, dek_nonce = aes_gcm_encrypt(raw_vmk, raw_dek)
        wrapped_key = WrappedDataKey.objects.create(
            vault=vault,
            vmk_version=vmk.version,
            encrypted_dek=encrypted_dek,
            nonce=dek_nonce,
            version=1,
            state="active",
        )

        # Build the session with warm caches so callers can use it immediately
        session = cls.__new__(cls)
        session._vault = vault
        session._ukek = raw_ukek
        session._vmk_cache = {vmk.version: raw_vmk}
        session._dek_cache = {wrapped_key.pk: raw_dek}
        return session, wrapped_key
