# Gervazy Strongbox Drop-in Replacement Patch

This zip contains replacement files for your `toto.gervazy` app:

- `gervazy/models.py`
- `gervazy/crypto.py`
- `gervazy/admin.py`

## What changed

- `UserVault` is renamed to `UserStrongbox`.
- Model fields named `vault` are renamed to `strongbox`.
- `WrappedDataKey.vmk_version` is replaced with a real FK: `WrappedDataKey.vmk`.
- AES-GCM nonce fields use 12-byte random nonces.
- Nonce uniqueness constraints are added.
- Decryption now enforces key/secret state checks.
- `EncryptedSecret` now uses stable AAD built from UUID + strongbox ID + version.
- Admin no longer displays raw ciphertext, nonce, salt, or encrypted key bytes.

## Install

Copy the files into your Django app, likely:

```bash
cp gervazy/models.py path/to/toto/gervazy/models.py
cp gervazy/crypto.py path/to/toto/gervazy/crypto.py
cp gervazy/admin.py path/to/toto/gervazy/admin.py
```

## Migrations

Run:

```bash
python manage.py makemigrations gervazy
python manage.py migrate
```

Django may ask whether fields/models were renamed. If you are migrating an existing DB, answer yes for:

- `UserVault` -> `UserStrongbox`
- `vault` -> `strongbox`

The `WrappedDataKey.vmk_version` -> `WrappedDataKey.vmk` change may require a manual data migration if you already have production data.

## Manual migration hint for existing data

If existing rows have `vmk_version`, add `vmk` in a temporary migration, populate it by matching:

```python
vmk = VaultMasterKey.objects.get(
    strongbox=wrapped_key.strongbox,
    version=wrapped_key.vmk_version,
)
wrapped_key.vmk = vmk
wrapped_key.save(update_fields=["vmk"])
```

Then remove `vmk_version` in a later migration.

## Compatibility notes

`GervazyCryptoSession.initialize_vault` is kept as an alias to `initialize_strongbox` during transition.

Code that imports `UserVault` must be updated to import `UserStrongbox`.

Code that queries `.vault` must be updated to `.strongbox`.
