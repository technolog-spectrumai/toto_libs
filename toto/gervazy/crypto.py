"""
Gervazy crypto layer.

Envelope hierarchy (bottom up):
  password  →  Argon2id UKEK  →  decrypt VMK  →  decrypt DEK  →  decrypt data

All AES keys are raw 32 bytes.  derive_key() returns base64url(32 bytes);
we decode that back to raw bytes before using it as an AES-256-GCM key.
"""
import base64
import os

from cryptography.hazmat.primitives.ciphers.aead import AESGCM


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
