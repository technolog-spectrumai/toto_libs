# Changelog

## Strongbox patch

- Renamed user-facing vault concept to Strongbox.
- Added `UserStrongbox` model.
- Renamed model relations from `vault` to `strongbox`.
- Replaced DEK-to-VMK version lookup with `WrappedDataKey.vmk` FK.
- Added AES-GCM key/nonce validation in crypto helpers.
- Added state checks before decrypting VMKs, DEKs, secrets, and private keys.
- Added stable AAD for `EncryptedSecret`.
- Hardened admin views to avoid raw cryptographic material display.
