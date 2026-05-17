# toto.sso — OpenID Connect SSO Provider

`toto.sso` is a standards-compliant [OpenID Connect](https://openid.net/connect/) (OIDC) authorization server built into the toto platform. It lets any external application delegate authentication to your portal — users log in once on your portal and are silently recognized by all connected apps.

## How it works

```
User                  Client App                  toto SSO
 │                        │                           │
 │  click "Login"         │                           │
 │───────────────────────>│                           │
 │                        │  GET /sso/authorize/      │
 │                        │──────────────────────────>│
 │                   redirect to login                │
 │<──────────────────────────────────────────────────│
 │  username + password   │                           │
 │──────────────────────────────────────────────────>│
 │                        │  ?code=<auth_code>        │
 │<──────────────────────────────────────────────────│
 │                        │  POST /sso/token/         │
 │                        │──────────────────────────>│
 │                        │  access_token + id_token  │
 │                        │<──────────────────────────│
 │  logged in             │                           │
 │<───────────────────────│                           │
```

The flow is the standard **Authorization Code** grant. For browser-only (SPA/mobile) apps, PKCE is supported and required.

---

## Endpoints

| Endpoint | URL |
|---|---|
| Discovery | `/.well-known/openid-configuration` |
| Authorization | `/sso/authorize/` |
| Token exchange | `/sso/token/` |
| User info | `/sso/userinfo/` |
| JWKS (public keys) | `/sso/jwks.json` |
| Login page | `/sso/login/` |
| Logout | `/sso/logout/` |

The discovery URL returns all endpoints as JSON — most OIDC client libraries can auto-configure from it alone.

---

## Supported scopes and claims

| Scope | Claims returned |
|---|---|
| `openid` | `iss`, `sub`, `aud`, `exp`, `iat`, `nonce` |
| `email` | `email`, `email_verified` |
| `profile` | `name`, `given_name`, `family_name`, `preferred_username`, `display_name`, `person_slug` |

Minimum required scope: `openid`.

---

## Onboarding a relying party

In OIDC terms, the external application is both a **client** and a **relying party**. In code, `SSORelyingParty` is a proxy model over the existing `SSOClient` registration table, so both names use the same stored credentials.

### What the relying party must provide to you

| Field | Description |
|---|---|
| **Application name** | Human-readable label, e.g. `"Acme HR Portal"` |
| **Redirect URI(s)** | The exact callback URL(s) their app will receive the authorization code at. Must match exactly — no wildcards, no trailing slash differences. |
| **Client type** | `confidential` (server-side app with a secret) or `public` (SPA / mobile app — no secret, uses PKCE) |

### Register the relying party (your side)

Run from the `portal/` directory (env vars must point at your deployment's database):

```bash
# Confidential relying party — server-side app with a secret
python manage.py create_sso_relying_party \
  --name "Acme HR Portal" \
  --redirect-uri "https://acme.example.com/auth/callback"

# Multiple redirect URIs
python manage.py create_sso_relying_party \
  --name "Acme HR Portal" \
  --redirect-uri "https://acme.example.com/auth/callback" \
  --redirect-uri "https://staging.acme.example.com/auth/callback"

# Public relying party — SPA or mobile app (no secret, must use PKCE)
python manage.py create_sso_relying_party \
  --name "Acme Mobile App" \
  --redirect-uri "https://acme.example.com/callback" \
  --public

# Trusted relying party — skip consent screen (internal/first-party apps only)
python manage.py create_sso_relying_party \
  --name "Internal Dashboard" \
  --redirect-uri "https://internal.example.com/callback" \
  --trusted
```

The command prints a `client_id` and (for confidential relying parties) a `client_secret`. **The secret is shown once and hashed immediately — store it now.**

`create_sso_client` is still available as a compatibility alias for older scripts.

### What you hand back to the relying party

| Value | Source |
|---|---|
| `client_id` | Command output |
| `client_secret` | Command output *(confidential only)* |
| Discovery URL | `https://yourportal.com/.well-known/openid-configuration` |

Most OIDC libraries only need the discovery URL plus the client credentials — they fetch all endpoints automatically.

---

## Client integration examples

### Python (using `authlib`)

```python
from authlib.integrations.django_client import OAuth

oauth = OAuth()
oauth.register(
    name="portal",
    server_metadata_url="https://yourportal.com/.well-known/openid-configuration",
    client_id="<client_id>",
    client_secret="<client_secret>",
    client_kwargs={"scope": "openid email profile"},
)
```

### JavaScript (using `openid-client`)

```js
import { Issuer } from 'openid-client';

const issuer = await Issuer.discover('https://yourportal.com/.well-known/openid-configuration');
const client = new issuer.Client({
  client_id: '<client_id>',
  client_secret: '<client_secret>',
  redirect_uris: ['https://myapp.com/callback'],
  response_types: ['code'],
});
```

---

## Signing keys

ID tokens are signed with **RS256** (RSA + SHA-256). The private key is encrypted at rest via `toto.gervazy` (requires `SSO_VAULT_PASSWORD`). The public key is served at `/sso/jwks.json` for token verification.

Generate the signing key once after deployment:

```bash
python manage.py create_sso_signing_key
```

---

## Security notes

- Authorization codes expire in **5 minutes** and are single-use.
- Access tokens expire in **1 hour**.
- Public relying parties **must** use PKCE — confidential relying parties may also use it.
- The consent screen is shown unless the relying party is marked `trusted`.
- Login attempts are rate-limited via `LOGIN_RETRY_COOLDOWN_SECONDS`.
- `sub` claims use a stable UUID (`SSOSubject`) — the user's database PK is never exposed.
