"""Social sign-in: hand-rolled OAuth 2.0 authorization-code flows.

Mirrors the sso_client consumer flow: state in a signed cookie surviving the
provider round-trip, token exchange and userinfo over ``requests``, explicit
ModelBackend login. Account resolution is identity-first (SocialIdentity),
then verified-email match, then — only when social signup is enabled —
provisioning; anything else is a friendly rejection back to the login page.
"""
import base64
import hashlib
import hmac
import logging
import secrets
from urllib.parse import urlencode

import requests as http_requests
from django.contrib import messages
from django.contrib.auth import get_user_model, login
from django.http import Http404, HttpResponseBadRequest
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.http import url_has_allowed_host_and_scheme

from .models import SocialIdentity
from .providers import PROVIDERS, provider_config, public_base_url, social_signup_enabled

logger = logging.getLogger(__name__)
User = get_user_model()

_STATE_COOKIE = "social_state"
_NEXT_COOKIE = "social_next"
_COOKIE_SALT = "social-login"
_COOKIE_MAX_AGE = 300  # 5 minutes — enough to survive the provider round-trip


def _spec_and_config(provider):
    spec = PROVIDERS.get(provider)
    if spec is None:
        raise Http404("Unknown social provider.")
    cfg = provider_config(spec)
    if not cfg["client_id"] or not cfg["client_secret"]:
        raise Http404("Social provider not configured.")
    return spec, cfg


def _safe_next(request, candidate):
    if candidate and url_has_allowed_host_and_scheme(
            candidate, allowed_hosts={request.get_host()},
            require_https=request.is_secure()):
        return candidate
    return ""


def _callback_uri(request, spec):
    path = reverse("sso:social_callback", args=[spec.key])
    base = public_base_url()
    return f"{base}{path}" if base else request.build_absolute_uri(path)


def _pkce_challenge(verifier):
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")


def social_login(request, provider):
    spec, cfg = _spec_and_config(provider)

    state = secrets.token_urlsafe(32)
    verifier = secrets.token_urlsafe(64) if spec.use_pkce else ""
    next_url = _safe_next(request, request.GET.get("next", ""))

    params = {
        "response_type": "code",
        "client_id": cfg["client_id"],
        "redirect_uri": _callback_uri(request, spec),
        "scope": spec.scope,
        "state": state,
    }
    if verifier:
        params["code_challenge"] = _pkce_challenge(verifier)
        params["code_challenge_method"] = "S256"

    joiner = "&" if "?" in spec.authorize_url else "?"
    response = redirect(f"{spec.authorize_url}{joiner}{urlencode(params)}")
    # State (and the PKCE verifier) in a signed cookie — survives the browser
    # round-trip to the provider without depending on the session being saved.
    response.set_signed_cookie(_STATE_COOKIE, f"{spec.key}:{state}:{verifier}",
                               salt=_COOKIE_SALT, max_age=_COOKIE_MAX_AGE,
                               httponly=True, samesite="Lax",
                               secure=request.is_secure())
    if next_url:
        response.set_cookie(_NEXT_COOKIE, next_url, max_age=_COOKIE_MAX_AGE,
                            httponly=True, samesite="Lax")
    return response


def social_callback(request, provider):
    spec, cfg = _spec_and_config(provider)

    if request.GET.get("error"):
        return _reject(request, f"{spec.label} sign-in was cancelled or refused.")

    try:
        stored = request.get_signed_cookie(_STATE_COOKIE, salt=_COOKIE_SALT,
                                           max_age=_COOKIE_MAX_AGE)
        cookie_provider, stored_state, verifier = stored.split(":", 2)
    except Exception:
        cookie_provider = stored_state = verifier = ""

    state = request.GET.get("state", "")
    if (cookie_provider != spec.key or not state
            or not hmac.compare_digest(state, stored_state)):
        return HttpResponseBadRequest("Invalid social login state. Please try again.")

    code = request.GET.get("code")
    if not code:
        return HttpResponseBadRequest("Missing authorization code.")

    token_data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": _callback_uri(request, spec),
        "client_id": cfg["client_id"],
        "client_secret": cfg["client_secret"],
    }
    if verifier:
        token_data["code_verifier"] = verifier

    token_resp = http_requests.post(spec.token_url, data=token_data, timeout=10)
    if not token_resp.ok:
        logger.warning(f"{spec.key} token exchange failed: {token_resp.status_code}")
        return HttpResponseBadRequest("Social login token exchange failed.")

    access_token = token_resp.json().get("access_token")
    userinfo_resp = http_requests.get(
        spec.userinfo_url,
        headers={"Authorization": f"Bearer {access_token}"},
        timeout=10,
    )
    if not userinfo_resp.ok:
        return HttpResponseBadRequest("Social login userinfo fetch failed.")

    claims = spec.normalize(userinfo_resp.json())
    user, error = _resolve_user(spec, claims)
    if error:
        return _reject(request, error)

    login(request, user, backend="django.contrib.auth.backends.ModelBackend")
    logger.info(f"User '{user.username}' logged in via {spec.key}.")

    next_url = _safe_next(request, request.COOKIES.get(_NEXT_COOKIE, ""))
    response = redirect(next_url or reverse("core:dashboard"))
    _delete_cookies(response)
    return response


def _resolve_user(spec, claims):
    """The four-step account resolution; returns (user, "") or (None, error)."""
    rejection = (f"We couldn't match your {spec.label} account to an account "
                 f"on this platform.")

    identity = SocialIdentity.objects.filter(provider=spec.key,
                                             subject=claims["sub"]).select_related("user").first()
    if identity:
        if not identity.user.is_active:
            return None, rejection
        if identity.email != claims["email"]:
            identity.email = claims["email"]
        identity.save(update_fields=["email", "last_used_at"])
        return identity.user, ""

    user = None
    if claims["email"] and claims["email_verified"]:
        user = User.objects.filter(email__iexact=claims["email"]).first()

    if user is None:
        if not social_signup_enabled():
            return None, rejection
        user, _ = User.objects.get_or_create(
            username=f"{spec.key}_{claims['sub']}",
            defaults={"email": claims["email"],
                      "first_name": claims["given_name"],
                      "last_name": claims["family_name"]},
        )
    else:
        # Existing account: fill blank names, never touch the stored email.
        changed = []
        for field, key in [("first_name", "given_name"), ("last_name", "family_name")]:
            if not getattr(user, field) and claims[key]:
                setattr(user, field, claims[key])
                changed.append(field)
        if changed:
            user.save(update_fields=changed)

    if not user.is_active:
        return None, rejection

    SocialIdentity.objects.get_or_create(provider=spec.key, subject=claims["sub"],
                                         defaults={"user": user, "email": claims["email"]})
    return user, ""


def _reject(request, message):
    messages.error(request, message)
    response = redirect(reverse("sso:login"))
    _delete_cookies(response)
    return response


def _delete_cookies(response):
    response.delete_cookie(_STATE_COOKIE)
    response.delete_cookie(_NEXT_COOKIE)
