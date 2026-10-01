import base64
import hashlib
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_public_key
from django.conf import settings

from toto.core.models import Platform
from toto.gervazy.crypto import GervazyCryptoSession
from .models import SSOSigningKey, SSOSubject


# ---------------------------------------------------------------------------
# Platform / issuer
# ---------------------------------------------------------------------------

def get_active_platform():
    platform = Platform.objects.filter(active=True).first()
    if not platform:
        raise RuntimeError("No active Platform configured.")
    return platform


def get_issuer(request=None) -> str:
    """The OIDC issuer — always a URL, never a bare hostname.

    `Platform.domain` is stored however an operator typed it, and on a local
    stack that is usually `localhost`. Returning it verbatim advertised
    `"issuer": "localhost"` in the discovery document while every endpoint
    beside it was `https://localhost/...`, and put the same bare string in
    every ID token's `iss` claim.

    That is not a cosmetic mismatch. OIDC requires the issuer to be a URL, and
    a relying party validates the `iss` it receives against the issuer it
    discovered; a strict client refuses the token and the sign-in dies at the
    callback with nothing useful on screen. Gitea lands such a failure on
    /user/link_account, which is indistinguishable from a missing username.

    `get_public_base_url` below has always normalised the scheme this way.
    The two read the same field and must agree, so this now does what that
    one does.
    """
    platform = get_active_platform()
    if platform.domain:
        domain = platform.domain.rstrip("/")
        if domain.startswith("http://") or domain.startswith("https://"):
            return domain
        return f"https://{domain}"
    if request:
        return request.build_absolute_uri("/").rstrip("/")
    raise RuntimeError("Cannot resolve SSO issuer without Platform.domain or request.")


def get_public_base_url() -> str:
    """Browser-facing base URL built from settings.PLATFORM_DOMAIN ("" when unset).
    Used to build OIDC redirect URIs and the browser-facing authorization endpoint
    advertised by the internal discovery document."""
    domain = (getattr(settings, "PLATFORM_DOMAIN", "") or "").strip().rstrip("/")
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


# ---------------------------------------------------------------------------
# Signing key helpers
# ---------------------------------------------------------------------------

def get_active_signing_key() -> SSOSigningKey:
    key = SSOSigningKey.objects.filter(is_active=True).select_related("encrypted_key").first()
    if not key:
        raise RuntimeError(
            "No active SSOSigningKey found. "
            "Run: python manage.py create_sso_signing_key --key-id <id>"
        )
    return key


def _load_vault_password() -> str:
    """The SSO vault passphrase.

    The implementation moved to ``sso_core.vault`` so a CONSUMER host can use it
    too — ``sso_master`` is not installed there, and the consumer needs the same
    strongbox for its client secret. This stays as the name the rest of this
    module calls.
    """
    from toto.sso_core.vault import load_vault_password

    return load_vault_password()


def _open_sso_vault(epk) -> GervazyCryptoSession:
    """Open a GervazyCryptoSession for the strongbox that owns *epk*."""
    return GervazyCryptoSession(epk.strongbox, _load_vault_password())


def get_signing_private_key_pem() -> str:
    """
    Decrypt and return the active SSO signing private key PEM.

    Opens a GervazyCryptoSession with the SSO vault password on every call.
    The decrypted key material lives only in memory and is never persisted.
    """
    signing_key = get_active_signing_key()
    if not signing_key.encrypted_key_id:
        raise RuntimeError(
            f"SSOSigningKey {signing_key.key_id!r} has no linked EncryptedPrivateKey. "
            "Re-run create_sso_signing_key."
        )
    epk = signing_key.encrypted_key
    session = _open_sso_vault(epk)
    return session.decrypt_private_key(epk)


# ---------------------------------------------------------------------------
# JWKS
# ---------------------------------------------------------------------------

def base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def _signing_key_to_jwk(signing_key: SSOSigningKey) -> dict:
    public_key = load_pem_public_key(signing_key.public_key_pem.encode())
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise RuntimeError("SSO signing key must be an RSA public key.")
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "kid": signing_key.key_id,
        "alg": signing_key.algorithm,
        "n": base64url_uint(numbers.n),
        "e": base64url_uint(numbers.e),
    }


def get_jwks() -> dict:
    keys = SSOSigningKey.objects.filter(is_active=True)
    return {"keys": [_signing_key_to_jwk(k) for k in keys]}


# ---------------------------------------------------------------------------
# User claims / subjects
# ---------------------------------------------------------------------------

def get_subject_for_user(user) -> str:
    subject, _ = SSOSubject.objects.get_or_create(user=user)
    return str(subject.subject)


def user_roles(user) -> list[str]:
    """The `roles` claim: what somebody is on THIS platform.

    One function, because two places must agree on it: the claim a relying
    party reads, and the allow-list `/authorize` checks before it issues a
    code (`may_sign_in_to`)."""
    if user.is_superuser:
        return ["admin", "staff"]
    if user.is_staff:
        return ["staff"]
    return ["viewer"]


#: Relying parties only some accounts may sign in to, by client_id, and the
#: role an account needs (any one of those listed). Overridden whole by the
#: SSO_CLIENT_REQUIRED_ROLES setting.
#:
#: Gitea is staff-only, and until 2026-10-01 only Gitea's side said so (its
#: OAuth source requires `roles=staff`): this provider signed anybody in and
#: handed Gitea a code for an account Gitea would then refuse — after the
#: redirect, so the account's claims had already left. With Gitea moved to a
#: machine of its own (zenobia's nabu) the refusal belongs here too, before
#: any redirect: a forge on another machine is a relying party this provider
#: should not have to trust to refuse.
DEFAULT_CLIENT_REQUIRED_ROLES = {"gitea": ("staff",)}


