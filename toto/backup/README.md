# toto.backup

Platform backup signing and storage. Signs outbound backup packages with an RSA private key from gervazy and stores received backup archives.

## Models

- `BackupProfile` — signing key configuration for a platform. Fields: `platform` (OneToOne FK to `core.Platform`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`, nullable), `verify_key` (public key PEM text for verifying incoming backups).

- `StoredBackup` — an archived backup file. Fields: `uid` (UUID), `platform` FK, `created_at`, `file` (Django `FileField` — stored at `stored-backups/{uid}/{filename}`), `file_size`, `checksum`, `is_verified` (bool — signature check passed), `notes`.

## Key coupling

- `core.Platform` — one backup profile per platform.
- `gervazy.EncryptedPrivateKey` — signing key decrypted at export time.
