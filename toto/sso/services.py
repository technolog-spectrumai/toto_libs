import base64
import hashlib
import time

import jwt
from cryptography.hazmat.primitives.asymmetric import rsa
from django.conf import settings

from toto.core.models import Platform
from .models import SSOSubject


def get_active_platform():
    platform = Platform.objects.filter(active=True).first()
    if not platform:
        raise RuntimeError("No active Platform configured.")
    if not platform.api_keypair_out_id:
        raise RuntimeError("Active Platform has no api_keypair_out RSAKeyPair configured.")
    return platform


def get_issuer(request=None):
    platform = get_active_platform()
    if platform.domain:
        return platform.domain.rstrip("/")
    if request:
        return request.build_absolute_uri("/").rstrip("/")
    raise RuntimeError("Cannot resolve SSO issuer without Platform.domain or request.")


def get_subject_for_user(user):
    subject, _ = SSOSubject.objects.get_or_create(user=user)
    return str(subject.subject)


def base64url_uint(value: int) -> str:
    raw = value.to_bytes((value.bit_length() + 7) // 8, "big")
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def rsa_public_key_to_jwk(keypair):
    public_key = keypair.get_public_key()
    if not isinstance(public_key, rsa.RSAPublicKey):
        raise RuntimeError("SSO signing key must be an RSA public key.")
    numbers = public_key.public_numbers()
    return {
        "kty": "RSA",
        "use": "sig",
        "kid": keypair.key_id,
        "alg": "RS256",
        "n": base64url_uint(numbers.n),
        "e": base64url_uint(numbers.e),
    }


def get_jwks():
    platform = get_active_platform()
    return {"keys": [rsa_public_key_to_jwk(platform.api_keypair_out)]}


def get_user_claims(user, scopes):
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

        # Optional integration with your Person model.
        person = getattr(user, "community_profile", None)
        if person:
            claims["display_name"] = person.display_name
            claims["person_slug"] = person.slug

    return claims


def build_id_token(request, *, user, client, scope, nonce=None, auth_time=None):
    platform = get_active_platform()
    keypair = platform.api_keypair_out
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
        keypair.private_key_pem,
        algorithm="RS256",
        headers={"kid": keypair.key_id, "typ": "JWT"},
    )


def verify_pkce(code_verifier, code_challenge, method):
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
