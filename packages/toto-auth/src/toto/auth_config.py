"""Effective authentication strategy for toto deployments.

Mirrors toto.features: resolve once in host settings from an env-style
``get`` callable, then feed the frozen result into INSTALLED_APPS, the url
tree, LOGIN_URL and AUTHENTICATION_BACKENDS.  Every mode keeps the ``sso``
url namespace alive (base templates hard-reverse ``sso:login``/``sso:logout``),
so ``LOGIN_URL = "sso:login"`` holds on providers, consumers and local hosts
alike.
"""
from dataclasses import dataclass

from toto.features import flag

MODE_LOCAL = "local"        # plain username/password sessions, no OIDC surface
MODE_PROVIDER = "provider"  # own identity authority + OIDC provider (historical default)
MODE_CONSUMER = "consumer"  # delegate sign-in to another toto platform via OIDC

MODES = (MODE_LOCAL, MODE_PROVIDER, MODE_CONSUMER)


class AuthConfigError(ValueError):
    """The requested auth configuration is contradictory or unknown."""


@dataclass(frozen=True)
class AuthConfig:
    mode: str                            # one of MODES (TOTO_AUTH_MODE)
    open_registration: bool              # SSO_OPEN_REGISTRATION — self-service signup API
    social_signup: bool                  # TOTO_SOCIAL_SIGNUP — unmatched social sign-ins may provision
    login_retry_cooldown_seconds: int    # LOGIN_RETRY_COOLDOWN_SECONDS
    captcha_retry_cooldown_seconds: int  # CAPTCHA_RETRY_COOLDOWN_SECONDS
    login_redirect: str                  # TOTO_LOGIN_REDIRECT — url name after login


def _int(get, name, default):
    raw = get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def resolve_auth(get, *, open_registration=None, default_open_registration=False) -> AuthConfig:
    """Resolve the host's login strategy from an env-style accessor.

    ``get`` is a name -> raw-or-None accessor (``os.environ.get`` in host
    settings, a config dict's ``.get`` in deploy tooling).  ``open_registration``
    forces registration open or closed regardless of the environment; leave it
    ``None`` to let the SSO_OPEN_REGISTRATION flag decide, with
    ``default_open_registration`` as the fallback when the flag is unset.
    """
    mode = get("TOTO_AUTH_MODE") or MODE_PROVIDER
    if mode not in MODES:
        raise AuthConfigError(f"TOTO_AUTH_MODE={mode!r} is not one of {'/'.join(MODES)}")
    if open_registration is None:
        open_registration = flag(get, "SSO_OPEN_REGISTRATION", default_open_registration)
    return AuthConfig(
        mode=mode,
        open_registration=bool(open_registration),
        social_signup=flag(get, "TOTO_SOCIAL_SIGNUP", False),
        login_retry_cooldown_seconds=_int(get, "LOGIN_RETRY_COOLDOWN_SECONDS", 3),
        captcha_retry_cooldown_seconds=_int(get, "CAPTCHA_RETRY_COOLDOWN_SECONDS", 3),
        login_redirect=get("TOTO_LOGIN_REDIRECT") or "core:dashboard",
    )


def auth_apps(cfg: AuthConfig) -> list:
    """The auth apps the host adds to INSTALLED_APPS for its mode.

    toto.social_login rides along in every mode: it is inert until a
    provider's OAuth credentials are configured.
    """
    if cfg.mode == MODE_PROVIDER:
        return ["toto.sso_core", "toto.sso_master", "toto.social_login"]
    if cfg.mode == MODE_CONSUMER:
        return ["toto.sso_core", "toto.sso_client", "toto.social_login"]
    return ["toto.social_login"]


def auth_urlpatterns(cfg: AuthConfig) -> list:
    """The url mounts for the host's mode; every mode serves the ``sso`` namespace."""
    from django.urls import include, path

    if cfg.mode == MODE_PROVIDER:
        # sso_master.urls carries its own sso/ and .well-known/ prefixes.
        return [path("", include("toto.sso_master.urls"))]
    if cfg.mode == MODE_CONSUMER:
        return [path("sso/", include("toto.sso_client.urls"))]
    return [path("sso/", include("toto.auth_local_urls"))]


def login_url(cfg: AuthConfig) -> str:
    """The LOGIN_URL url name (hosts wrap it in reverse_lazy)."""
    return "sso:login"


def authentication_backends(cfg: AuthConfig) -> list:
    """AUTHENTICATION_BACKENDS for the mode (the consumer callback names its
    backend explicitly, so ModelBackend serves every current mode)."""
    return ["django.contrib.auth.backends.ModelBackend"]