def client_required_roles(client_id: str) -> tuple[str, ...]:
    """The roles one of which an account needs to sign in to this client;
    empty when anybody may."""
    from django.conf import settings

    table = getattr(settings, "SSO_CLIENT_REQUIRED_ROLES", None)
    if table is None:
        table = DEFAULT_CLIENT_REQUIRED_ROLES
    return tuple(table.get(client_id) or ())


def may_sign_in_to(user, client_id: str) -> bool:
    """Whether this account may be issued a code for this relying party."""
    required = client_required_roles(client_id)
    if not required:
        return True
    return bool(set(required) & set(user_roles(user)))


def get_user_claims(user, scopes) -> dict:
    scopes = set(scopes)
    claims = {"sub": get_subject_for_user(user)}

    if "email" in scopes:
        claims["email"] = user.email or ""
        claims["email_verified"] = bool(user.email)

    if "profile" in scopes:
        claims["name"] = user.get_full_name() or user.username
        claims["preferred_username"] = user.username
        claims["given_name"] = user.first_name or ""
        claims["family_name"] = user.last_name or ""

        person = getattr(user, "community_profile", None)
        if person:
            claims["display_name"] = person.display_name
            claims["person_slug"] = person.slug

    # Role claim for relying parties that map local roles. Emitted only for
    # clients that request the non-standard `roles` scope. Two consumers:
    #   - Grafana gates its Admin role on `contains(roles[*], 'admin')` with
    #     strict mapping, so anyone without `admin` is denied entirely.
    #   - Gitea requires `staff` in roles to log in at all (staff + superusers)
    #     and maps `admin` → instance admin (see provision_oauth.sh).
    if "roles" in scopes:
        claims["roles"] = user_roles(user)
        claims["is_superuser"] = bool(user.is_superuser)
        # A federated toto host mirrors this onto its own account, so that
        # disabling someone here disables them there. It is effectively always
        # true at /authorize (an inactive user cannot sign in to reach it), but
        # an access token outlives the session by up to an hour, so /userinfo
        # can legitimately be asked about a user disabled since.
        claims["is_active"] = bool(user.is_active)

    # `groups` — Django group membership, verbatim.
    #
    # A SEPARATE SCOPE from `roles`, because they answer different questions.
    # `roles` is derived from is_staff/is_superuser and says what somebody is
    # on THIS platform; `groups` says which rooms they have been let into, and
    # is edited by an administrator without touching anybody's staff flag.
    # A relying party that wants one rarely wants the other.
    #
    # This is the access model for Wekan and lakeFS, and it exists because
    # there are NO external accounts: every account belongs to somebody here,
    # so "who may reach the boards" is a membership question rather than an
    # account-type question. Remove a person from a group and their next token
    # carries the change.
    #
    # Local by design. `datalink_policies` refuses to federate auth.Group at
    # all — "group membership is a local authorization decision" — so a
    # federated host never inherits who may see this company's boards, which
    # is exactly right.
    if "groups" in scopes:
        claims["groups"] = sorted(
            user.groups.values_list("name", flat=True))
        # Superusers administer every relying party that maps a group to an
        # admin role (Wekan's OAUTH2_ADMIN_GROUPS, Grafana's admin-group).
        # Stated here rather than by seeding a real group, so the mapping
        # cannot drift from the `roles` claim above, which already says
        # "admin" for the same people.
        if user.is_superuser and "admin" not in claims["groups"]:
            claims["groups"].append("admin")

    return claims


# ---------------------------------------------------------------------------
# ID token
# ---------------------------------------------------------------------------

def build_id_token(request, *, user, client, scope, nonce=None, auth_time=None) -> str:
    """Sign and return an OIDC ID token JWT using the active Gervazy signing key."""
    signing_key = get_active_signing_key()
    private_key_pem = get_signing_private_key_pem()
    issuer = get_issuer(request)
    now = int(time.time())

    claims = {
        "iss": issuer,
        "sub": get_subject_for_user(user),
        "aud": client.client_id,
        "iat": now,
        "exp": now + 3600,
        "auth_time": auth_time or now,
    }
    if nonce:
        claims["nonce"] = nonce
    claims.update(get_user_claims(user, scope.split()))

    return jwt.encode(
        claims,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": signing_key.key_id, "typ": "JWT"},
    )


# ---------------------------------------------------------------------------
# PKCE
# ---------------------------------------------------------------------------

def verify_pkce(code_verifier, code_challenge, method) -> bool:
    """Check a PKCE verifier against the stored challenge.

    ``plain`` is refused. For a public client the challenge travels in the query
    string of the /authorize request, so a "verifier" that equals the challenge is
    known to anyone who saw that URL — a browser history, a Referer header, a proxy
    log — and the exchange is protected by nothing at all. RFC 7636 wants S256 for
    exactly this reason, and it is advertised as the only supported method.

    A challenge sent with **no** method is treated as S256 rather than defaulting
    to plain: the OIDC default is plain, which means a client that simply omitted
    the parameter used to get the useless variant silently. Confidential clients
    are unaffected either way — they authenticate with a secret and send no
    challenge at all.
    """
    if not code_challenge:
        return True
    if not code_verifier:
        return False
    if method in ("S256", "", None):
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return computed == code_challenge
    return False