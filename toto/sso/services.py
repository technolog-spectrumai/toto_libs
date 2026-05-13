import base64
import hashlib
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.hazmat.primitives.serialization import load_pem_private_key, load_pem_public_key
from django.conf import settings

from toto.core.models import Platform
from .models import SSOSigningKey, SSOSubject


def get_active_platform():
    platform = Platform.objects.filter(active=True).first()
    if not platform:
        raise RuntimeError("No active Platform configured.")
    return platform


def get_issuer(request=None):
    platform = get_active_platform()
    if platform.domain:
        return platform.domain.rstrip("/")
    if request:
        return request.build_absolute_uri("/").rstrip("/")
    raise RuntimeError("Cannot resolve SSO issuer without Platform.domain or request.")


def get_active_signing_key():
    """Return the active SSOSigningKey DB record (holds public key + key_id)."""
    key = SSOSigningKey.objects.filter(is_active=True).first()
    if not key:
        raise RuntimeError(
            "No active SSOSigningKey found. "
            "Run: python manage.py create_sso_signing_key --key-id <id>"
        )
    return key


def _get_private_key_pem():
    """Load the RSA private key PEM from settings (set via SSO_SIGNING_PRIVATE_KEY env var)."""
    pem = getattr(settings, "SSO_SIGNING_PRIVATE_KEY", "").strip()
    if not pem:
        raise RuntimeError(
            "SSO_SIGNING_PRIVATE_KEY is not set. "
            "Set this environment variable to the PEM-encoded RSA private key."
        )
    return pem


def get_subject_for_user(user):
    subject, _ = SSOSubject.objects.get_or_create(user=user)
    return str(subject.subject)


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
    """Return the JWKS JSON dict containing all active public signing keys."""
    keys = SSOSigningKey.objects.filter(is_active=True)
    return {"keys": [_signing_key_to_jwk(k) for k in keys]}


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

    return claims


def build_id_token(request, *, user, client, scope, nonce=None, auth_time=None) -> str:
    """Sign and return an OIDC ID token JWT."""
    signing_key = get_active_signing_key()
    private_key_pem = _get_private_key_pem()
    issuer = get_issuer(request)
    now = int(time.time())
    scopes = scope.split()

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

    claims.update(get_user_claims(user, scopes))

    return jwt.encode(
        claims,
        private_key_pem,
        algorithm="RS256",
        headers={"kid": signing_key.key_id, "typ": "JWT"},
    )


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
