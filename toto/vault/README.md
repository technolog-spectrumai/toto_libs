# toto.vault

Encrypted file storage: buckets, directories, files, gateways, and storage backends.

## Concepts

- **Bucket** — named storage namespace with an optional quota (MB). Backed by local disk, S3-compatible, or remote Toto server.
- **VaultDirectory** — hierarchical folders within a bucket. Supports per-user access restrictions.
- **VaultFile** — uploaded file with type detection (image, PDF, text, SVG, video, audio…), content hash, and optional public access.
- **FileGateway** — upload endpoint linked to a directory. Controls allowed users, max file size, and whether uploads are made public.
- **StorageProvider** — named S3-compatible preset (AWS, OVH, MinIO…). Seeded by `ingress_storage_providers`.

## Storage backends

| Backend | `storage_backend` value | Notes |
|---|---|---|
| Local disk | `local` | Default. Files under `MEDIA_ROOT`. |
| S3-compatible | `s3` | Requires `storage_config` with `bucket_name`, `region_name`, and env-var credentials. |
| Remote Toto | `remote_toto` | Proxies to another Toto instance via its vault API. |

## Key coupling

- `library.LibraryItem.vault_file` — books, articles, audio, video reference their source file here.
- `ocr.OcrImage.vault_file` — OCR images are vault files.
- `texlab.LatexFile.vault_file` — LaTeX source files live in vault.
- `gervazy.EncryptedFile` is a separate encrypted-at-rest store; `vault` is for unencrypted / user-accessible files.
- `invoice.Invoice.bucket` — invoices can be linked to a bucket.

## Billing — tariffs optional

Vault charges for storage via `toto.metering.charge`. When `toto.tariffs` is **not** installed (`BUILD_ECONOMY=0`) all uploads proceed normally, uncharged.

When tariffs is installed, upload views use:

```python
from toto.metering.charge import get_tariff_for_user, check_user_can_act, charge_user

tariff = get_tariff_for_user(user, "vault")
check_user_can_act(user, tariff, "storage.request", 1)       # pre-flight balance check
charge_user(user, tariff, "storage.request", 1, ...)         # drain balance after upload
```

Charge failure is non-fatal — metering still records the event.

## Metering events

| Metric | Unit | When |
|---|---|---|
| `storage.request` | request | Every file upload |
| `storage.transfer_mb` | MB | Every file upload (actual bytes transferred) |

## Ingress

`python manage.py ingress_vault` seeds demo buckets, directories, and files. Tariff and invoice seeding is skipped when `toto.tariffs` / `toto.invoice` are not installed.
