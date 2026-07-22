# toto.gervazy

Encryption-at-rest vault and document signing service. Implements a three-tier AES-256-GCM key hierarchy (password → Argon2id KDF → UKEK → wrapped VMK → wrapped DEK → encrypted objects) and an Ed25519-based cryptographic signing layer for contracts and documents.

## Purpose

When a user sets their vault password, Argon2id derives a UKEK from it (never stored). The UKEK encrypts a `VaultMasterKey` blob. The VMK in turn wraps `WrappedDataKey` records (one per namespace). Data keys encrypt the actual secrets, files, and private keys. Decryption requires the user's password at runtime — the system cannot read stored secrets without it.

The signing layer (`signing.py`) stores each person's Ed25519 private key encrypted inside their strongbox. To sign a document, the caller opens a `GervazyCryptoSession` with the strongbox password, which decrypts the private key in memory, produces a signature, then discards the key. The public key is stored in plaintext and can verify signatures at any time without a password.

The `CryptoAuditLog` records every operation for compliance. The OIDC signing key (`sso_master`) and all outbound API credentials (`api`) live in gervazy.

## Key hierarchy

```
User password (never stored)
    │ Argon2id KDF
    ▼
UKEK (User Key Encryption Key — derived, never stored)
    │ AES-256-GCM unwrap
    ▼
VaultMasterKey (VMK — stored as encrypted blob in UserStrongbox)
    │ AES-256-GCM unwrap
    ▼
WrappedDataKey (DEK — one per namespace, stored encrypted)
    │ AES-256-GCM decrypt
    ▼
EncryptedSecret / EncryptedFile / EncryptedPrivateKey (stored as encrypted blobs)
```

## Models

- `UserStrongbox` (`UserVault`) — per-user container. Links `auth.User` to Argon2id KDF parameters and one or more `VaultMasterKey` records.
- `VaultMasterKey` — the VMK blob encrypted by the UKEK. Fields: `encrypted_vmk`, `nonce`, `state`, `version`.
- `WrappedDataKey` — namespace-scoped DEK wrapped under a VMK. Fields: `encrypted_dek`, `nonce`, `vmk` FK, `state`, `version`.
- `EncryptedSecret` — arbitrary key/value secret (API tokens, SMTP passwords, OAuth secrets). Encrypted by a DEK; includes `aad` for binding to context.
- `EncryptedFile` — metadata for a chunked AES-256-GCM encrypted file. Chunks stored in `EncryptedFileChunk`.
- `EncryptedFileChunk` — one sequential encrypted chunk of an `EncryptedFile`.
- `EncryptedPrivateKey` — RSA or Ed25519 private key stored encrypted by a DEK. `public_key_pem` stored in plaintext. Supported types: `Ed25519`, `RSA-2048`, `RSA-4096`.
- `PersonSigningKey` — links a `people.Person` to their active `EncryptedPrivateKey` used for document signing. Only one `is_active=True` record per person at a time; old keys are retired (not deleted) so existing signatures remain verifiable.
- `CryptoAuditLog` — append-only log of vault operations. Never stores plaintext or key material.

## Signing service (`signing.py`)

`SigningService` is a stateless helper class:

| Method | Description |
|---|---|
| `get_active_signing_key(person)` | Returns the active `PersonSigningKey` or `None` |
| `provision_signing_key(session, wrapped_key, person)` | Generates Ed25519 key pair, encrypts private key, retires old key |
| `sign_document(session, person, payload, *, wrapped_key=None)` | Signs `payload` bytes; provisions key first if needed. Returns `DocumentSignature` |
| `verify(person, payload, signature_b64)` | Verifies a base64 signature against all known public keys for the person. No password required |
| `canonical_contract_payload(contract, person, signed_at)` | Builds the deterministic UTF-8 payload used for contract signing |

### DocumentSignature dataclass

```python
@dataclass(frozen=True)
class DocumentSignature:
    payload: bytes          # canonical message that was signed
    signature_b64: str      # base64-encoded Ed25519 signature
    signing_key_id: str     # key_id of the EncryptedPrivateKey used
    public_key_pem: str     # public key for offline verification
```

### Canonical contract payload format

```
sign:contract
uuid:<contract.uuid>
name:<contract.name>
person:<person.pk>
at:<signed_at.isoformat()>
```

## Key coupling

- `sso_master.SSOSigningKey.encrypted_key` → `gervazy.EncryptedPrivateKey` — OIDC signing key.
- `backup.BackupProfile.signing_key` → `gervazy.EncryptedPrivateKey` — backup signing key.
- `api.ApiConnector.api_secret`, `api.EmailService.smtp_secret` → `gervazy.EncryptedSecret` — outbound API credentials.
- `contracts.ContractSignatory.signing_key` → `gervazy.EncryptedPrivateKey` — the key that produced the contract's cryptographic signature.
- `people.Person` ← `gervazy.PersonSigningKey` — each person's active Ed25519 signing identity.

## Dependencies

- `people` (for `PersonSigningKey.person` FK)
- Otherwise standalone.
