# toto.gervazy

Encryption-at-rest vault. Implements a three-tier AES-256-GCM key hierarchy: password → Argon2id KDF → UKEK → wrapped VMK → wrapped DEK → encrypted objects. Nothing sensitive is stored in plaintext.

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

- `UserStrongbox` — per-user container. Links `auth.User` to Argon2id salt and one or more `VaultMasterKey` records. Has a `is_locked` flag.
- `VaultMasterKey` — the VMK blob for a strongbox. Fields: `encrypted_vmk` (hex), `kdf_salt`, `kdf_params` (JSON — memory, time, parallelism), `is_active`.
- `WrappedDataKey` — a namespace-scoped DEK wrapped under a VMK. Fields: `namespace` (slug, e.g. `"sso_signing"`), `encrypted_dek` (hex), `vault_master_key` FK.
- `EncryptedSecret` — an arbitrary key/value secret stored encrypted. Fields: `strongbox` FK, `wrapped_data_key` FK, `name`, `namespace`, `encrypted_value` (hex), `iv` (hex).
- `EncryptedFile` — an encrypted file object. Fields: `strongbox`, `wrapped_data_key`, `original_filename`, `encrypted_content` (stored in chunks via `EncryptedFileChunk`), `content_type`, `size_bytes`.
- `EncryptedFileChunk` — a sequential chunk of an `EncryptedFile`. Fields: `file` FK, `chunk_index`, `encrypted_data` (hex).
- `EncryptedPrivateKey` — an RSA/EC private key stored encrypted. Fields: `strongbox`, `wrapped_data_key`, `algorithm`, `key_size`, `encrypted_pem` (hex), `public_key_pem` (plaintext, safe to store).
- `CryptoAuditLog` — append-only log of vault operations (`unlock`, `wrap`, `unwrap`, `encrypt`, `decrypt`, `key_rotation`). Fields: `user` FK, `operation`, `namespace`, `target_id`, `ip_address`, `success`, `error_message`.

## Key coupling

- `sso_master.SSOSigningKey.encrypted_key` → `gervazy.EncryptedPrivateKey` — the OIDC signing key lives here.
- `backup.BackupProfile.signing_key` → `gervazy.EncryptedPrivateKey` — backup signing key.
- `api.ApiConnector.api_secret`, `api.EmailService.smtp_secret` → `gervazy.EncryptedSecret` — outbound API credentials.
- The vault is unlocked per-request using `SSO_VAULT_PASSWORD` (sso_master) or interactive password input for user vaults.
