# toto.vault

User file storage. Organizes user-uploaded files into named buckets with optional access control via gateways.

## Models

- `Bucket` — a named file container. Fields: `owner` (FK to `auth.User`), `name`, `is_public`, `created_at`.
- `VaultFile` — a file stored in a bucket. Fields: `owner` FK, `bucket` FK (nullable), `original_filename`, `content_type`, `size`, `file` (Django `FileField`), `checksum`, `uploaded_at`, `is_deleted`, `deleted_at`. Soft-delete only.
- `FileGateway` — sharing gate on a bucket. Fields: `bucket` (OneToOne), `token` (UUID slug for URL access), `allowed_users` (M2M to `auth.User`), `is_public`, `expires_at`.

## Key coupling

- `library.LibraryItem.vault_file` — books, articles, audio, video reference their source file here.
- `ocr.OcrImage.vault_file` — OCR images are vault files.
- `texlab.LatexFile.vault_file` — LaTeX source files live in vault.
- `gervazy.EncryptedFile` is a separate encrypted-at-rest store; `vault` is for unencrypted / user-accessible files.

## Dependencies

None — standalone app.
