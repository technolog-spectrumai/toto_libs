# toto.sso_master

Full OpenID Connect (OIDC) provider. Issues ID tokens, access tokens, and JWKS. Signs tokens with RSA keys stored encrypted in `gervazy`.

## Purpose

toto acts as its own identity provider. External apps (e.g. `regis`) and internal services register as OIDC clients. When a user logs into a client app, they are redirected to toto's `/sso/authorize/` endpoint, authenticate, and receive a JWT ID token signed with the active `SSOSigningKey`. The private signing key never leaves gervazy — `services.get_signing_private_key_pem()` unlocks it at token-issue time using `SSO_VAULT_PASSWORD`. The JWKS endpoint (`/sso/jwks/`) lets relying parties verify token signatures.

## Models

- `SSOClient` — a registered OIDC client application. Fields: `client_id` (unique), `client_secret` (hashed), `client_type` (`confidential / public`), `name`, `redirect_uris` (text, one per line), `scopes` (space-separated), `is_active`, `is_trusted` (trusted clients skip the consent screen), `metadata`.

- `SSOSubject` — links a Django `auth.User` to an OIDC `sub` claim (stable UUID per user per client). Fields: `user` FK, `client` FK, `sub` (UUID). Unique on `(user, client)`.

- `SSOAuthorizationCode` — a short-lived authorization code issued during the authorization code flow. Fields: `client`, `user`, `code` (unique), `redirect_uri`, `scope`, `nonce`, `code_challenge` / `code_challenge_method` (PKCE), `expires_at`, `is_used`.

- `SSOSigningKey` — an RSA key pair used to sign JWTs. Fields: `key_id` (UUID `kid` claim), `algorithm` (default `RS256`), `public_key_pem` (plaintext), `encrypted_key` (FK to `gervazy.EncryptedPrivateKey`), `is_active`, `created_at`. Only one key is active at a time.

- `SSOAccessToken` — an issued access token (stored for introspection). Fields: `client`, `user`, `token` (hashed), `scope`, `expires_at`, `is_revoked`, `issued_at`.

- `SSORelyingParty` — extends `SSOClient`. An explicitly provisioned relying party (e.g. a `regis` deployment). Adds: `display_name`, `description`, `connection_bundle_hash` (SHA-256 of the imported connection bundle).

## Services (`services.py`)

| Function | What it does |
|---|---|
| `get_active_platform()` | Reads `core.Platform` singleton for issuer domain |
| `get_issuer(request)` | Returns the OIDC issuer URL (`https://{domain}`) |
| `get_active_signing_key()` | Returns the active `SSOSigningKey` |
| `get_signing_private_key_pem()` | Unlocks the gervazy vault and returns the RSA PEM |
| `get_jwks()` | Builds the JWKS endpoint response dict |
| `build_id_token(request, ...)` | Signs and returns a JWT ID token |
| `get_user_claims(user, scopes)` | Returns claims dict for the requested scopes |
| `verify_pkce(verifier, challenge, method)` | Validates PKCE code verifier against challenge |

## Key coupling

- `gervazy.EncryptedPrivateKey` — signing key decrypted at token-issue time using `SSO_VAULT_PASSWORD`.
- `core.Platform` — issuer URL is derived from platform domain.
- `sso_core.manifest.ConnectionBundle` — used to provision `SSORelyingParty` records for connected apps.
