# toto.sso_core

Shared SSO manifest and connection bundle schemas. No models — pure Python dataclasses and utilities for inter-app SSO provisioning.

## What it contains

- `manifest.ManifestBundle` — a dataclass exported by an SSO consumer app (e.g. `regis`). Declares what OIDC client it needs: `client_id`, `client_type`, `redirect_uris`, `scopes`, `trusted`.
- `manifest.ConnectionBundle` — a dataclass issued by the SSO provider (`sso_master`) to a consumer. Contains the full OIDC endpoint URLs, client credentials, and signing key public cert.
- `manifest.OIDCClientSpec` — nested spec inside `ManifestBundle`.

Both bundles serialize to/from JSON and have no Django dependency.

## Purpose

Enables the "import connection bundle" workflow: a consumer app exports a `ManifestBundle` JSON → operator imports it into `sso_master` admin → provider provisions an `SSORelyingParty` record and exports a `ConnectionBundle` → operator imports it into the consumer app (`sso_client`).

## Key coupling

- `sso_master` — reads `ManifestBundle` to provision `SSORelyingParty`.
- `sso_client.OIDCProviderConfig` — stores the imported `ConnectionBundle`.

## Dependencies

None — standalone app.
