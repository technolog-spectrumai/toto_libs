# toto.sso_client

OIDC consumer configuration. Stores the provider endpoint and client credentials imported from an `sso_master` connection bundle. Used by apps that authenticate users against an external toto SSO provider.

## Models

- `OIDCProviderConfig` — single-row config table (only one record should be active). Fields:
  - `label` — human name for the provider (default `"Portal"`)
  - `portal_url` — base URL of the SSO provider
  - `client_id`, `client_secret` — OIDC credentials (secret stored in plaintext here; consider moving to gervazy in production)
  - `scopes` — requested scopes (default `"openid email profile"`)
  - `app_name`, `trusted` — manifest export fields (what this app declared itself as)
  - `redirect_uris` — newline-separated allowed redirect URIs
  - `active`, `imported_at`
  - `redirect_uris_list()` — property returning a cleaned list

## Key coupling

- `sso_core.manifest.ConnectionBundle` — populated via admin import of a connection bundle JSON.
- `sso_master.SSORelyingParty` — the provider-side counterpart record.

## Dependencies

- `sso_core` — OIDCProviderConfig stores a ConnectionBundle imported from sso_master via sso_core dataclasses
