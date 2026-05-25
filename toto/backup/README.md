# toto.backup

Platform backup signing and storage. Signs outbound backup packages with an RSA private key from gervazy and stores received backup archives.

## Purpose

The platform operator sets up a `BackupProfile` with a gervazy `EncryptedPrivateKey` as the signing key. Outbound backup archives are cryptographically signed before transmission; incoming archives are verified against the stored public key. `StoredBackup` records track each archive with a checksum and verification flag. This ensures backup integrity and authenticity across deployment boundaries.

## Models

- `BackupProfile` — signing key configuration for a platform. Fields: `platform` (OneToOne FK to `core.Platform`), `signing_key` (FK to `gervazy.EncryptedPrivateKey`, nullable), `verify_key` (public key PEM text for verifying incoming backups).

- `StoredBackup` — an archived backup file. Fields: `uid` (UUID), `platform` FK, `created_at`, `file` (Django `FileField` — stored at `stored-backups/{uid}/{filename}`), `file_size`, `checksum`, `is_verified` (bool — signature check passed), `notes`.

## Key coupling

- `core.Platform` — one backup profile per platform.
- `gervazy.EncryptedPrivateKey` — signing key decrypted at export time.

## Dependencies

- `core` — BackupProfile is OneToOne with core.Platform
- `gervazy` — EncryptedPrivateKey as archive signing key
