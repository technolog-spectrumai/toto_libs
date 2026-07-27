"""Social provider registry and request-time configuration.

The single source of truth for which social providers exist and which are
enabled on this host. A provider is enabled exactly when both of its OAuth
credentials are set — env vars first, Django settings as fallback (the
SSO_CLIENT_SECRET precedent): ``GOOGLE_OAUTH_CLIENT_ID`` /
``GOOGLE_OAUTH_CLIENT_SECRET``, ``FACEBOOK_OAUTH_CLIENT_ID`` /
``FACEBOOK_OAUTH_CLIENT_SECRET`` (Facebook's console calls these "App ID"
and "App Secret").
"""
import os
from dataclasses import dataclass
from typing import Callable
from urllib.parse import urlencode

from django.conf import settings
from django.urls import reverse

from toto.features import flag


@dataclass(frozen=True)
class ProviderSpec:
    key: str
    label: str
    icon: str            # font-awesome class for the login button
    authorize_url: str
    token_url: str
    userinfo_url: str
    scope: str
    use_pkce: bool
    normalize: Callable  # raw userinfo dict -> {sub, email, email_verified, given_name, family_name}


def _normalize_google(data):
    return {
        "sub": str(data["sub"]),
        "email": data.get("email", "") or "",
        "email_verified": bool(data.get("email_verified")),
        "given_name": data.get("given_name", "") or "",
        "family_name": data.get("family_name", "") or "",
    }


def _normalize_facebook(data):
    email = data.get("email", "") or ""
    return {
        "sub": str(data["id"]),
        "email": email,
        # The Graph API only ever returns confirmed emails (and none at all
        # when the user denied the email permission).
        "email_verified": bool(email),
        "given_name": data.get("first_name", "") or "",
        "family_name": data.get("last_name", "") or "",
    }


PROVIDERS = {
    "google": ProviderSpec(
        key="google",
        label="Google",
        icon="fa-brands fa-google",
        authorize_url="https://accounts.google.com/o/oauth2/v2/auth",
        token_url="https://oauth2.googleapis.com/token",
        userinfo_url="https://openidconnect.googleapis.com/v1/userinfo",
        scope="openid email profile",
        use_pkce=True,
        normalize=_normalize_google,
    ),
    "facebook": ProviderSpec(
        key="facebook",
        label="Facebook",
        icon="fa-brands fa-facebook",
        authorize_url="https://www.facebook.com/v19.0/dialog/oauth",
        token_url="https://graph.facebook.com/v19.0/oauth/access_token",
        userinfo_url="https://graph.facebook.com/v19.0/me?fields=id,first_name,last_name,email",
        scope="email public_profile",
        use_pkce=False,
        normalize=_normalize_facebook,
    ),
}


def _credential(name):
    return os.environ.get(name) or getattr(settings, name, "") or ""


def provider_config(spec):
    return {
        "client_id": _credential(f"{spec.key.upper()}_OAUTH_CLIENT_ID"),
        "client_secret": _credential(f"{spec.key.upper()}_OAUTH_CLIENT_SECRET"),
    }


def enabled_providers():
    enabled = []
    for spec in PROVIDERS.values():
        cfg = provider_config(spec)
        if cfg["client_id"] and cfg["client_secret"]:
            enabled.append(spec)
    return enabled


def social_signup_enabled():
    """May an unmatched social sign-in provision a new account?

    An explicit ``TOTO_SOCIAL_SIGNUP`` Django setting wins (strategy-adopting
    hosts assign it from resolve_auth); otherwise the env flag decides,
    default closed.
    """
    explicit = getattr(settings, "TOTO_SOCIAL_SIGNUP", None)
    if explicit is not None:
        return bool(explicit)
    return flag(os.environ.get, "TOTO_SOCIAL_SIGNUP", False)


def public_base_url():
    # Local copy of toto.sso_master.services.get_public_base_url — that module
    # imports sso_master models, which consumer/local hosts don't install.
    domain = (getattr(settings, "PLATFORM_DOMAIN", "") or "").strip().rstrip("/")
    if not domain:
        return ""
    if domain.startswith("http://") or domain.startswith("https://"):
        return domain
    return f"https://{domain}"


def login_page_providers(request):
    """Button data for the login page: [{key, label, icon, login_url}]."""
    next_url = request.GET.get("next", "")
    entries = []
    for spec in enabled_providers():
        url = reverse("sso:social_login", args=[spec.key])
        if next_url:
            url = f"{url}?{urlencode({'next': next_url})}"
        entries.append({"key": spec.key, "label": spec.label,
                        "icon": spec.icon, "login_url": url})
    return entries
