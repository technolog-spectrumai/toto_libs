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
    platform = get_active_platform()
    if platform.domain:
        return platform.domain.rstrip("/")
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
    password = getattr(settings, "SSO_VAULT_PASSWORD", "").strip()
    if password:
        return password
    # Dev fallback: read from run/sso_*.json bundle written by portal reset.
    import json
    from toto.conf import run_dir as _run_dir
    run_dir = _run_dir()
    for bundle_path in run_dir.glob("sso_*.json"):
        try:
            vp = json.loads(bundle_path.read_text()).get("vault_password", "")
            if vp:
                return vp
        except Exception:
            pass
    raise RuntimeError(
        "SSO_VAULT_PASSWORD is not set. "
        "Configure this environment variable with the SSO system vault password."
    )


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
        if user.is_superuser:
            claims["roles"] = ["admin", "staff"]
        elif user.is_staff:
            claims["roles"] = ["staff"]
        else:
            claims["roles"] = ["viewer"]
        claims["is_superuser"] = bool(user.is_superuser)

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
    if not code_challenge:
        return True
    if not code_verifier:
        return False
    if method == "S256":
        digest = hashlib.sha256(code_verifier.encode("ascii")).digest()
        computed = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        return computed == code_challenge
    if method in ("plain", "", None):
        return code_verifier == code_challenge
    return False
