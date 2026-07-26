# toto-auth

`toto-auth` is the authentication distribution of the **toto** suite: it owns every way a person signs in to a toto host. It ships the three SSO apps that previously lived in `toto-base` — `toto.sso_core`, `toto.sso_master`, `toto.sso_client` — under the shared `toto.*` PEP 420 namespace, unchanged in import path, app label, url names and migrations. It is one of the lockstep-versioned wheels of the suite and depends on `toto-base`.

## What it does (functional)

A toto host picks one authentication posture:

- **Provider** (the historical default) — the host is its own identity authority. Users sign in with username/password on the host, and the host doubles as an OIDC 1.0 provider that other services (Grafana, Gitea, sibling platforms) can federate against.
- **Consumer** — the host delegates sign-in to another toto platform: the login link forwards to the provider's authorize endpoint and the callback provisions/links the local user.
- **Local** — plain username/password sessions with no OIDC surface at all.

## The strategy resolver (`toto.auth_config`)

Mirrors `toto.features`: resolve once in host settings from an env-style
``get`` callable, then feed the frozen `AuthConfig` into the settings the
host used to hardcode.

| knob | values | default | notes |
|---|---|---|---|
| `TOTO_AUTH_MODE` | `local` / `provider` / `consumer` | `provider` | unknown value raises `AuthConfigError` |
| `SSO_OPEN_REGISTRATION` | `0`/`1` | `False` | the `open_registration=`/`default_open_registration=` kwargs force or re-default it |
| `LOGIN_RETRY_COOLDOWN_SECONDS` | int | `3` | login throttle (`toto.core.auth_cooldown`) |
| `CAPTCHA_RETRY_COOLDOWN_SECONDS` | int | `3` | captcha throttle |
| `TOTO_LOGIN_REDIRECT` | url name | `core:dashboard` | post-login destination |
| `TOTO_SOCIAL_SIGNUP` | `0`/`1` | `False` | may an unmatched social sign-in provision a new account |
| `GOOGLE_OAUTH_CLIENT_ID` / `GOOGLE_OAUTH_CLIENT_SECRET` | strings | unset | enables the Google button when both set |
| `FACEBOOK_OAUTH_CLIENT_ID` / `FACEBOOK_OAUTH_CLIENT_SECRET` | strings | unset | enables the Facebook button when both set (Facebook's console calls these App ID / App Secret) |

Host consumption (settings.py):

```python
from toto.auth_config import resolve_auth, login_url, authentication_backends

_A = resolve_auth(os.environ.get)
AUTHENTICATION_BACKENDS = authentication_backends(_A)
LOGIN_URL = reverse_lazy(login_url(_A))
SSO_OPEN_REGISTRATION = _A.open_registration
LOGIN_RETRY_COOLDOWN_SECONDS = _A.login_retry_cooldown_seconds
CAPTCHA_RETRY_COOLDOWN_SECONDS = _A.captcha_retry_cooldown_seconds
```

and in urls.py: `urlpatterns += auth_urlpatterns(_A)`; in INSTALLED_APPS:
`*auth_apps(_A)` (equal to `registry.AUTH_APPS` in provider mode). Every mode
serves the `sso` url namespace — provider via `sso_master.urls`, consumer via
`sso_client.urls`, local via `toto.auth_local_urls` (aliases onto the shared
login in `toto.core`) — so `LOGIN_URL = "sso:login"` and the hard
`{% url 'sso:login' %}`/`{% url 'sso:logout' %}` references in base templates
resolve in all three modes.

## Social login (`toto.social_login`)

"Continue with Google / Facebook" on the unified login page, in every auth
mode. Hand-rolled OAuth 2.0 authorization-code flows (PKCE S256 for Google),
state in a signed cookie, no third-party auth framework. A provider's button
appears exactly when its credentials are configured — env vars first, Django
settings fallback; with no credentials the app is completely inert.

Account resolution at the callback, in order:

1. A `SocialIdentity` (provider + provider-issued subject id) already links a
   local user → sign in.
2. The provider-verified email matches an existing user → link a new
   `SocialIdentity`, sign in (independent of any signup flag).
3. `TOTO_SOCIAL_SIGNUP` is on → provision `<provider>_<sub>`, link, sign in.
4. Otherwise → friendly rejection message back on the login page.

Existing accounts never have their stored email overwritten; unverified or
missing provider emails (Facebook users can deny the email permission) skip
step 2. Register these exact redirect URIs in the provider consoles:
`https://<PLATFORM_DOMAIN>/sso/social/google/callback/` and
`.../facebook/callback/` (dev: `http://localhost:<port>/sso/social/...` —
built from `PLATFORM_DOMAIN` when set, else the request host). Hosts install
it by adding `toto.social_login` to `INSTALLED_APPS` (part of
`registry.AUTH_APPS` / `auth_config.auth_apps` since 1.8).

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
