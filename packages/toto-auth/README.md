# toto-auth

`toto-auth` is the authentication distribution of the **toto** suite: it owns every way a person signs in to a toto host. It ships the three SSO apps that previously lived in `toto-base` — `toto.sso_core`, `toto.sso_master`, `toto.sso_client` — under the shared `toto.*` PEP 420 namespace, unchanged in import path, app label, url names and migrations. It is one of the lockstep-versioned wheels of the suite and depends on `toto-base`.

## What it does (functional)

A toto host picks one authentication posture:

- **Provider** (the historical default) — the host is its own identity authority. Users sign in with username/password on the host, and the host doubles as an OIDC 1.0 provider that other services (Grafana, Gitea, sibling platforms) can federate against.
- **Consumer** — the host delegates sign-in to another toto platform: the login link forwards to the provider's authorize endpoint and the callback provisions/links the local user.
- **Local** — plain username/password sessions with no OIDC surface at all.

## The apps

### sso_core

Shared, Django-free dataclass schemas for exchanging OIDC configuration between platforms: `OIDCClientSpec` and `ManifestBundle` (consumer → provider: "what I need"), `ConnectionBundle` (provider → consumer: "your issued credentials"). No models, no views.

### sso_master — OIDC provider

- Models: `SSOClient`/`SSORelyingParty` (hashed client secret, redirect-uri allowlist, scopes, trusted flag), `SSOSubject` (stable UUID `sub` per user), `SSOAuthorizationCode` (5-minute single-use, PKCE), `SSOSigningKey` (RS256; private half encrypted via `toto.gervazy`), `SSOAccessToken` (opaque bearer, 1 h).
- Views: interactive login/logout, the full OIDC surface (`/.well-known/openid-configuration`, `jwks`, `authorize`, `consent`, `token`, `userinfo`), an admin test-login flow, profile page, and the complete password-reset flow (enabled only when email delivery is configured).
- Self-service registration API (`POST /sso/api/register/`), gated by `SSO_OPEN_REGISTRATION` (default closed).
- Provisioning: `create_sso_relying_party`, `create_sso_signing_key`, `import_oidc_manifest`, `ingress_sso_master`.
- Settings read: `SSO_VAULT_PASSWORD` (signing-key unlock), `PLATFORM_DOMAIN` (issuer), `SSO_OPEN_REGISTRATION`.

### sso_client — OIDC consumer

- Model: `OIDCProviderConfig` — the single active upstream provider (portal url, client id/secret, scopes); `SSO_CLIENT_SECRET` env overrides the stored secret.
- Views: `oidc_login` (forwards to the provider's authorize endpoint; falls back to the local login page when no provider is configured), `oidc_callback` (state check, token exchange, userinfo, user provisioning + `people.Person` linking), `oidc_logout`.
- Its urlconf deliberately uses `app_name = "sso"`, mirroring `sso_master`, so `LOGIN_URL = "sso:login"` resolves identically on providers and consumers.
- Provisioning: `export_oidc_manifest`, `ingress_sso_client`.

## Key couplings

- Depends on **`toto-base`** for `toto.core` (login form, cooldown throttle, dashboard redirect), `toto.gervazy` (signing-key encryption), `toto.people` (person linking), `toto.api` (CORS base view) and `toto.ingress`.
- Third-party: `PyJWT` + `cryptography` (ID-token signing and RSA key handling), `requests` (consumer token/userinfo calls).
- Hosts that install the suite piecemeal must install `toto-auth` alongside `toto-base` to keep the historical `BASE_APPS` contract (`toto.sso_core`, `toto.sso_master`) satisfiable.
